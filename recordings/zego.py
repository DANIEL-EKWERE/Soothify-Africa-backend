"""ZEGOCLOUD Cloud Recording.

Recording happens in ZEGOCLOUD's cloud and the file is delivered to our own
bucket, so it does not depend on either person's phone and a practitioner
cannot switch it off.

Their server API signs each request with the App ID, the Server Secret, a
nonce and a timestamp. Everything Zego-specific lives here; the rest of the
app deals in Recording rows.

Check the request bodies against the current Cloud Recording documentation
before going live -- Zego revises these fields between versions, and a wrong
field name fails at the worst moment rather than at import.
"""

import hashlib
import json
import logging
import random
import time
from urllib import error, parse, request

from django.conf import settings

logger = logging.getLogger(__name__)

BASE_URL = 'https://cloudrecord-api.zego.im/'
TIMEOUT = 20


class ZegoError(RuntimeError):
    """Recording could not be started or stopped."""


def configured():
    from experts import calls

    return bool(calls.app_id() and calls.server_secret())


def start(room_id, output_prefix):
    """Begin recording a room. Returns Zego's task id.

    Audio only: video would multiply storage for no benefit to either the
    safety case or the off-platform one, and a therapy session on video is
    more exposure than the purpose needs.
    """
    body = {
        'RoomId': room_id,
        'RecordInputParams': {
            # 1 = audio only.
            'RecordMode': 1,
            'StreamType': 1,
            'MaxIdleTime': 300,
        },
        'RecordOutputParams': {
            'OutputFileFormat': 'mp3',
            'OutputFolder': output_prefix,
        },
        'StorageParams': _storage_params(output_prefix),
    }
    data = _call('StartRecord', body)
    return data.get('TaskId', '')


def stop(task_id):
    """Stop a running recording. Safe to call on one already finished."""
    return _call('StopRecord', {'TaskId': task_id})


def _storage_params(prefix):
    """Where Zego delivers the file: our own bucket, not theirs.

    Keeping the audio in our bucket is what makes deletion after thirty days
    something we can actually carry out.
    """
    import os

    return {
        # 2 = Amazon S3 (and S3-compatible, which covers Cloudflare R2).
        'Vendor': 2,
        'Region': os.environ.get('AWS_S3_REGION_NAME', 'eu-west-1'),
        'Bucket': os.environ.get('AWS_STORAGE_BUCKET_NAME', ''),
        'AccessKeyId': os.environ.get('AWS_ACCESS_KEY_ID', ''),
        'AccessKeySecret': os.environ.get('AWS_SECRET_ACCESS_KEY', ''),
        'Prefix': prefix,
    }


def _call(action, body):
    from experts import calls

    app_id = calls.app_id()
    secret = calls.server_secret()
    if not app_id or not secret:
        raise ZegoError('ZEGOCLOUD credentials are not set.')

    timestamp = int(time.time())
    nonce = str(random.randint(100000, 999999))
    signature = hashlib.md5(
        '{0}{1}{2}{3}'.format(app_id, nonce, secret, timestamp).encode('utf-8'),
    ).hexdigest()

    query = parse.urlencode({
        'Action': action,
        'AppId': app_id,
        'SignatureNonce': nonce,
        'Timestamp': timestamp,
        'Signature': signature,
        'SignatureVersion': '2.0',
        'IsTest': 'false' if getattr(settings, 'ON_AWS', False) else 'true',
    })

    req = request.Request(
        '{0}?{1}'.format(BASE_URL, query),
        data=json.dumps(body).encode('utf-8'),
        method='POST',
    )
    req.add_header('Content-Type', 'application/json')

    try:
        with request.urlopen(req, timeout=TIMEOUT) as response:
            parsed = json.loads(response.read().decode('utf-8'))
    except (error.URLError, ValueError, OSError) as failure:
        logger.warning('zego %s failed: %s', action, failure)
        raise ZegoError('Could not reach ZEGOCLOUD.')

    if parsed.get('Code') not in (0, '0'):
        raise ZegoError(parsed.get('Message') or 'ZEGOCLOUD refused the request.')
    return parsed.get('Data') or {}
