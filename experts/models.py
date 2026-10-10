"""Practitioners, and the sessions people book with them.

Two sides of one thing: a client picks a discipline, is matched with an
expert, chooses a day and a time, pays, and later joins a call. The expert
sees the same session from the other side, and is paid for it.

Money is whole naira. The app prices sessions at 10,000 and 25,000 and shows
no kobo anywhere, so storing minor units would invent precision the product
does not have.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Discipline(models.TextChoices):
    """What an expert is booked for -- the app's ExpertSessionType."""

    THERAPY = 'therapy', _('Therapy')
    YOGA = 'yoga', _('Yoga')
    PILATES = 'pilates', _('Pilates')


class SessionLanguage(models.TextChoices):
    """The two the app itself offers, which is what it promises clients."""

    ENGLISH = 'english', _('English')
    PIDGIN = 'pidgin', _('Pidgin English')


class ExpertProfile(models.Model):
    """Someone who takes sessions.

    Separate from accounts.Profile: that is every account, this is the handful
    who practise, and it only exists once an application is approved.
    """

    profile = models.OneToOneField(
        'accounts.Profile', on_delete=models.CASCADE, related_name='expert',
    )
    headline = models.CharField(
        _('headline'), max_length=160, blank=True,
        help_text=_('"We believe that fitness is part of your journey."'),
    )
    bio = models.TextField(_('about'), blank=True)
    quote = models.CharField(_('quote'), max_length=200, blank=True)

    disciplines = models.JSONField(_('disciplines'), default=list)
    languages = models.JSONField(_('languages'), default=list)
    interests = models.JSONField(_('interests'), default=list, blank=True)

    years_experience = models.PositiveSmallIntegerField(_('years practising'), default=0)

    # Whether new bookings may be matched to them. An expert on leave stays
    # listed to their existing clients but is not handed anyone new.
    is_accepting = models.BooleanField(_('taking new clients'), default=True)
    is_published = models.BooleanField(_('listed'), default=True)

    rating_average = models.FloatField(_('rating'), default=0)
    rating_count = models.PositiveIntegerField(_('ratings'), default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-rating_average', 'profile__full_name')
        verbose_name = _('expert')
        verbose_name_plural = _('experts')

    def __str__(self):
        return self.profile.full_name or self.profile.user.get_username()

    @property
    def display_name(self):
        return self.profile.full_name

    def practises(self, discipline):
        return discipline in (self.disciplines or [])

    def refresh_rating(self):
        from django.db.models import Avg, Count

        rated = self.sessions.filter(rating__isnull=False)
        stats = rated.aggregate(avg=Avg('rating'), count=Count('id'))
        self.rating_average = round(stats['avg'] or 0, 2)
        self.rating_count = stats['count'] or 0
        self.save(update_fields=['rating_average', 'rating_count'])


def application_document_path(instance, filename):
    return 'expert-applications/{0}/{1}'.format(instance.application_id, filename)


class Application(models.Model):
    """Someone asking to practise here.

    The documents are a practising certificate and a government ID. They are
    the most sensitive files this system holds, so they are stored privately
    and only ever served through a short-lived signed link to staff.
    """

    STATUS = (
        ('submitted', _('Submitted')),
        ('reviewing', _('Being reviewed')),
        ('approved', _('Approved')),
        ('rejected', _('Rejected')),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='expert_applications',
    )
    full_name = models.CharField(_('full name'), max_length=120)
    field = models.CharField(_('practises'), max_length=20, choices=Discipline.choices)
    languages = models.JSONField(_('languages'), default=list)
    experience_years = models.PositiveSmallIntegerField(_('years practising'), default=0)
    bio = models.TextField(_('about'), blank=True)

    status = models.CharField(_('status'), max_length=20, choices=STATUS, default='submitted')
    review_notes = models.TextField(
        _('review notes'), blank=True,
        help_text=_('Why it was approved or turned down. The applicant never sees this.'),
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_applications',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('expert application')
        verbose_name_plural = _('expert applications')

    def __str__(self):
        return '{0} ({1})'.format(self.full_name, self.get_status_display())


class ApplicationDocument(models.Model):
    """A certificate or an ID attached to an application."""

    KINDS = (
        ('certification', _('Certification, degree or licence')),
        ('identity', _('Government-issued photo ID')),
    )

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name='documents',
    )
    kind = models.CharField(_('kind'), max_length=20, choices=KINDS)
    file = models.FileField(_('file'), upload_to=application_document_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('application', 'kind')
        verbose_name = _('application document')
        verbose_name_plural = _('application documents')

    def __str__(self):
        return self.get_kind_display()


class AvailabilitySlot(models.Model):
    """One window an expert can be booked in.

    Times are minutes from midnight, as the app models them, so a slot sorts
    and compares without a date attached and a timezone cannot shift it.
    """

    expert = models.ForeignKey(
        ExpertProfile, on_delete=models.CASCADE, related_name='availability',
    )
    day = models.DateField(_('day'))
    start_minute = models.PositiveSmallIntegerField(
        _('from'), validators=[MinValueValidator(0), MaxValueValidator(1440)],
    )
    end_minute = models.PositiveSmallIntegerField(
        _('to'), validators=[MinValueValidator(0), MaxValueValidator(1440)],
    )

    class Meta:
        ordering = ('day', 'start_minute')
        unique_together = ('expert', 'day', 'start_minute')
        verbose_name = _('availability slot')
        verbose_name_plural = _('availability')
        indexes = [models.Index(fields=['expert', 'day'])]

    def __str__(self):
        return '{0} {1}-{2}'.format(self.day, self.start_minute, self.end_minute)

    @property
    def is_past(self):
        return self.day < timezone.localdate()


class Session(models.Model):
    """A booked session, from both sides.

    The order the app now follows: pick a discipline, get matched, choose the
    day and time, then pay. So a session exists before it is paid for, which
    is what `pending` means -- and an unpaid session holds the slot only
    briefly (see `expires_at`), or nobody else could ever book that hour.
    """

    STATUS = (
        ('pending', _('Awaiting payment')),
        ('confirmed', _('Confirmed')),
        ('completed', _('Completed')),
        ('cancelled', _('Cancelled')),
        ('no_show', _('Nobody joined')),
    )
    MODES = (('phone', _('Phone call')), ('video', _('Video call')))
    PLANS = (('single', _('One session')), ('monthly', _('Monthly, unlimited')))

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sessions',
    )
    expert = models.ForeignKey(
        ExpertProfile, on_delete=models.PROTECT, related_name='sessions',
    )
    discipline = models.CharField(
        _('discipline'), max_length=20, choices=Discipline.choices,
    )

    starts_at = models.DateTimeField(_('starts'))
    ends_at = models.DateTimeField(_('ends'))
    mode = models.CharField(_('taken by'), max_length=10, choices=MODES, default='video')

    plan = models.CharField(_('plan'), max_length=10, choices=PLANS, default='single')
    price_naira = models.PositiveIntegerField(_('price'), default=0)

    status = models.CharField(_('status'), max_length=20, choices=STATUS, default='pending')
    # An unpaid booking must not hold a slot forever.
    expires_at = models.DateTimeField(_('hold expires'), null=True, blank=True)

    rating = models.PositiveSmallIntegerField(
        _('rating'), null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    feedback = models.TextField(_('feedback'), blank=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cancelled_sessions',
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('starts_at',)
        verbose_name = _('session')
        verbose_name_plural = _('sessions')
        indexes = [
            models.Index(fields=['expert', 'starts_at']),
            models.Index(fields=['client', 'starts_at']),
            models.Index(fields=['status', 'starts_at']),
        ]

    def __str__(self):
        return '{0} with {1}'.format(self.get_discipline_display(), self.expert)

    @property
    def is_upcoming(self):
        return self.status == 'confirmed' and self.starts_at >= timezone.now()

    @property
    def room_id(self):
        """What both sides join. Derived, so it cannot drift from the session."""
        return 'session-{0}'.format(self.pk)

    def mark_paid(self):
        self.status = 'confirmed'
        self.expires_at = None
        self.save(update_fields=['status', 'expires_at'])

    def cancel(self, by=None):
        self.status = 'cancelled'
        self.cancelled_at = timezone.now()
        self.cancelled_by = by
        self.save(update_fields=['status', 'cancelled_at', 'cancelled_by'])


class SessionNote(models.Model):
    """What the practitioner wrote after a session.

    Clinical notes: the expert's own record, never served to the client. A
    client asking for their data gets their mood entries and journal, not
    somebody else's professional notes about them.
    """

    session = models.OneToOneField(
        Session, on_delete=models.CASCADE, related_name='note',
    )
    body = models.TextField(_('note'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('session note')
        verbose_name_plural = _('session notes')

    def __str__(self):
        return 'note on {0}'.format(self.session)


class PayoutMethod(models.Model):
    """Where an expert is paid.

    Only the last four digits of an account are kept: the full number is
    Paystack's to hold, and "Bank ****1234" is all the screen ever shows.
    """

    KINDS = (
        ('bank', _('Bank Transfer')),
        ('card', _('Paystack Card')),
        ('mobile', _('Mobile Money (USSD)')),
    )

    expert = models.ForeignKey(
        ExpertProfile, on_delete=models.CASCADE, related_name='payout_methods',
    )
    kind = models.CharField(_('kind'), max_length=10, choices=KINDS, default='bank')
    bank_name = models.CharField(_('bank'), max_length=80, blank=True)
    account_name = models.CharField(_('account name'), max_length=120, blank=True)
    last4 = models.CharField(_('last four digits'), max_length=4, blank=True)

    # Paystack's handle for the destination. The account number itself is sent
    # once, to them, and never stored here.
    recipient_code = models.CharField(_('Paystack recipient'), max_length=80, blank=True)

    is_default = models.BooleanField(_('default'), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-is_default', '-created_at')
        verbose_name = _('payout method')
        verbose_name_plural = _('payout methods')

    def __str__(self):
        if self.kind == 'bank' and self.last4:
            return '{0} ****{1}'.format(self.bank_name or 'Bank', self.last4)
        return self.get_kind_display()


class Payout(models.Model):
    """Money sent to an expert."""

    STATUS = (
        ('pending', _('Pending')),
        ('sent', _('Sent')),
        ('failed', _('Failed')),
    )

    expert = models.ForeignKey(
        ExpertProfile, on_delete=models.PROTECT, related_name='payouts',
    )
    amount_naira = models.PositiveIntegerField(_('amount'))
    label = models.CharField(_('label'), max_length=80, default='Weekly payout')
    method = models.ForeignKey(
        PayoutMethod, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payouts',
    )
    status = models.CharField(_('status'), max_length=10, choices=STATUS, default='pending')
    reference = models.CharField(_('reference'), max_length=80, blank=True)
    paid_on = models.DateField(_('paid on'), null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('payout')
        verbose_name_plural = _('payouts')

    def __str__(self):
        return '{0} to {1}'.format(self.amount_naira, self.expert)


def earnings_for(expert, now=None):
    """What the Earnings screen shows.

    Counted from completed sessions and sent payouts rather than held as a
    balance, so the figures cannot drift from what actually happened.
    """
    now = now or timezone.now()
    week_start = now - timezone.timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    done = expert.sessions.filter(status='completed')
    this_week = done.filter(starts_at__gte=week_start)

    total_earned = done.aggregate(total=Sum('price_naira'))['total'] or 0
    paid_out = expert.payouts.filter(status='sent').aggregate(
        total=Sum('amount_naira'),
    )['total'] or 0

    return {
        'available': max(total_earned - paid_out, 0),
        'this_week': this_week.aggregate(total=Sum('price_naira'))['total'] or 0,
        'this_week_sessions': this_week.count(),
        'total_earned': total_earned,
        'total_sessions': done.count(),
        'next_payout': _next_friday(now).date(),
        'payout_method': str(
            expert.payout_methods.filter(is_default=True).first() or '',
        ),
    }


def _next_friday(now):
    """Payouts run weekly on a Friday, which is what the design prints."""
    ahead = (4 - now.weekday()) % 7 or 7
    return now + timezone.timedelta(days=ahead)
