"""Talking to Bachs.

The only place that knows Bachs exists or speaks HTTP about money. Everything
else deals in whole naira and in the models.

Two things differ from a plain gateway, and both simplify what we have to do:

    trials        a recurring product carries `trial_period`. The card is
                  saved at checkout and not charged; Bachs bills the first
                  cycle itself when the trial ends and sends `invoice.paid`.
                  So there is no day-8 job here, and no card-check charge.
    payouts       two calls -- a destination, then the payout -- and Bachs
                  reports delivery by webhook.

Amounts cross this boundary as decimal strings ("25000.00"), never integers:
their API takes the amount as written, so sending 2500000 for kobo would
charge a hundred times the price.
"""

import hashlib
import hmac
import json
import logging
import time
import uuid
from decimal import Decimal
from urllib import error, parse, request

from django.conf import settings

logger = logging.getLogger(__name__)

# How we identify ourselves to their API. See the User-Agent note in _call.
USER_AGENT = 'SoothifyAfrica/1.0 (+https://soothifyafrica.com.ng)'

SANDBOX_URL = 'https://sandbox-api.bachs.io'
LIVE_URL = 'https://api.bachs.io'
TIMEOUT = 20

# How far out of step a webhook's timestamp may be before it is treated as a
# replay. Their documented default.
SIGNATURE_TOLERANCE = 300


class BachsError(RuntimeError):
    """Bachs said no, or could not be reached."""


def configured():
    return bool(settings.BACHS_SECRET_KEY)


def base_url():
    """Sandbox or live, chosen by the key itself.

    A live key against the sandbox host silently does nothing useful, and a
    separate setting is one more thing to get wrong on deployment day.
    """
    if settings.BACHS_SECRET_KEY.startswith('sk_live_'):
        return LIVE_URL
    return SANDBOX_URL


def reference(prefix='sfy'):
    """Our own id for a charge. Unique per organisation, as they require."""
    return '{0}_{1}'.format(prefix, uuid.uuid4().hex[:20])


def money(naira):
    """Whole naira to the decimal string their API expects."""
    return str(Decimal(int(naira)).quantize(Decimal('0.01')))


def from_money(amount):
    """Their decimal string back to whole naira."""
    try:
        return int(Decimal(str(amount)))
    except (TypeError, ValueError, ArithmeticError):
        return 0


# ------------------------------------------------------------------ products

def create_product(name, naira, interval=None, frequency=1, trial_days=0):
    """A thing that can be bought.

    With an interval it recurs, and `trial_days` gives it a free trial --
    which is the whole subscription mechanism: a checkout for a product with
    a trial period starts one.
    """
    payload = {
        'name': name,
        'price': {
            'price_type': 'fixed',
            'amount': money(naira),
            'currency': 'NGN',
        },
    }
    if interval:
        payload['billing_cycle'] = {'interval': interval, 'frequency': frequency}
        if trial_days:
            payload['trial_period'] = {'interval': 'day', 'frequency': trial_days}
    return _post('/v1/products', payload)


# ------------------------------------------------------------------ checkout

def checkout_for_product(product_id, email, reference_, success_url,
                         cancel_url='', metadata=None, name=''):
    """Buy a product -- a membership, with or without a trial."""
    customer = {'email': email}
    if name:
        customer['name'] = name

    return _post('/v1/checkout-sessions', {
        'product_cart': [{'product_id': product_id, 'quantity': 1}],
        # Required for a recurring product, and it is how the subscription
        # finds its way back to one of our accounts.
        'customer': customer,
        'success_url': success_url,
        'cancel_url': cancel_url or success_url,
        'reference': reference_,
        'metadata': metadata or {},
    })


def checkout_for_amount(naira, email, reference_, success_url,
                        cancel_url='', metadata=None):
    """Buy something that is not a product -- one booked session.

    Ad-hoc pricing, so we are not creating a product per session.
    """
    return _post('/v1/checkout-sessions', {
        'pricing': {'amount': money(naira), 'currency': 'NGN'},
        'customer': {'email': email},
        'success_url': success_url,
        'cancel_url': cancel_url or success_url,
        'reference': reference_,
        'metadata': metadata or {},
    })


def get_checkout(checkout_id):
    return _get('/v1/checkout-sessions/{0}'.format(parse.quote(checkout_id)))


# ------------------------------------------------------------- subscriptions

def get_subscription(subscription_id):
    return _get('/v1/subscriptions/{0}'.format(parse.quote(subscription_id)))


def cancel_subscription(subscription_id):
    """Stop it renewing. Access runs to the end of the paid period."""
    return _delete('/v1/subscriptions/{0}'.format(parse.quote(subscription_id)))


