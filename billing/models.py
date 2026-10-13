"""Memberships, the free trial, session payments and invoices.

Money is whole naira throughout, as everywhere else in this project. Bachs
takes decimal strings ("150000.00"), so the conversion happens at its edge
(billing/bachs.py) and nowhere else.

The shape follows the app's Manage-subscription screens: a membership is
either inactive, on trial, or active; it renews monthly or annually; and
Billing History lists what was charged.
"""

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

    # Bachs' handles. No card detail reaches this server: Bachs holds the
    # saved method and runs the billing cycle, including the charge when a
    # trial ends. The brand and last four digits are only so the screen can
    # say "Mastercard ending in 1234".
    customer_id = models.CharField(_('Bachs customer'), max_length=80, blank=True)
    subscription_id = models.CharField(_('Bachs subscription'), max_length=80, blank=True)
    card_brand = models.CharField(_('card'), max_length=40, blank=True)
    card_last4 = models.CharField(_('last four'), max_length=4, blank=True)

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
    def card_label(self):
        if not self.card_last4:
            return ''
        return '{0} ending in {1}'.format(self.card_brand or 'Card', self.card_last4)

    @property
    def next_charge_naira(self):
        if self.plan is None or self.plan.price_naira is None:
            return 0
        return self.plan.price_naira

    def start_trial(self, plan, cadence, trial_ends_at=None, subscription_id=''):
        """Record a trial that Bachs has started.

        `trial_ends_at` comes from the subscription they created. Falling
        back to our own clock would eventually disagree with the date they
        actually charge on, and the screen promises a date.
        """
        self.plan = plan
        self.cadence = cadence
        self.state = 'trial'
        self.trial_ends_at = trial_ends_at or (
            timezone.now() + timezone.timedelta(days=self.TRIAL_DAYS)
        )
        self.renews_at = self.trial_ends_at
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
        self.save(update_fields=['renews_at', 'state', 'trial_ends_at', 'updated_at'])

    def cancel(self):
        """Stop renewing. Access runs to the end of what was paid for."""
        self.state = 'cancelled'
        self.cancelled_at = timezone.now()
        self.save(update_fields=['state', 'cancelled_at', 'updated_at'])


class Payment(models.Model):
    """One attempt to take money, for a membership or for a session.

    Kept even when it fails: a failed charge is why a membership went past
    due, and somebody will ask.
    """

    KINDS = (
        ('membership', _('Membership')),
        ('session', _('Session')),
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
