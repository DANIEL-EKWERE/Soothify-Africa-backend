"""Scheduled billing work.

The one that matters: a trial ends on day 8 and the card is charged then. On
AWS, Celery beat runs it (see soothifyAfrica/celery.py); anywhere without a
worker, `python manage.py run_due_jobs` does the same thing from cron.
"""

import logging

from django.utils import timezone

from api import jobs

from . import paystack
from .models import Membership, Payment

logger = logging.getLogger(__name__)


@jobs.task
def charge_due_subscriptions():
    """Charge every membership whose trial or period has ended.

    Each one is attempted on its own: a card that fails must not stop the
    rest of the run. A failure marks the membership past due rather than
    cancelling it, because the usual cause is a card that needs topping up.
    """
    due = Membership.objects.filter(
        state__in=('trial', 'active'),
        renews_at__lte=timezone.now(),
    ).select_related('plan', 'user')

    charged = 0
    for membership in due:
        if not membership.authorization_code or membership.plan is None:
            logger.warning(
                'membership %s is due but has no saved card', membership.pk,
            )
            continue
        if _charge(membership):
            charged += 1

    logger.info('charged %s of %s due memberships', charged, due.count())
    return charged


def _charge(membership):
    amount = membership.plan.price_naira or 0
    if amount <= 0:
        return False

    payment = Payment.objects.create(
        user=membership.user,
        kind='membership',
        amount_naira=amount,
        description='{0} ({1})'.format(
            membership.plan.name, membership.get_cadence_display(),
        ),
        membership=membership,
        reference=paystack.reference('ren'),
    )

    try:
        result = paystack.charge_authorization(
            email=membership.user.email,
            naira=amount,
            authorization_code=membership.authorization_code,
            reference_=payment.reference,
            metadata={'kind': 'membership', 'membership_id': membership.pk},
        )
    except paystack.PaystackError as failure:
        logger.warning('renewal failed for membership %s: %s', membership.pk, failure)
        payment.status = 'failed'
        payment.save(update_fields=['status'])
        membership.state = 'past_due'
        membership.save(update_fields=['state', 'updated_at'])
        return False

    # Paystack answers synchronously here, and also sends the webhook. The
    # webhook is still what settles it, so this only records the attempt.
    if result.get('status') == 'success':
        logger.info('renewal charged for membership %s', membership.pk)
        return True

    payment.status = 'failed'
    payment.save(update_fields=['status'])
    membership.state = 'past_due'
    membership.save(update_fields=['state', 'updated_at'])
    return False
