"""The notification feed, and what each person wants to be told about.

The feed is deliberately heterogeneous -- the design draws a recommendation
card, social lines, a reminder pill, a weekly digest, a booking row and a
breathing-minute push, all in one list. Modelling the kind per entry is what
lets one endpoint serve all of them.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Kind(models.TextChoices):
    """Matches the app's NotificationKind enum, value for value."""

    RECOMMENDATION = 'recommendation', _('Recommendation card')
    SOCIAL = 'social', _('Someone did something on your post')
    REMINDER = 'reminder', _('Reminder')
    DIGEST = 'digest', _('Weekly digest')
    PLAIN = 'plain', _('Plain line')
    ACTION = 'action', _('Line with an action')
    BOOKING = 'booking', _('Booking')
    BREATHE = 'breathe', _('Breathing minute')


class Filter(models.TextChoices):
    """The chips above the feed. 'all' is implied and never stored."""

    SESSIONS = 'sessions', _('Sessions')
    WHATS_NEW = 'whats_new', _("What's new")


class Notification(models.Model):
    """One row in the feed.

    Composed text rather than a template id: the design writes each line in
    its own voice, and the app renders what it is given. `emphasis` is the run
    inside the body the design sets in bold -- the discipline in "You have a
    **Yoga** session on 28 Sep at 5:00pm".
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications',
    )
    kind = models.CharField(_('kind'), max_length=20, choices=Kind.choices)

    # Bold lead-in, then the rest of the line: "Kendrick commented on ...".
    actor = models.CharField(_('actor'), max_length=80, blank=True)
    text = models.CharField(_('text'), max_length=300, blank=True)

    title = models.CharField(_('title'), max_length=160, blank=True)
    body = models.TextField(_('body'), blank=True)
    emphasis = models.CharField(_('bold run'), max_length=80, blank=True)
    action_label = models.CharField(_('action'), max_length=40, blank=True)

    # Where tapping it goes -- an app route, not a URL, so the app decides how
    # to open it: 'breathe', 'community/12', 'session/8'.
    target = models.CharField(_('opens'), max_length=120, blank=True)

    # Comma-wrapped rather than JSON: the only query is "does this row carry
    # that chip", and a JSON contains lookup is unsupported on SQLite -- which
    # is what the cPanel deployment runs. Stored as ",sessions,whats_new," so
    # a LIKE for ",sessions," cannot match a chip that merely ends in it.
    filters = models.CharField(_('filters'), max_length=120, blank=True, default='')

    unread = models.BooleanField(_('unread'), default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('notification')
        verbose_name_plural = _('notifications')
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return self.title or self.text or self.get_kind_display()

    @property
    def filter_keys(self):
        return [key for key in self.filters.split(',') if key]

    def set_filters(self, keys):
        self.filters = ',{0},'.format(','.join(keys)) if keys else ''

    def save(self, *args, **kwargs):
        # Accepts a plain list as well as the stored form, so callers can say
        # filters=['sessions'] and not think about the commas.
        if self.filters and not self.filters.startswith(','):
            self.set_filters([k.strip() for k in self.filters.split(',') if k.strip()])
        super().save(*args, **kwargs)


class Preferences(models.Model):
    """The four switches on the Notifications settings screen.

    All four start off, which is what the design draws and the kinder default:
    nobody is opted into reminders by signing up. `offers` is marketing and is
    treated as consent -- never assume it.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='notification_preferences',
    )
    notify_balance = models.BooleanField(_('balance reminder'), default=False)
    notify_meditation = models.BooleanField(_('meditation reminder'), default=False)
    notify_mood = models.BooleanField(_('mood check-in reminder'), default=False)
    notify_offers = models.BooleanField(_('promotional offers'), default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('notification preferences')
        verbose_name_plural = _('notification preferences')

    def __str__(self):
        return 'preferences for {0}'.format(self.user.get_username())


class PushDevice(models.Model):
    """A phone that can be pushed to.

    One row per install, keyed by the FCM token. Kept here rather than in
    accounts because its only purpose is delivery, and a token that stops
    working should be deleted without touching the account.
    """

    PLATFORMS = (('android', 'Android'), ('ios', 'iOS'), ('web', 'Web'))

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='devices',
    )
    token = models.CharField(_('FCM token'), max_length=255, unique=True)
    platform = models.CharField(_('platform'), max_length=10, choices=PLATFORMS)
    is_active = models.BooleanField(_('active'), default=True)
    last_seen_at = models.DateTimeField(_('last seen'), default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-last_seen_at',)
        verbose_name = _('push device')
        verbose_name_plural = _('push devices')

    def __str__(self):
        return '{0} on {1}'.format(self.user.get_username(), self.platform)
