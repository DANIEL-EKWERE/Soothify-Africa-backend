"""Mail this app sends: the password reset code.

Sent as background work. Someone who has forgotten their password should see
"check your email" immediately, not watch a spinner while a mail server
thinks about it -- and a mail failure must not be reported as "that address
does not exist", which is what an error here would look like.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from api import jobs

logger = logging.getLogger(__name__)

SUBJECT = 'Your Soothify reset code'


@jobs.task
def send_reset_code(user_id, code):
    """Send one reset code. Never raises."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None or not user.email:
        return False

    profile = getattr(user, 'profile', None)
    context = {
        'first_name': _first_name(getattr(profile, 'full_name', '') or user.get_full_name()),
        'code': code,
        'minutes': 15,
        'support_email': settings.WAITLIST_REPLY_TO,
        'site_url': settings.SITE_URL.rstrip('/'),
    }

    try:
        message = EmailMultiAlternatives(
            subject=SUBJECT,
            body=render_to_string('email/password_reset.txt', context),
            from_email=settings.WAITLIST_FROM_EMAIL,
            to=[user.email],
            reply_to=[settings.WAITLIST_REPLY_TO],
        )
        message.attach_alternative(
            render_to_string('email/password_reset.html', context), 'text/html',
        )
        message.send()
        return True
    except Exception:
        logger.exception('reset code email failed for %s', user.email)
        return False


def _first_name(name):
    parts = (name or '').split()
    if not parts:
        return 'there'
    word = parts[0]
    return word.capitalize() if word.isupper() or word.islower() else word
