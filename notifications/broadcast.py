"""Sending one message to many people.

The team writes a broadcast in the dashboard, checks who it is addressed to,
and sends it. This is the part that does the sending: it works out the
audience, then for each person files a feed row, pushes it to their phone
and writes them an email, according to the channel chosen.

Everything is per-person and nothing raises. A broadcast to four thousand
people that stops at the first bad address has done more harm than one that
delivers 3,999 and says so, which is why the failure count exists.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from api import jobs

from .models import Broadcast
from .push import notify

logger = logging.getLogger(__name__)

#: How many messages one SMTP connection carries before it is reopened.
#: cPanel's mail server closes a long-lived connection on its own, and a
#: dropped one fails every message after it rather than reconnecting.
BATCH = 50


# ----------------------------------------------------------- who it goes to

def audience_for(broadcast):
    """The accounts this broadcast is addressed to.

    Returns a queryset, so the dashboard can count it without sending
    anything -- the number shown on the confirm screen is the same query
    that will run a moment later.

    The waitlist is the exception: those people have no account, so they are
    not here. See `waitlist_addresses`.
    """
    users = get_user_model().objects.filter(is_active=True)

    if broadcast.audience == Broadcast.Audience.CHOSEN:
        users = users.filter(pk__in=broadcast.people.values('pk'))
    elif broadcast.audience == Broadcast.Audience.MEMBERS:
        users = users.filter(membership__state__in=('trial', 'active'))
    elif broadcast.audience == Broadcast.Audience.LAPSED:
        users = users.filter(membership__state__in=('expired', 'cancelled'))
    elif broadcast.audience == Broadcast.Audience.PRACTITIONERS:
        users = users.filter(profile__role='practitioner')
    elif broadcast.audience == Broadcast.Audience.WAITLIST:
        return users.none()

    if broadcast.is_marketing:
        # Consent, not a preference. Somebody who never turned offers on
        # never agreed to be marketed to, and the switch defaults to off.
        users = users.filter(notification_preferences__notify_offers=True)

    return users.distinct()


def waitlist_addresses(broadcast):
    """Addresses from the landing page's waitlist, which have no accounts.

    Only for the waitlist audience, and only by email: there is no phone to
    push to and no feed to file anything in.
    """
    if broadcast.audience != Broadcast.Audience.WAITLIST:
        return []

    from home.models import WaitlistSignup

    seen = set(
        get_user_model().objects.values_list('email', flat=True),
    )
    addresses = []
    for email in WaitlistSignup.objects.values_list('email', flat=True):
        address = (email or '').strip().lower()
        # Somebody who signed up and then made an account would otherwise
        # get the same message twice.
        if address and address not in seen:
            seen.add(address)
            addresses.append(address)
    return addresses


def count_for(broadcast):
    """How many people this would reach, for the confirm screen."""
    if broadcast.audience == Broadcast.Audience.WAITLIST:
        return len(waitlist_addresses(broadcast))

    users = audience_for(broadcast)
    if broadcast.goes_by_email:
        # An account with no address cannot be emailed; if email is the only
        # channel, it is not a recipient at all.
        if not broadcast.goes_by_push:
            return users.exclude(Q(email='') | Q(email__isnull=True)).count()
    return users.count()


# -------------------------------------------------------------- sending it

@jobs.task
def send_broadcast(broadcast_id):
    """Send one broadcast. Runs inline on cPanel, in the worker on AWS."""
    broadcast = Broadcast.objects.filter(pk=broadcast_id).first()
    if broadcast is None:
        return 0
    if broadcast.state != Broadcast.State.SENDING:
        # Guarded in the view before this runs, so reaching here means two
        # sends raced. The second one stops.
        logger.warning('broadcast %s is %s, not sending', broadcast_id, broadcast.state)
        return 0

    delivered = failed = 0
    notes = []

    if broadcast.audience == Broadcast.Audience.WAITLIST:
        sent, bad = _email_addresses(broadcast, waitlist_addresses(broadcast))
        delivered, failed = sent, bad
    else:
        people = list(audience_for(broadcast).select_related('profile'))
        if broadcast.goes_by_push:
            delivered += _file_and_push(broadcast, people)
        if broadcast.goes_by_email:
            addressed = [p for p in people if (p.email or '').strip()]
            missing = len(people) - len(addressed)
            if missing:
                notes.append('{0} account(s) have no email address'.format(missing))
            sent, bad = _email_addresses(
                broadcast, [p.email.strip() for p in addressed],
            )
            failed += bad
            # When both channels run, the feed row is the delivery that
            # counts; counting the email too would report twice the people.
            if not broadcast.goes_by_push:
                delivered += sent

    broadcast.recipients = max(delivered + failed, broadcast.recipients)
    broadcast.delivered = delivered
    broadcast.failed = failed
    broadcast.detail = '; '.join(notes)
    broadcast.state = (
        Broadcast.State.FAILED
        if delivered == 0 and failed
        else Broadcast.State.SENT
    )
    broadcast.sent_at = timezone.now()
    broadcast.save(update_fields=[
        'recipients', 'delivered', 'failed', 'detail', 'state', 'sent_at',
    ])

    logger.info(
        'broadcast %s: %s delivered, %s failed', broadcast_id, delivered, failed,
    )
    return delivered


def _file_and_push(broadcast, people):
    """A feed row each, pushed to whatever phones they have."""
    done = 0
    for person in people:
        try:
            notify(
                person,
                kind='plain',
                title=broadcast.title,
                body=broadcast.body,
                # No target: this opens the feed, which is where it is.
            )
            done += 1
        except Exception:
            logger.exception(
                'broadcast %s: could not file for user %s', broadcast.pk, person.pk,
            )
    return done


def _email_addresses(broadcast, addresses):
    """Write to each address. Returns (sent, failed)."""
    if not addresses:
        return 0, 0

    context = {
        'title': broadcast.title,
        # Split here rather than in the template: a template that does text
        # processing is a template nobody can read.
        'paragraphs': [p.strip() for p in broadcast.body.split('\n\n') if p.strip()],
        'site_url': settings.SITE_URL.rstrip('/'),
        'support_email': settings.WAITLIST_REPLY_TO,
    }
    text = render_to_string('email/broadcast.txt', context)
    html = render_to_string('email/broadcast.html', context)

    sent = failed = 0
    for start in range(0, len(addresses), BATCH):
        chunk = addresses[start:start + BATCH]
        try:
            connection = get_connection()
            connection.open()
        except Exception:
            logger.exception('broadcast %s: no mail connection', broadcast.pk)
            failed += len(chunk)
            continue

        for address in chunk:
            try:
                message = EmailMultiAlternatives(
                    subject=broadcast.title,
                    body=text,
                    from_email=settings.WAITLIST_FROM_EMAIL,
                    # One message per person: a shared To: line would show
                    # every recipient their address list.
                    to=[address],
                    reply_to=[settings.WAITLIST_REPLY_TO],
                    connection=connection,
                )
                message.attach_alternative(html, 'text/html')
                message.send()
                sent += 1
            except Exception:
                logger.exception('broadcast %s: could not write to %s',
                                 broadcast.pk, address)
                failed += 1

        try:
            connection.close()
        except Exception:
            pass

    return sent, failed
