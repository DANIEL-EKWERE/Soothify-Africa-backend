"""Memberships, the free trial, session payments and invoices.

Money is whole naira throughout, as everywhere else in this project. Bachs
takes decimal strings ("150000.00"), so the conversion happens at its edge
(billing/bachs.py) and nowhere else.

The shape follows the app's Manage-subscription screens: a membership is
either inactive, on trial, or active; it renews monthly or annually; and
Billing History lists what was charged.
"""

import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Cadence(models.TextChoices):
    MONTHLY = 'monthly', _('Monthly')
    ANNUAL = 'annual', _('Annual')


class PlanAction(models.TextChoices):
    """What a plan card's button does -- the app's PlanAction enum."""

    TRIAL = 'trial', _('Start the free trial')
    WAITLIST = 'waitlist', _('Join the waitlist')
    CORPORATE = 'corporate', _('Speak with the corporate team')


class Plan(models.Model):
    """One card on the Plans screen.

    Two of the three sell nothing yet -- Passport collects an email and
    Corporate opens a conversation -- so `price_naira` is optional and the
    button is what the card is really for.
    """

    key = models.SlugField(_('key'), primary_key=True, max_length=40)
    name = models.CharField(_('name'), max_length=80)
    description = models.TextField(_('description'))

    price_naira = models.PositiveIntegerField(_('price'), null=True, blank=True)
    price_suffix = models.CharField(
        _('after the price'), max_length=40, blank=True,
        help_text=_('"/One time access", "/mo".'),
    )
    cadence = models.CharField(
        _('billed'), max_length=10, choices=Cadence.choices, blank=True,
    )
    action = models.CharField(
        _('button'), max_length=20, choices=PlanAction.choices, default=PlanAction.TRIAL,
    )
    is_recommended = models.BooleanField(
        _('emphasised'), default=False,
        help_text=_('The design fills one card and reverses its text.'),
    )
    position = models.PositiveSmallIntegerField(_('position'), default=0)
    is_active = models.BooleanField(_('listed'), default=True)

    # The recurring product at Bachs this card sells. Its trial_period is
    # what starts a free trial, so a plan without one cannot be subscribed
    # to -- see billing/bachs.py.
    bachs_product_id = models.CharField(
        _('Bachs product'), max_length=80, blank=True,
    )
    trial_days = models.PositiveSmallIntegerField(
        _('free trial (days)'), default=7,
        help_text=_('Set on the product at Bachs. Zero means no trial.'),
    )

    class Meta:
        ordering = ('position', 'name')
        verbose_name = _('plan')
        verbose_name_plural = _('plans')

    def __str__(self):
        return self.name

    @property
    def price_display(self):
        if self.price_naira is None:
            return ''
        return '₦{0:,}'.format(self.price_naira)


