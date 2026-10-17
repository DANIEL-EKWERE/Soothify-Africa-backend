"""Scheduled billing work.

Two jobs, and which of them matters depends on how memberships are sold
(MEMBERSHIP_BILLING):

**subscription** -- Bachs runs the cycle, so all that is needed is
reconciliation: if a webhook was missed (their outage, ours, a deploy at the
wrong moment) a membership can sit in the wrong state, and this asks Bachs
what the truth is.

**one_off** -- nothing renews itself, so this is the whole renewal
machinery. Members are reminded before their period runs out and the
membership lapses when it does. Nobody is ever charged by it: the reminder
is an invitation to come back and pay.
"""

import logging

from django.conf import settings
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


@jobs.task
def give_back_card_check(payment_id):
    """Refund the small charge that proved a card.

    Separate from the request that took it, because the money arriving and
    the money going back are two conversations with Bachs and the second
    must not keep a webhook waiting.

    A refund that fails is left as a paid card check and logged loudly:
    somebody is a hundred naira down and that has to be visible, not
    swallowed.
    """
    from .models import Payment

    payment = Payment.objects.filter(pk=payment_id, kind='card_check').first()
    if payment is None:
        logger.warning('no card check %s to refund', payment_id)
        return False
    if payment.refund_id:
        return True             # already given back
    if not payment.charge_id:
        logger.error('card check %s has no charge to refund', payment.reference)
        return False

    try:
        refund = bachs.refund(
            charge_id=payment.charge_id,
            reference_='rf_{0}'.format(payment.reference),
            reason='Card check for the free trial',
        )
    except bachs.BachsError as failure:
        logger.error(
            'could not refund card check %s (%s): %s',
            payment.reference, payment.charge_id, failure,
        )
        return False

    payment.refund_id = refund.get('refund_id', '') or 'refunded'
    payment.status = 'refunded'
    payment.save(update_fields=['refund_id', 'status'])
    logger.info('card check %s refunded', payment.reference)
    return True


@jobs.task
def charge_due_memberships():
    """Take the next period from the cards we hold.

    This is what stands in for a subscription. It runs the morning a period
    ends, asks Bachs to charge the saved card, and grants nothing: the money
    is confirmed by `collection.succeeded` like any other payment, because a
    charge that returns "processing" has not happened yet.

    A card that cannot be charged off-session -- most cards here, since
    their banks want an OTP nobody is present to give -- is not retried.
    The member is handed to the reminders instead, which is where they
    would have been anyway.
    """
    from .models import Membership, Payment

    now = timezone.now()
    today = timezone.localtime(now).date()

    due = Membership.objects.filter(
        state__in=('trial', 'active'),
        auto_renew=True,
        subscription_id='',          # a real subscription is theirs to run
        renews_at__isnull=False,
        renews_at__lte=now,
    ).exclude(payment_method_id='').select_related('plan', 'user')

    charged = 0
    for membership in due:
        attempted = membership.charge_attempted_at
        if attempted and timezone.localtime(attempted).date() >= today:
            continue                 # already tried today

        if not membership.can_be_charged:
            membership.stop_trying_the_card()
            continue

        amount = membership.next_charge_naira
        if not amount:
            logger.warning('membership %s has no price to charge', membership.pk)
            membership.stop_trying_the_card()
            continue

        payment = Payment.objects.create(
            user=membership.user,
            kind='membership',
            amount_naira=amount,
            description='{0} ({1})'.format(
                membership.plan.name if membership.plan else 'Membership',
                membership.get_cadence_display(),
            ),
            membership=membership,
            reference=bachs.reference('ren'),
        )
        membership.charge_attempted_at = now
        membership.save(update_fields=['charge_attempted_at', 'updated_at'])

        try:
            bachs.charge_saved_card(
                customer_id=membership.customer_id,
                payment_method_id=membership.payment_method_id,
                naira=amount,
                reference_=payment.reference,
                description=payment.description,
            )
        except bachs.BachsError as failure:
            # The usual answer here, and not a fault: the card's bank wants
            # the cardholder, and there is no cardholder.
            logger.info(
                'could not charge the saved card for %s: %s',
                membership.user_id, failure,
            )
            payment.status = 'failed'
            payment.save(update_fields=['status'])
            # Time to act on the message we are about to send, rather than
            # losing access in the same minute it arrives.
            membership.grant_grace(
                int(getattr(settings, 'MEMBERSHIP_CHARGE_GRACE_HOURS', 48)),
            )
            membership.stop_trying_the_card()
            _ask_them_to_pay(membership)
            continue

        charged += 1

    logger.info('membership charges attempted: %s', charged)
    return charged


