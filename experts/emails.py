"""Mail about practitioner applications.

Two messages, and neither is allowed to cost us an application: both are
sent after the row is saved, and both swallow their failures. Somebody who
sent their certificate and got a connection error instead of an
acknowledgement would send it again, and we would be reading the same
application twice.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, mail_admins
from django.template.loader import render_to_string

from api import jobs

logger = logging.getLogger(__name__)

APPLICANT_SUBJECT = 'We have your application to practise on Soothify'


@jobs.task
def send_application_received(application_id):
    """Tell the applicant we have it, and what happens next.

    Takes an id, not an object: on AWS this is a Celery task, and a task's
    arguments have to survive being serialised onto a queue.
    """
    from .models import Application

    application = Application.objects.filter(pk=application_id).first()
    if application is None or not application.email:
        return False

    site_url = settings.SITE_URL.rstrip('/')
    context = {
        'first_name': _first_name(application.full_name),
        'disciplines': application.disciplines_display,
        'support_email': settings.WAITLIST_REPLY_TO,
        'site_url': site_url,
    }

    try:
        message = EmailMultiAlternatives(
            subject=APPLICANT_SUBJECT,
            body=render_to_string('email/application_received.txt', context),
            from_email=settings.WAITLIST_FROM_EMAIL,
            to=[application.email],
            reply_to=[settings.WAITLIST_REPLY_TO],
        )
        message.attach_alternative(
            render_to_string('email/application_received.html', context),
            'text/html',
        )
        message.send()
        return True
    except Exception as failure:          # noqa: BLE001 -- see the module docstring
        logger.exception('application acknowledgement failed for %s: %s',
                         application.email, failure)
        return False


def tell_the_team(application):
    """A line to whoever reviews these, so one does not sit unread.

    Deliberately thin: the name, what they practise and where to look. The
    documents are the sensitive part and they stay behind the dashboard's
    signed links rather than travelling by email.
    """
    try:
        mail_admins(
            subject='New practitioner application: {0}'.format(application.full_name),
            message=(
                '{name} applied to practise on Soothify.\n\n'
                'Practises: {disciplines}\n'
                'Experience: {years} years\n'
                'Email: {email}\n'
                'Phone: {phone}\n\n'
                'Read it, with their certificate and ID, in the dashboard.'
            ).format(
                name=application.full_name,
                disciplines=application.disciplines_display or '--',
                years=application.experience_years,
                email=application.email,
                phone=application.phone or '--',
            ),
            fail_silently=True,
        )
        return True
    except Exception as failure:          # noqa: BLE001
        logger.exception('could not tell the team about an application: %s', failure)
        return False


def _first_name(name):
    """"DANIEL EKWERE" -> "Daniel".

    The same rule as the waitlist's welcome: a greeting in capitals is
    enough on its own to be refused by the host's spam filter, and reads as
    shouting anyway. Mixed case is left as written.
    """
    parts = (name or '').split()
    if not parts:
        return 'there'
    word = parts[0]
    return word.capitalize() if word.isupper() or word.islower() else word
