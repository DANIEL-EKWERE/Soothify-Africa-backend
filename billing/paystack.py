"""Talking to Paystack.

The only place in the project that knows Paystack exists, works in kobo, or
speaks HTTP to anyone about money. Everything else deals in naira and in the
models.

Two directions:

    out   initialise a transaction (the app opens the returned URL), charge a
          saved card when a trial ends, and send an expert their payout.
    in    the webhook, which is the only thing we trust to say a payment
          succeeded -- a client telling us it paid is a claim, not a fact.
"""

import hashlib
import hmac
import json
import logging
import uuid
from urllib import error, parse, request

from django.conf import settings

logger = logging.getLogger(__name__)

BASE_URL = 'https://api.paystack.co'
TIMEOUT = 20


class PaystackError(RuntimeError):
    """Paystack said no, or could not be reached."""


def configured():
    return bool(settings.PAYSTACK_SECRET_KEY)


def reference(prefix='sfy'):
    """Our own id for a transaction. Unique, and not guessable in order."""
    return '{0}_{1}'.format(prefix, uuid.uuid4().hex[:20])


def to_kobo(naira):
    return int(naira) * 100


def initialize(email, naira, reference_, callback_url=None, metadata=None):
    """Start a payment. Returns the checkout URL for the app to open."""
    payload = {
        'email': email,
        'amount': to_kobo(naira),
        'reference': reference_,
        'currency': 'NGN',
    }
    if callback_url:
        payload['callback_url'] = callback_url
    if metadata:
        payload['metadata'] = metadata
    data = _post('/transaction/initialize', payload)
    return {
        'authorization_url': data.get('authorization_url', ''),
        'access_code': data.get('access_code', ''),
        'reference': data.get('reference', reference_),
    }


def verify(reference_):
    """What Paystack says happened. Used by the webhook and by polling."""
    return _get('/transaction/verify/{0}'.format(parse.quote(reference_)))


def charge_authorization(email, naira, authorization_code, reference_, metadata=None):
    """Charge a card we already hold an authorization for.

    This is what ends a trial: no checkout page, because the member already
    agreed when they started it.
    """
    payload = {
        'email': email,
        'amount': to_kobo(naira),
        'authorization_code': authorization_code,
        'reference': reference_,
        'currency': 'NGN',
    }
    if metadata:
        payload['metadata'] = metadata
    return _post('/transaction/charge_authorization', payload)


def create_transfer_recipient(name, account_number, bank_code):
    """Register where an expert is paid. Returns the recipient code.

    The account number goes to Paystack and is not stored here -- only the
    code, and the last four digits for the screen.
    """
    return _post('/transferrecipient', {
        'type': 'nuban',
        'name': name,
        'account_number': account_number,
        'bank_code': bank_code,
        'currency': 'NGN',
    })


def transfer(naira, recipient_code, reason, reference_):
    """Send an expert their money."""
    return _post('/transfer', {
        'source': 'balance',
        'amount': to_kobo(naira),
        'recipient': recipient_code,
        'reason': reason,
        'reference': reference_,
    })


def signature_is_valid(body, signature):
    """Whether a webhook really came from Paystack.

    HMAC-SHA512 of the raw body with the secret key. Compared in constant
    time: a timing-sensitive comparison on a signature is a real attack, not
    a theoretical one, and the fix costs nothing.
    """
    if not signature or not settings.PAYSTACK_SECRET_KEY:
        return False
    expected = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode('utf-8'), body, hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _post(path, payload):
    return _call('POST', path, payload)


def _get(path):
    return _call('GET', path, None)


def _call(method, path, payload):
    if not configured():
        raise PaystackError('PAYSTACK_SECRET_KEY is not set.')

    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = request.Request(BASE_URL + path, data=body, method=method)
    req.add_header('Authorization', 'Bearer {0}'.format(settings.PAYSTACK_SECRET_KEY))
    req.add_header('Content-Type', 'application/json')

    try:
        with request.urlopen(req, timeout=TIMEOUT) as response:
            parsed = json.loads(response.read().decode('utf-8'))
    except error.HTTPError as http_error:
        detail = http_error.read().decode('utf-8', 'replace')[:400]
        logger.warning('paystack %s %s failed: %s', method, path, detail)
        raise PaystackError(_message(detail) or 'Paystack refused the request.')
    except (error.URLError, ValueError, OSError) as failure:
        logger.warning('paystack %s %s unreachable: %s', method, path, failure)
        raise PaystackError('Could not reach Paystack. Try again in a moment.')

    if not parsed.get('status'):
        raise PaystackError(parsed.get('message') or 'Paystack refused the request.')
    return parsed.get('data') or {}


def _message(raw):
    try:
        return json.loads(raw).get('message', '')
    except ValueError:
        return ''
