"""Scheduled work for sessions. See soothifyAfrica/celery.py for the times."""

import datetime
import logging

from django.utils import timezone

from api import jobs

logger = logging.getLogger(__name__)


@jobs.task
def send_session_reminders():
    """Tell people about today's sessions, if they asked to be told.

    Reads the notification preferences rather than mailing everyone: the
    reminder switches default to off, and an unasked-for reminder about a
    therapy appointment is a privacy problem, not just a nuisance.
    """
    from notifications.models import Preferences
    from notifications.push import notify

    from .models import Session

    today = timezone.localdate()
    due = Session.objects.filter(
        status='confirmed', starts_at__date=today,
    ).select_related('client', 'expert', 'expert__profile')

    sent = 0
    for session in due:
        wants = Preferences.objects.filter(
            user=session.client, notify_balance=True,
        ).exists()
        if not wants:
            continue
        discipline = session.get_discipline_display()
        notify(
            session.client,
            kind='reminder',
            title='Reminder',
            body='You have {0} session today at {1}'.format(
                discipline, _clock(session.starts_at),
            ),
            emphasis=discipline,
            target='session/{0}'.format(session.pk),
            filters=['sessions'],
        )
        sent += 1

    logger.info('session reminders: %s sent for %s', sent, today)
    return sent


@jobs.task
def release_expired_holds():
    """Give back slots nobody paid for.

    A booking holds its hour for twenty minutes. Without this, an abandoned
    checkout blocks that time for everyone else.
    """
    from .models import Session

    expired = Session.objects.filter(
        status='pending', expires_at__lt=timezone.now(),
    )
    count = expired.update(
        status='cancelled', cancelled_at=timezone.now(),
    )
    logger.info('released %s unpaid holds', count)
    return count


def _clock(when):
    local = timezone.localtime(when)
    hour = local.hour % 12 or 12
    return '{0}:{1:02d}{2}'.format(hour, local.minute, 'am' if local.hour < 12 else 'pm')


def _unused(delta=datetime.timedelta()):  # pragma: no cover
    return delta