@jobs.task
def give_up_on_stuck_charges():
    """Hand back a period whose charge never resolved.

    A charge answers "processing" and the outcome arrives by webhook. If
    none ever does -- their outage, a lost delivery -- the membership would
    otherwise sit past its date for good, which is a free library.
    """
    from .models import Membership

    hours = int(getattr(settings, 'MEMBERSHIP_CHARGE_GRACE_HOURS', 48))
    cutoff = timezone.now() - timezone.timedelta(hours=hours)

    stuck = Membership.objects.filter(
        state__in=('trial', 'active'),
        auto_renew=True,
        subscription_id='',
        renews_at__isnull=False,
        renews_at__lt=cutoff,
        charge_attempted_at__lt=cutoff,
    ).select_related('plan', 'user')

    given_up = 0
    for membership in stuck:
        membership.stop_trying_the_card()
        _ask_them_to_pay(membership)
        given_up += 1

    logger.info('charges given up on: %s', given_up)
    return given_up


def _ask_them_to_pay(membership):
    """Tell a member their card could not be used, without alarming them.

    Deliberately not "your payment failed": nothing is wrong with the card
    in most of these, it simply cannot be charged while they are not there.
    """
    from notifications.push import notify

    price = membership.next_charge_naira
    deadline = ''
    if membership.renews_at and membership.renews_at > timezone.now():
        deadline = ' Your access stays on until {0}.'.format(
            timezone.localtime(membership.renews_at).strftime('%A %d %B'),
        )

    # This is today's word on the subject, so the ordinary reminder stays
    # quiet: two messages about the same money in one morning reads as a
    # system talking to itself.
    membership.reminded_at = timezone.now()
    membership.save(update_fields=['reminded_at', 'updated_at'])

    notify(
        membership.user,
        kind='action',
        title='Please confirm your payment',
        body=(
            'We could not take {0} from your card without you there -- your '
            'bank asks you to approve it. Tap to pay and keep your '
            'membership.{1}'.format(
                '\u20a6{0:,}'.format(price) if price else 'the payment',
                deadline,
            )
        ),
        target='subscription',
    )


# When to tell someone their membership is about to end, counted in days
# before the date. Twice is enough: a warning with time to act on it, and
# one on the day itself.
REMINDER_DAYS = (2, 0)


@jobs.task
def send_renewal_reminders():
    """Tell members whose period is nearly up, while they can still act.

    Only those whose membership will not renew itself -- a subscription
    takes the money on its own, and warning somebody about a charge they
    already agreed to is noise.

    Sent whatever the notification preferences say: this is about access
    somebody is paying for, not a nudge to meditate.
    """
    from notifications.push import notify

    now = timezone.now()
    horizon = now + timezone.timedelta(days=max(REMINDER_DAYS) + 1)

    due = Membership.objects.filter(
        state__in=('trial', 'active'),
        # Ours to see through. A real subscription of theirs charges and
        # warns on its own, and two warnings is worse than one.
        subscription_id='',
        renews_at__isnull=False,
        renews_at__gt=now,
        renews_at__lte=horizon,
    ).select_related('plan', 'user')

    sent = 0
    for membership in due:
        # One number, used both to decide and to word it. Counting whole
        # days from the clock instead would have a reminder sent two days
        # ahead say "tomorrow", because 47h59m is one day and a bit.
        days = _days_left(membership.renews_at, now)
        if not _is_reminder_day(membership, now, days):
            continue

        ending = membership.state == 'trial'
        price = membership.next_charge_naira
        will_charge = membership.can_be_charged

        # No chip of its own: the feed's filters are Sessions and What's
        # new, and money belongs under neither. It shows under All.
        notify(
            membership.user,
            kind='reminder',
            title=_reminder_title(ending, will_charge),
            body=_reminder_body(membership, days, price, ending, will_charge),
            emphasis=_when(days),
            target='subscription',
        )

        membership.reminded_at = now
        membership.save(update_fields=['reminded_at', 'updated_at'])
        sent += 1

    logger.info('renewal reminders: %s sent', sent)
    return sent