class Membership(models.Model):
    """Where one person's subscription stands.

    One row per member, not one per period: the periods are the invoices. The
    state here is what the "Your Subscription" screen reads.
    """

    STATE = (
        ('inactive', _('Inactive')),
        ('trial', _('On the free trial')),
        ('active', _('Active')),
        ('past_due', _('Payment failed')),
        ('cancelled', _('Cancelled')),
    )

    # The app's own trial length, from "Start 7-Day Free Trial".
    TRIAL_DAYS = 7

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='membership',
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, related_name='memberships', null=True, blank=True,
    )
    cadence = models.CharField(
        _('billed'), max_length=10, choices=Cadence.choices, default=Cadence.ANNUAL,
    )
    state = models.CharField(_('state'), max_length=20, choices=STATE, default='inactive')

    trial_ends_at = models.DateTimeField(_('trial ends'), null=True, blank=True)
    renews_at = models.DateTimeField(_('renews'), null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    # Whether the date above is a renewal or an ending.
    #
    # False while memberships are sold one period at a time -- see
    # MEMBERSHIP_BILLING. The screens have to say which it is: "renews on the
    # 9th" when it will take the money by itself, "ends on the 9th" when the
    # member has to come back and pay. Getting that wrong is how someone
    # loses access believing they were covered.
    auto_renew = models.BooleanField(_('renews itself'), default=True)

    # When the free week was taken, so it cannot be taken twice. Kept apart
    # from `trial_ends_at`, which is cleared once the trial is over.
    trial_started_at = models.DateTimeField(
        _('trial started'), null=True, blank=True,
    )

    # When the free week was turned down because the card had already had
    # one. Without this the offer would still be showing, and the next tap
    # would take another hundred naira to reach the same answer.
    #
    # Cleared by hand if it was ever wrong -- a card signature is brand,
    # last four and expiry, and two strangers can share all three.
    trial_refused_at = models.DateTimeField(
        _('free week refused'), null=True, blank=True,
    )

    # When we last tried to charge the saved card for a new period. Kept so
    # the job does not try twice in a day, and so a charge that never comes
    # back either way can be given up on.
    charge_attempted_at = models.DateTimeField(
        _('charge attempted'), null=True, blank=True,
    )

    # The last time we told them their membership was about to end, so a
    # daily job does not tell them daily.
    reminded_at = models.DateTimeField(_('last reminded'), null=True, blank=True)

    # Bachs' handles. No card detail reaches this server: Bachs holds the
    # saved method and runs the billing cycle, including the charge when a
    # trial ends. The brand and last four digits are only so the screen can
    # say "Mastercard ending in 1234".
    customer_id = models.CharField(_('Bachs customer'), max_length=80, blank=True)
    subscription_id = models.CharField(_('Bachs subscription'), max_length=80, blank=True)
    card_brand = models.CharField(_('card'), max_length=40, blank=True)
    card_last4 = models.CharField(_('last four'), max_length=4, blank=True)
    card_exp_month = models.PositiveSmallIntegerField(
        _('expires (month)'), null=True, blank=True,
    )
    card_exp_year = models.PositiveSmallIntegerField(
        _('expires (year)'), null=True, blank=True,
    )

    # Their handle for the saved card. Scoped to their customer, so it says
    # nothing about the same card in somebody else's hands.
    payment_method_id = models.CharField(
        _('Bachs payment method'), max_length=80, blank=True,
    )

    # What does say something: a hash of the brand, the last four digits and
    # the expiry. It is how a second free week on the same card is spotted
    # when it is asked for under a new email address, which is the whole
    # reason a card is asked for at all.
    #
    # Hashed rather than kept in the open: it is a cross-account identifier,
    # and nothing needs to read it back -- only to compare.
    card_signature = models.CharField(
        _('card signature'), max_length=64, blank=True, db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('membership')
        verbose_name_plural = _('memberships')

    def __str__(self):
        return '{0} ({1})'.format(self.user.get_username(), self.get_state_display())

    @property
    def is_active(self):
        """Whether paid content may be played right now.

        A trial counts: the whole point of the seven days is full access.
        """
        if self.state in ('trial', 'active'):
            return True
        # Cancelled mid-period: they paid for the rest of it.
        if self.state == 'cancelled' and self.renews_at:
            return self.renews_at > timezone.now()
        return False

    @property
    def runs_itself(self):
        """Whether Bachs runs this membership's billing cycle.

        True only for a real subscription of theirs. Our own charging of a
        saved card is not that: it is a job here, and it is allowed to fail.
        """
        return bool(self.subscription_id)

    @property
    def has_usable_card(self):
        """Whether a card is on file that we are allowed to charge at all.

        About the card, not about this membership's intentions: it is what
        decides whether a period *can* renew itself, and so what
        `auto_renew` is set from.
        """
        if self.runs_itself:
            return False
        if not (self.customer_id and self.payment_method_id):
            return False
        return bool(getattr(settings, 'MEMBERSHIP_AUTO_CHARGE', True))

    @property
    def can_be_charged(self):
        """Whether we will in fact try the card when this period ends.

        The card, plus the intention. They come apart after a charge the
        bank would not take without its customer: the card is still on file
        and still unusable, and the member is paying by hand from then on.
        """
        return self.has_usable_card and self.auto_renew

    @property
    def card_label(self):
        if not self.card_last4:
            return ''
        return '{0} ending in {1}'.format(self.card_brand or 'Card', self.card_last4)

    @property
    def next_charge_naira(self):
        if self.plan is None or self.plan.price_naira is None:
            return 0
        return self.plan.price_naira

    @staticmethod
    def signature_for(brand, last4, exp_month, exp_year):
        """A stable, opaque name for one card.

        Bachs give no fingerprint of their own -- their saved-card id belongs
        to one customer, so the same card under a second email reads as a
        different card. Brand, last four and expiry are everything they do
        give, so that is what this is made of.

        It is a blunt instrument, and deliberately so: it is used to decline
        a *free week*, never to decline a payment. Two strangers whose cards
        agree on all four will find the second of them is offered the paid
        plan instead, which is a disappointment and not a lockout.
        """
        if not last4:
            return ''
        raw = '{0}|{1}|{2}|{3}|{4}'.format(
            (brand or '').strip().lower(), last4,
            exp_month or '', exp_year or '', settings.SECRET_KEY,
        )
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    @classmethod
    def trial_taken_on_card(cls, signature, exclude_user=None):
        """Whether this card has already had its free week, on any account."""
        if not signature:
            return False
        others = cls.objects.filter(
            card_signature=signature, trial_started_at__isnull=False,
        )
        if exclude_user is not None:
            others = others.exclude(user=exclude_user)
        return others.exists()

    def remember_card(self, brand, last4, exp_month=None, exp_year=None,
                      payment_method_id='', customer_id=''):
        """Record the card that was just proved, and what it is called."""
        self.card_brand = (brand or '').title()
        self.card_last4 = last4 or ''
        self.card_exp_month = exp_month
        self.card_exp_year = exp_year
        self.card_signature = self.signature_for(brand, last4, exp_month, exp_year)
        if payment_method_id:
            self.payment_method_id = payment_method_id
        if customer_id:
            self.customer_id = customer_id
        self.save(update_fields=[
            'card_brand', 'card_last4', 'card_exp_month', 'card_exp_year',
            'card_signature', 'payment_method_id', 'customer_id', 'updated_at',
        ])

    @property
    def trial_used(self):
        """Whether the free week is off the table for this account.

        Taken, or refused because the card behind it had already had one.
        Either way there is nothing left to offer, and offering it anyway
        costs the person another card check to be told the same thing.
        """
        return self.trial_started_at is not None or self.trial_refused_at is not None

    def grant_grace(self, hours):
        """Keep access open a little longer after a charge we could not take.

        Not generosity: the message we send says "tap to pay and keep your
        membership", and cutting somebody off the same minute makes that a
        lie. The window is what gives them time to do the thing we asked.

        Granted once, because it follows a charge attempt and no further
        attempt is made after one fails.
        """
        start = self.renews_at or timezone.now()
        if start < timezone.now():
            start = timezone.now()
        self.renews_at = start + timezone.timedelta(hours=hours)
        self.save(update_fields=['renews_at', 'updated_at'])

    def stop_trying_the_card(self):
        """Give up on charging this card; the member is asked instead.

        Not a punishment and not an error: an off-session charge cannot ask
        for an OTP, so the commonest card here simply cannot be charged
        this way. What follows is the reminder, which was the only path
        before any of this existed.
        """
        self.auto_renew = False
        self.save(update_fields=['auto_renew', 'updated_at'])

    def refuse_trial(self):
        self.trial_refused_at = timezone.now()
        self.save(update_fields=['trial_refused_at', 'updated_at'])

    def start_trial(self, plan, cadence, trial_ends_at=None, subscription_id='',
                    auto_renew=True):
        """Begin the free week.

        `trial_ends_at` comes from the subscription when Bachs is running
        one: falling back to our own clock would eventually disagree with the
        date they actually charge on, and the screen promises a date. Sold
        one period at a time there is no subscription, so our clock is the
        only clock, and `auto_renew` is False -- nothing will be charged when
        the week ends.
        """
        self.plan = plan
        self.cadence = cadence
        self.state = 'trial'
        self.trial_ends_at = trial_ends_at or (
            timezone.now() + timezone.timedelta(days=self.TRIAL_DAYS)
        )
        self.renews_at = self.trial_ends_at
        self.trial_started_at = self.trial_started_at or timezone.now()
        self.auto_renew = auto_renew
        self.reminded_at = None
        if subscription_id:
            self.subscription_id = subscription_id
        self.cancelled_at = None
        self.save()

    def extend(self, until=None):
        """Move the renewal on, after a successful charge.

        `until` is the period end Bachs sent. Without it -- a one-off
        payment, or an invoice that did not carry one -- we work it out from
        the cadence.
        """
        if until is None:
            days = 365 if self.cadence == Cadence.ANNUAL else 30
            start = self.renews_at or timezone.now()
            if start < timezone.now():
                start = timezone.now()
            until = start + timezone.timedelta(days=days)
        self.renews_at = until
        self.state = 'active'
        self.trial_ends_at = None
        # A period that was paid for is a period to be reminded about again.
        self.reminded_at = None
        self.save(update_fields=[
            'renews_at', 'state', 'trial_ends_at', 'reminded_at', 'updated_at',
        ])

    def cancel(self):
        """Stop renewing. Access runs to the end of what was paid for.

        Also puts the saved card down: "cancel" means "do not charge me
        again", and a cancelled membership still marked as renewing itself
        would be charged by the job -- or, worse, never ended by it.
        """
        self.state = 'cancelled'
        self.cancelled_at = timezone.now()
        self.auto_renew = False
        self.save(update_fields=[
            'state', 'cancelled_at', 'auto_renew', 'updated_at',
        ])

    def lapse(self):
        """The period ran out and nobody paid for another.

        The plan and the dates stay as they were: Billing History and
        "you were on the annual plan" both read from them, and the row is
        the only record that this person was ever a member.
        """
        self.state = 'inactive'
        self.trial_ends_at = None
        self.save(update_fields=['state', 'trial_ends_at', 'updated_at'])


class Payment(models.Model):
    """One attempt to take money, for a membership or for a session.

    Kept even when it fails: a failed charge is why a membership went past
    due, and somebody will ask.
    """

    KINDS = (
        ('membership', _('Membership')),
        ('session', _('Session')),
        # The small charge that proves a card before the free week, taken
        # and given straight back. Never shown on Billing History.
        ('card_check', _('Card check')),
    )
    STATUS = (
        ('pending', _('Pending')),
        ('paid', _('Paid')),
        ('failed', _('Failed')),
        ('refunded', _('Refunded')),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payments',
    )
    kind = models.CharField(_('for'), max_length=20, choices=KINDS)
    amount_naira = models.PositiveIntegerField(_('amount'))
    description = models.CharField(_('description'), max_length=200, blank=True)

    session = models.ForeignKey(
        'experts.Session', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payments',
    )
    membership = models.ForeignKey(
        Membership, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payments',
    )
    promo_code = models.CharField(_('promo code'), max_length=40, blank=True)

    # Ours, sent to Bachs and echoed back on the webhook.
    reference = models.CharField(_('reference'), max_length=80, unique=True)
    checkout_id = models.CharField(_('Bachs checkout'), max_length=80, blank=True)

    # Their id for the money itself, which is what a refund is asked for by.
    charge_id = models.CharField(_('Bachs charge'), max_length=80, blank=True)
    refund_id = models.CharField(_('Bachs refund'), max_length=80, blank=True)
    status = models.CharField(_('status'), max_length=20, choices=STATUS, default='pending')
    card_label = models.CharField(_('paid with'), max_length=60, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('payment')
        verbose_name_plural = _('payments')
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return '{0} {1}'.format(self.amount_naira, self.get_status_display())

    @property
    def status_label(self):
        """What Billing History prints -- "Paid (Mastercard 1234)"."""
        if self.status == 'paid' and self.card_label:
            return 'Paid ({0})'.format(self.card_label)
        return self.get_status_display()


class PromoCode(models.Model):
    """A discount someone can type at checkout."""

    code = models.CharField(_('code'), max_length=40, unique=True)
    percent_off = models.PositiveSmallIntegerField(
        _('percent off'), null=True, blank=True,
    )
    amount_off_naira = models.PositiveIntegerField(
        _('amount off'), null=True, blank=True,
    )
    max_uses = models.PositiveIntegerField(
        _('maximum uses'), null=True, blank=True,
        help_text=_('Blank for no limit.'),
    )
    used_count = models.PositiveIntegerField(_('used'), default=0)
    expires_at = models.DateTimeField(_('expires'), null=True, blank=True)
    is_active = models.BooleanField(_('active'), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('code',)
        verbose_name = _('promo code')
        verbose_name_plural = _('promo codes')

    def __str__(self):
        return self.code

    @property
    def is_usable(self):
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        if self.max_uses is not None and self.used_count >= self.max_uses:
            return False
        return True

    def apply_to(self, naira):
        """The amount after this code. Never below zero, never above the
        original -- a discount that increases a bill is a bug, not a feature."""
        if not self.is_usable:
            return naira
        if self.percent_off:
            reduced = naira - (naira * self.percent_off // 100)
        elif self.amount_off_naira:
            reduced = naira - self.amount_off_naira
        else:
            reduced = naira
        return max(0, min(reduced, naira))
