"""Joining a session call, through ZEGOCLOUD.

The server's only job is to say "this person may join that room, until then".
A token is minted per session and expires with it, so a link cannot be reused
tomorrow or handed to someone who was not on the booking.

ZEGOCLOUD's own server sample is copied widely; this is written from its token
04 format rather than pasted, and tested. It needs the App ID and Server
Secret from the console, in the environment:

    ZEGO_APP_ID=123456789
    ZEGO_SERVER_SECRET=32-character-secret

Without them the endpoint says so plainly instead of minting something that
cannot work.
"""

import base64
import json
import os
import struct
import time

from django.conf import settings


class NotConfigured(RuntimeError):
    """No credentials, so no call can be joined."""


def app_id():
    value = os.environ.get('ZEGO_APP_ID', '') or getattr(settings, 'ZEGO_APP_ID', '')
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def server_secret():
    return os.environ.get('ZEGO_SERVER_SECRET', '') or getattr(
        settings, 'ZEGO_SERVER_SECRET', '',
    )


def token_for(user_id, room_id, seconds=3600, privileges=None):
    """A ZEGOCLOUD token 04 string.

    `user_id` and `room_id` are ours -- the account id and 'session-12' -- so
    a token is only ever good for the one session it was minted for.
    """
    secret = server_secret()
    identifier = app_id()
    if not identifier or len(secret) != 32:
        raise NotConfigured(
            'Set ZEGO_APP_ID and a 32-character ZEGO_SERVER_SECRET to enable calls.',
        )

    now = int(time.time())
    payload = {
        'app_id': identifier,
        'user_id': str(user_id),
        'nonce': struct.unpack('i', os.urandom(4))[0],
        'ctime': now,
        'expire': now + int(seconds),
        'room_id': str(room_id),
        # 1 = may publish (speak and show video), 2 = may subscribe. Both
        # sides of a therapy session need each.
        'privilege': privileges or {'1': 1, '2': 1},
        'stream_id_list': None,
    }
    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')

    iv = os.urandom(16)
    encrypted = _aes_cbc_encrypt(body, secret.encode('utf-8'), iv)

    packed = (
        struct.pack('!q', payload['expire'])
        + struct.pack('!h', len(iv)) + iv
        + struct.pack('!h', len(encrypted)) + encrypted
    )
    return '04' + base64.b64encode(packed).decode('utf-8')


def _aes_cbc_encrypt(data, key, iv):
    """AES-CBC with PKCS#7 padding, which is what ZEGOCLOUD expects."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(data) + padder.finalize()

    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()
