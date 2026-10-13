"""Scheduled billing work.

Much smaller than it was. Bachs runs the subscription itself -- it charges
when a trial ends, renews on schedule, and retries a failed card -- so there
is no day-8 job here any more.

What is left is reconciliation: if a webhook was missed (their outage, ours,
a deploy at the wrong moment), a membership can sit in the wrong state. This
asks Bachs what the truth is and writes it down.
"""

import logging

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api import jobs

from . import bachs
from .models import Membership

logger = logging.getLogger(__name__)

# How long past a renewal date we wait before suspecting a missed webhook.
# Long enough for a retry to arrive on its own.
GRACE_HOURS = 6


@jobs.task
def reconcile_subscriptions():
    """Check memberships that look overdue against Bachs.

    Only those past their date: asking about every member every morning
    would be a lot of calls to answer a question nobody asked.
    """
    cutoff = timezone.now() - timezone.timedelta(hours=GRACE_HOURS)
    stale = Membership.objects.filter(
        state__in=('trial', 'active', 'past_due'),
        renews_at__lt=cutoff,
    ).exclude(subscription_id='').select_related('plan', 'user')

    fixed = 0
    for membership in stale:
        if _reconcile(membership):
            fixed += 1

    logger.info('reconciled %s of %s stale memberships', fixed, stale.count())
    return fixed


def _reconcile(membership):
    try:
        subscription = bachs.get_subscription(membership.subscription_id)
    except bachs.BachsError as failure:
        logger.warning(
            'could not read subscription %s: %s', membership.subscription_id, failure,
        )
        return False

    state = subscription.get('status', '')
    period_end = _as_datetime(subscription.get('current_period_end'))
    trial_end = _as_datetime(subscription.get('trial_end'))

    if state == 'active':
        membership.state = 'active'
        membership.renews_at = period_end or membership.renews_at
        membership.trial_ends_at = None
    elif state == 'trialing':
        membership.state = 'trial'
        membership.trial_ends_at = trial_end
        membership.renews_at = trial_end
    elif state in ('past_due', 'unpaid'):
        membership.state = 'past_due'
    elif state in ('canceled', 'cancelled', 'expired'):
        membership.state = 'cancelled'
        membership.cancelled_at = membership.cancelled_at or timezone.now()
    else:
        logger.info('subscription %s is %s; left alone', membership.subscription_id, state)
        return False

    membership.save()
    return True


def _as_datetime(value):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is not None and timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.utc)
    return parsed
