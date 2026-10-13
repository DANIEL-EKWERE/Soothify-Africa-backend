"""Signing in with Google or Apple.

Both providers work the same way from the app's side: the phone performs the
provider's own flow, gets back a signed identity token, and posts it here.
The server verifies that token with the provider -- never trusting the email
the app claims -- and answers with the same session payload as a password
login, so the app's routing after signing in is identical whichever button
was pressed.

The token is verified rather than merely decoded. An unverified identity
token is just a JSON blob anyone can mint, and accepting one would let any
caller sign in as any email they can type.
"""

import json
import urllib.request
from datetime import timedelta

import jwt
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from rest_framework import serializers

from .models import Profile

APPLE_KEYS_URL = 'https://appleid.apple.com/auth/keys'
APPLE_ISSUER = 'https://appleid.apple.com'

# Apple rotates its signing keys, so they are fetched rather than pinned, and
# cached because the endpoint is rate-limited and every sign-in needs them.
APPLE_KEYS_CACHE_KEY = 'apple_signin_jwks'
APPLE_KEYS_TTL = int(timedelta(hours=12).total_seconds())


class SocialAuthError(serializers.ValidationError):
    """A rejected identity token.

    A ValidationError so DRF answers 400 with a message the app can show,
    rather than a 500 that reads as "our fault".
    """


def _normalise_email(email):
    return (email or '').strip().lower()


def verify_google(id_token_str):
    """Return (email, full_name) from a Google ID token, or raise.

    Accepts any of the project's client ids: Android, iOS and web each have
    their own, and the `aud` claim carries whichever one the app used. The
    web client id is also what Android returns when a server client id is
    configured, which is the usual setup.
    """
    audiences = [a for a in getattr(settings, 'GOOGLE_CLIENT_IDS', []) if a]
    if not audiences:
        raise SocialAuthError(
            {'detail': 'Google sign-in is not configured on the server.'},
        )

    try:
        claims = google_id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(),
        )
    except ValueError as exc:
        raise SocialAuthError({'detail': 'That Google sign-in was rejected.'}) from exc

    if claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
        raise SocialAuthError({'detail': 'That Google sign-in was rejected.'})
    if claims.get('aud') not in audiences:
        raise SocialAuthError({'detail': 'That Google sign-in was rejected.'})
    # Google will happily issue a token for an address the user never proved
    # they own if the account is federated; signing someone in on an
    # unverified address is an account takeover waiting to happen.
    if not claims.get('email_verified'):
        raise SocialAuthError({'detail': 'That Google address is not verified.'})

    email = _normalise_email(claims.get('email'))
    if not email:
        raise SocialAuthError({'detail': 'That Google account has no email address.'})
    return email, (claims.get('name') or '').strip()


def _apple_keys(force_refresh=False):
    if not force_refresh:
        cached = cache.get(APPLE_KEYS_CACHE_KEY)
        if cached:
            return cached
    with urllib.request.urlopen(APPLE_KEYS_URL, timeout=10) as response:
        keys = json.loads(response.read())['keys']
    cache.set(APPLE_KEYS_CACHE_KEY, keys, APPLE_KEYS_TTL)
    return keys


def verify_apple(identity_token):
    """Return (email, sub) from an Apple identity token, or raise.

    Apple only sends the name on the very first authorisation, and never
    again -- so the app passes it alongside and the caller uses it only when
    creating the account.
    """
    audiences = [a for a in getattr(settings, 'APPLE_CLIENT_IDS', []) if a]
    if not audiences:
        raise SocialAuthError(
            {'detail': 'Apple sign-in is not configured on the server.'},
        )

    try:
        header = jwt.get_unverified_header(identity_token)
    except jwt.PyJWTError as exc:
        raise SocialAuthError({'detail': 'That Apple sign-in was rejected.'}) from exc

    def _decode(keys):
        for key in keys:
            if key.get('kid') != header.get('kid'):
                continue
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
            return jwt.decode(
                identity_token,
                public_key,
                algorithms=[key.get('alg', 'RS256')],
                audience=audiences,
                issuer=APPLE_ISSUER,
            )
        return None

    try:
        claims = _decode(_apple_keys())
        if claims is None:
            # The key id is unknown: Apple has rotated since we cached. One
            # forced refresh, then give up.
            claims = _decode(_apple_keys(force_refresh=True))
    except jwt.PyJWTError as exc:
        raise SocialAuthError({'detail': 'That Apple sign-in was rejected.'}) from exc

    if claims is None:
        raise SocialAuthError({'detail': 'That Apple sign-in was rejected.'})

    email = _normalise_email(claims.get('email'))
    sub = claims.get('sub') or ''
    if not email:
        # Hide My Email still yields a relay address, so an empty email means
        # a token we cannot tie to an account.
        raise SocialAuthError({'detail': 'That Apple account has no email address.'})
    return email, sub


@transaction.atomic
def profile_for(email, full_name=''):
    """The profile behind a verified social identity, created if new.

    Matched on the verified email, so signing in with Google to an address
    that already registered with a password lands on the same account rather
    than a duplicate. Password accounts keep their password; this adds a
    second way in, not a second account.
    """
    model = Profile._meta.get_field('user').related_model
    existing = model.objects.filter(email__iexact=email).first()
    if existing is not None:
        profile = getattr(existing, 'profile', None)
        if profile is None:
            profile = Profile.objects.create(
                user=existing, full_name=existing.get_full_name(),
            )
        if full_name and not profile.full_name:
            profile.full_name = full_name
            profile.save(update_fields=['full_name'])
        return profile

    # No usable password: the account exists only through the provider, and
    # set_unusable_password is how Django says "this cannot be logged into
    # with a password" rather than "the password is empty".
    profile = Profile.create_user(email=email, password=None, full_name=full_name)
    profile.user.set_unusable_password()
    profile.user.last_login = timezone.now()
    profile.user.save(update_fields=['password', 'last_login'])
    return profile