def end_trial_now(subscription_id):
    """Charge a trial immediately -- "start my membership now".

    Their note: a trial change must be sent on its own, not combined with
    other changes.
    """
    return _patch(
        '/v1/subscriptions/{0}'.format(parse.quote(subscription_id)),
        {'trial_end': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())},
    )


# ------------------------------------------------------------------- payouts

def list_banks(country='NG'):
    """Bank codes for a payout destination."""
    return _get('/v1/reference/banks?country={0}'.format(country))


def resolve_account(account_number, bank_code):
    """Whose account this is, before anyone is paid."""
    return _post('/v1/misc/bank-accounts/resolve', {
        'account_number': account_number,
        'bank_code': bank_code,
    })


def create_payout_destination(bank_code, account_number, currency='NGN'):
    """Where an expert is paid. Returns a pd_… id.

    The account number goes to Bachs and is not stored here; we keep the id
    and the last four digits for the screen.
    """
    return _post('/v1/payouts/destinations', {
        'type': 'bank_account',
        'currency': currency,
        'bank_code': bank_code,
        'account_number': account_number,
    })


def get_payout_destination(destination_id):
    return _get('/v1/payouts/destinations/{0}'.format(parse.quote(destination_id)))


def create_payout(destination_id, naira, reference_):
    """Send an expert their money.

    Idempotency-Key is the reference: a retry after a timeout must not pay
    somebody twice, and a timeout is exactly when a retry happens.
    """
    return _post(
        '/v1/payouts',
        {
            'destination': destination_id,
            'amount': money(naira),
            'reference': reference_,
        },
        idempotency_key=reference_,
    )


# ------------------------------------------------------------------ webhooks

def signature_is_valid(body, header, secret=None, now=None):
    """Whether a webhook really came from Bachs.

    HMAC-SHA256 over "{timestamp}.{raw body}", read from X-Bachs-Signature-V2
    ("t=…,v1=…"). Several v1 values can appear while a secret is rotating, so
    any match counts. Compared in constant time, and checked against the
    tolerance so a captured delivery cannot be replayed tomorrow.

    The body must be the raw bytes: parsing and re-serialising JSON changes
    them and the signature no longer matches.
    """
    secret = secret or settings.BACHS_WEBHOOK_SECRET
    if not secret or not header:
        return False

    pairs = [part.strip().split('=', 1) for part in header.split(',') if '=' in part]
    timestamp = next((value for key, value in pairs if key == 't'), None)
    signatures = [value for key, value in pairs if key == 'v1']
    if timestamp is None or not signatures:
        return False

    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now or time.time()) - sent_at) > SIGNATURE_TOLERANCE:
        return False

    expected = hmac.new(
        secret.encode('utf-8'),
        '{0}.'.format(sent_at).encode('utf-8') + body,
        hashlib.sha256,
    ).hexdigest()
    return any(hmac.compare_digest(expected, sent) for sent in signatures)


# --------------------------------------------------------------------- HTTP

def _post(path, payload, idempotency_key=None):
    return _call('POST', path, payload, idempotency_key)


def _patch(path, payload):
    return _call('PATCH', path, payload)


def _delete(path):
    return _call('DELETE', path, None)


def _get(path):
    return _call('GET', path, None)


def _call(method, path, payload, idempotency_key=None):
    if not configured():
        raise BachsError('BACHS_SECRET_KEY is not set.')

    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = request.Request(base_url() + path, data=body, method=method)
    req.add_header('Authorization', 'Bearer {0}'.format(settings.BACHS_SECRET_KEY))
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json')
    # Named deliberately. Their API sits behind Cloudflare, which blocks the
    # default "Python-urllib/3.x" outright (error 1010, "browser signature
    # banned") -- so without this every call fails with a 403 that says
    # nothing about the key being wrong.
    req.add_header('User-Agent', USER_AGENT)
    if idempotency_key:
        req.add_header('Idempotency-Key', idempotency_key)

    try:
        with request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read().decode('utf-8')
    except error.HTTPError as http_error:
        detail = http_error.read().decode('utf-8', 'replace')[:400]
        logger.warning('bachs %s %s failed: %s', method, path, detail)
        raise BachsError(_message(detail) or 'Bachs refused the request.')
    except (error.URLError, ValueError, OSError) as failure:
        logger.warning('bachs %s %s unreachable: %s', method, path, failure)
        raise BachsError('Could not reach Bachs. Try again in a moment.')

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise BachsError('Bachs sent something that was not JSON.')
    # Their payloads are the object itself, sometimes wrapped in "data".
    return parsed.get('data', parsed) if isinstance(parsed, dict) else {}


def _message(raw):
    try:
        parsed = json.loads(raw)
    except ValueError:
        return ''
    if isinstance(parsed, dict):
        error_detail = parsed.get('error')
        if isinstance(error_detail, dict):
            return error_detail.get('message', '')
        return parsed.get('message', '') or str(error_detail or '')
    return ''
