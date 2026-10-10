"""Telling the team an enquiry came in.

Sent as background work: a company filling in a form should not wait on the
mail server, and a mail failure must not lose the enquiry -- the row is saved
before this runs.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage

from api import jobs

logger = logging.getLogger(__name__)


@jobs.task
def send_enquiry_notice(enquiry_id):
    from .models import Enquiry

    enquiry = Enquiry.objects.filter(pk=enquiry_id).first()
    if enquiry is None:
        return False

    lines = [
        'A company asked about Corporate Wellness.',
        '',
        'Company:   {0}'.format(enquiry.company),
        'Contact:   {0}'.format(enquiry.contact_name),
        'Email:     {0}'.format(enquiry.email),
        'Phone:     {0}'.format(enquiry.phone),
        'Employees: {0}'.format(enquiry.employees or '-'),
        'Services:  {0}'.format(enquiry.services or '-'),
        '',
        enquiry.notes or '(no additional requirements given)',
    ]
    message = EmailMessage(
        subject='Corporate enquiry from {0}'.format(enquiry.company),
        body='\n'.join(lines),
        from_email=settings.WAITLIST_FROM_EMAIL,
        to=[settings.WAITLIST_REPLY_TO],
        # So the team can answer the company straight from the notice.
        reply_to=[enquiry.email],
    )
    message.send(fail_silently=False)
    return True