@jobs.task
def expire_lapsed_memberships():
    """End what was paid for, once it has run out.

    The counterpart of the reminder: without this a membership sold a period
    at a time would never end, and someone who stopped paying would keep the
    library for good.

    A membership that renews itself is left alone even when it is past its
    date -- that is the reconciler's business, and Bachs may simply be a few
    minutes behind.
    """
    now = timezone.now()
    lapsed = Membership.objects.filter(
        state__in=('trial', 'active', 'cancelled'),
        auto_renew=False,
        renews_at__isnull=False,
        renews_at__lt=now,
    ).select_related('plan', 'user')

    ended = 0
    for membership in lapsed:
        was_trial = membership.state == 'trial'
        membership.lapse()
        _say_it_ended(membership, was_trial)
        ended += 1

    logger.info('memberships lapsed: %s', ended)
    return ended


def _days_left(renews_at, now):
    """How many days away the date is, as a person would count them.

    Calendar days in Lagos, not elapsed hours: the job runs once a morning,
    and "two days before" should mean the same thing whether it runs at six
    or at seven. A member reading "ends tomorrow" means their tomorrow.
    """
    return (timezone.localtime(renews_at).date() - timezone.localtime(now).date()).days


def _is_reminder_day(membership, now, days_left):
    """Whether today is one of the days to speak up, and we have not already."""
    if days_left not in REMINDER_DAYS:
        return False
    if membership.reminded_at is None:
        return True
    # One a day at most, so a second run of the job says nothing twice.
    return timezone.localtime(membership.reminded_at).date() < timezone.localtime(now).date()


def _reminder_title(ending_trial, will_charge):
    if will_charge:
        return 'Your free week ends soon' if ending_trial \
            else 'Your membership renews soon'
    return 'Your free week ends soon' if ending_trial \
        else 'Your membership ends soon'


def _reminder_body(membership, days, price, ending_trial, will_charge=False):
    """What to say, which turns on whether we are about to take the money.

    Somebody whose card we are going to charge is told so before it
    happens, with the card named and the date given. That is the whole
    point of warning them: the charge is the thing they might want to stop.
    """
    plan = membership.plan.name if membership.plan else 'Your membership'
    amount = '\u20a6{0:,}'.format(price) if price else ''
    when = _when(days)

    if will_charge:
        card = membership.card_label or 'your card'
        if ending_trial:
            return (
                'Your free week ends {0}. We will charge {1} to {2} then. '
                'Cancel any time before.'.format(
                    when, amount or 'for your plan', card,
                )
            )
        return (
            '{0} renews {1}. We will charge {2} to {3}. Cancel any time '
            'before.'.format(plan, when, amount or 'your plan', card)
        )

    if ending_trial:
        return 'Your free week ends {0}. Pay {1} to keep {2}.'.format(
            when, amount or 'for your plan', plan,
        )
    return '{0} ends {1}. Renew for {2} to keep your access.'.format(
        plan, when, amount or 'your plan',
    )


def _when(days):
    if days <= 0:
        return 'today'
    if days == 1:
        return 'tomorrow'
    return 'in {0} days'.format(days)


def _say_it_ended(membership, was_trial):
    from notifications.push import notify

    notify(
        membership.user,
        kind='plain',
        title='Your free week has ended' if was_trial else 'Your membership has ended',
        body=(
            'Subscribe to carry on with the library, sleep stories and the '
            'journal.'
        ),
        target='subscription',
    )
