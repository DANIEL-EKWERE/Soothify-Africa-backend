"""Practitioners, and the sessions people book with them.

Two sides of one thing: a client picks a discipline, is matched with an
expert, chooses a day and a time, pays, and later joins a call. The expert
sees the same session from the other side, and is paid for it.

Money is whole naira. The app prices sessions at 10,000 and 25,000 and shows
no kobo anywhere, so storing minor units would invent precision the product
does not have.
"""

import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
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


def _in_words(choices, keys):
    """Stored keys as the labels a person reads, in the order given."""
    labels = dict(choices)
    return ', '.join(str(labels.get(key, key)) for key in (keys or []))


def application_document_path(instance, filename):
    """Where a certificate or an ID is stored.

    Under `private/`, which is the one prefix a web server can be told to
    refuse in a single rule -- these are the most sensitive files the system
    holds, and the only way they are meant to be read is a signed link
    (api/media.py) issued to staff.

    The name carries eight random characters as well as the application's
    id: ids are sequential, and a guessable path is the whole risk if that
    prefix is ever exposed by a misconfiguration.
    """
    stem = secrets.token_urlsafe(6)
    return 'private/expert-applications/{0}/{1}-{2}'.format(
        instance.application_id, stem, filename,
    )


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

    SOURCES = (
        ('app', _('From the app')),
        ('web', _('From the website')),
    )

    # Null for an application sent from the website: a practitioner should
    # not have to make a client account before asking to practise, and the
    # account is created when the application is approved.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='expert_applications', null=True, blank=True,
    )
    source = models.CharField(
        _('came from'), max_length=10, choices=SOURCES, default='app',
    )

    full_name = models.CharField(_('full name'), max_length=120)

    # How to reach them. Taken from the account for an application sent from
    # the app, and asked for on the website, where there is no account to
    # take it from.
    # Blank only so the column could be added to rows that predate it --
    # those are backfilled from their account in the same migration. Both
    # ways in fill it: the API from the account, the website from the form.
    email = models.EmailField(_('email'), blank=True)
    phone = models.CharField(_('phone'), max_length=40, blank=True)

    # More than one, because people are: a licensed therapist who also
    # teaches yoga is ordinary here, and the form asks for "areas of
    # expertise". Held as a list of Discipline values, like `languages`.
    disciplines = models.JSONField(_('practises'), default=list)

    languages = models.JSONField(_('languages'), default=list)
    experience_years = models.PositiveSmallIntegerField(_('years practising'), default=0)
    bio = models.TextField(_('about'), blank=True)

    # The rest of what the form asks for. All optional: an application
    # missing a portfolio can still be reviewed, and one missing a licence
    # number has the certificate attached instead.
    portfolio_url = models.URLField(_('portfolio or website'), blank=True)
    licence_number = models.CharField(
        _('licence number'), max_length=80, blank=True,
    )
    insurance_provider = models.CharField(
        _('insurance'), max_length=120, blank=True,
    )

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
        indexes = [models.Index(fields=['email'])]

    def __str__(self):
        return '{0} ({1})'.format(self.full_name, self.get_status_display())

    def approve(self, reviewer=None, notes=''):
        """Take them on.

        Three things have to be true afterwards: there is an account, it is
        a practitioner's, and there is an ExpertProfile for clients to be
        matched with. An application from the website has no account yet, so
        one is made -- with no usable password, because nobody has chosen it.
        Setting one is the applicant's own errand, through the ordinary
        password reset.

        Returns (profile, was_invited). `was_invited` is True when an
        account was created here, which is what tells the caller to send
        them a way in.
        """
        from accounts.models import Profile, Role

        invited = False
        user = self.user
        if user is None:
            model = get_user_model()
            user = model.objects.filter(email__iexact=self.email).first()
            if user is None:
                user = model.objects.create_user(
                    username=self.email[:150], email=self.email,
                )
                # No password at all: one that nobody chose is one that
                # somebody else might guess.
                user.set_unusable_password()
                user.save(update_fields=['password'])
                invited = True
            self.user = user

        profile = getattr(user, 'profile', None) or Profile.objects.create(
            user=user, full_name=self.full_name,
        )
        profile.role = Role.PRACTITIONER
        if not profile.full_name:
            profile.full_name = self.full_name
        profile.save(update_fields=['role', 'full_name'])

        ExpertProfile.objects.get_or_create(
            profile=profile,
            defaults={
                'disciplines': list(self.disciplines or []),
                'languages': list(self.languages or []),
                'years_experience': self.experience_years,
                'bio': self.bio,
            },
        )

        self.status = 'approved'
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        if notes:
            self.review_notes = notes
        self.save(update_fields=[
            'user', 'status', 'reviewed_by', 'reviewed_at', 'review_notes',
        ])
        return profile, invited

    def reject(self, reviewer=None, notes=''):
        """Turn it down, with the reason kept for us rather than for them.

        The applicant is written to separately and in our own words: review
        notes are candid, and candour written for colleagues does not belong
        in somebody's inbox.
        """
        self.status = 'rejected'
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        if notes:
            self.review_notes = notes
        self.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'review_notes',
        ])

    @property
    def disciplines_display(self):
        """What they practise, in words, for the dashboard and the admin."""
        return _in_words(Discipline.choices, self.disciplines)

    @property
    def languages_display(self):
        """The languages, in words. "english, pidgin" is a storage detail."""
        return _in_words(SessionLanguage.choices, self.languages)


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

    Only the last four digits of an account are kept: the full number goes
    to Bachs once, to make a payout destination, and "Bank ****1234" is all
    any screen ever shows.
    """

    KINDS = (
        ('bank', _('Bank Transfer')),
        ('card', _('Card')),
        ('mobile', _('Mobile Money (USSD)')),
    )

    expert = models.ForeignKey(
        ExpertProfile, on_delete=models.CASCADE, related_name='payout_methods',
    )
    kind = models.CharField(_('kind'), max_length=10, choices=KINDS, default='bank')
    bank_name = models.CharField(_('bank'), max_length=80, blank=True)
    account_name = models.CharField(_('account name'), max_length=120, blank=True)
    last4 = models.CharField(_('last four digits'), max_length=4, blank=True)

    # Bachs' handle for the destination, from create_payout_destination. The
    # account number itself is sent once, to them, and never stored here.
    destination_id = models.CharField(
        _('Bachs destination'), max_length=80, blank=True,
    )

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


class Recommendation(models.Model):
    """A piece of the library a practitioner pointed their client at.

    Left during a call or written up with the session notes afterwards, and
    read in the client's Journal under its second tab.

    Deliberately not a SessionNote. That is the practitioner's own clinical
    record and is never served to the client; this is the opposite -- it is
    written *for* them, and it is the only thing from a session they see.

    It carries a MediaItem rather than a title somebody typed, so the card
    opens something when it is tapped. A recommendation whose item is later
    unpublished stops being offered rather than becoming a dead link.
    """

    expert = models.ForeignKey(
        ExpertProfile, on_delete=models.CASCADE, related_name='recommendations',
        verbose_name=_('from'),
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='recommendations', verbose_name=_('for'),
    )
    #: The session it came out of, where there was one. Null for something
    #: sent between sessions, which the app allows.
    session = models.ForeignKey(
        Session, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recommendations',
    )
    item = models.ForeignKey(
        'content.MediaItem', on_delete=models.CASCADE,
        related_name='recommendations', verbose_name=_('content'),
    )
    note = models.TextField(_("expert's note"), blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    #: When the client opened it. Null is the red "New" pill on the card and
    #: the count on the tab.
    read_at = models.DateTimeField(_('read'), null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('recommendation')
        verbose_name_plural = _('recommendations')
        indexes = [models.Index(fields=['client', '-created_at'])]

    def __str__(self):
        return '{0} -> {1}'.format(self.expert, self.item)

    @property
    def is_new(self):
        return self.read_at is None

    def mark_read(self):
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=['read_at'])

    @property
    def session_label(self):
        """"Therapy 1-on-1 Therapy" -- the service, then the format.

        Built here rather than in the app so the two cannot word it
        differently, and empty when it came between sessions.
        """
        if self.session_id is None:
            return ''
        return self.session.get_discipline_display()
