"""The welcome email sent the moment someone joins the waitlist."""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)

# No emoji: the host's outgoing spam filter rejects this email outright when
# the subject carries the ✨ (550 "high-probability spam"); the same words pass.
SUBJECT = "Welcome to Soothify! You're officially on the waitlist"


def first_name(name):
    """"DANIEL EKWERE" -> "Daniel". A greeting in capitals ("Hi DANIEL,") is
    enough on its own to get the email rejected by the host's spam filter, and
    reads as shouting anyway. Names typed in mixed case are left as written."""
    parts = (name or '').split()
    if not parts:
        return 'there'
    word = parts[0]
    return word.capitalize() if word.isupper() or word.islower() else word


def send_welcome_email(signup, request=None):
    """Write to a new signup. Never raises: a mail problem must not cost us the
    signup, which is already saved by the time this runs."""
    if not signup.email:
        return False

    path = reverse('home:waitlist')
    share_url = f'{path}?ref={signup.referral_code}'
    site_url = settings.SITE_URL.rstrip('/')
    if request is not None:
        share_url = request.build_absolute_uri(share_url)
    else:
        share_url = f'{site_url}{share_url}'

    context = {
        'first_name': first_name(signup.name),
        'position': signup.position(),
        'share_url': share_url,
        'support_email': settings.WAITLIST_REPLY_TO,
        'site_url': site_url,
    }

    try:
        message = EmailMultiAlternatives(
            subject=SUBJECT,
            body=render_to_string('email/waitlist_welcome.txt', context),
            from_email=settings.WAITLIST_FROM_EMAIL,
            to=[signup.email],
            reply_to=[settings.WAITLIST_REPLY_TO],
        )
        message.attach_alternative(render_to_string('email/waitlist_welcome.html', context), 'text/html')
        message.send()
        return True
    except Exception:
        # A dead SMTP server, a refused relay, a bad address -- log it and move
        # on; the person is on the list either way.
        logger.exception('waitlist welcome email failed for %s', signup.email)
        return False
