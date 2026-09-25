import secrets
import string

from django.db import models
from django.utils.translation import gettext_lazy as _

# Each friend who joins through someone's link moves them this many places up.
REFERRAL_BOOST = 10

_CODE_ALPHABET = string.ascii_lowercase + string.digits


def new_referral_code():
    return ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


class WaitlistSignup(models.Model):
    """Someone who asked for early access from the landing page."""

    # The stored values are short and stable; the labels are what the form and
    # the admin show, so relabelling later does not rewrite collected rows.
    class Goal(models.TextChoices):
        STRESS = 'stress', _('Unwinding & reducing daily stress')
        SLEEP = 'sleep', _('Falling asleep easier & deep rest')
        ROUTINES = 'routines', _('Building consistent wellness routines')
        MINDFULNESS = 'mindfulness', _('Finding inner calm & mindfulness')
        CALM = 'calm', _('Feeling more calm')
        OTHER = 'other', _('Other (Please specify)')

    class Interest(models.TextChoices):
        MEDITATION = 'meditation', _('Guided meditation & breathwork')
        SLEEP_SOUNDS = 'sleep_sounds', _('Sleep sounds & soundscapes')
        YOGA = 'yoga', _('Gentle yoga & movement')
        COURSES = 'courses', _('Wellness & mindfulness courses')
        COMMUNITY = 'community', _('Community support & circles')
        OTHER = 'other', _('Other (Please specify)')

    class Source(models.TextChoices):
        SOCIAL = 'social', _('Social Media')
        FRIEND = 'friend', _('Friend/Family')
        SEARCH = 'search', _('Online Search')
        ADS = 'ads', _('Ads')
        OTHER = 'other', _('Other')

    name = models.CharField(_('name'), max_length=120)
    # One spot per address: signing up again updates the same row.
    email = models.EmailField(_('email address'), unique=True)
    phone = models.CharField(_('phone number'), max_length=32, blank=True)

    goals = models.JSONField(_('what brings them'), default=list, blank=True)
    goals_other = models.CharField(_('other reason'), max_length=200, blank=True)
    interests = models.JSONField(_('experiences they want'), default=list, blank=True)
    interests_other = models.CharField(_('other experience'), max_length=200, blank=True)
    source = models.CharField(
        _('where they heard about us'), max_length=20, choices=Source.choices, blank=True,
    )
    wants_updates = models.BooleanField(_('wants updates'), default=False)

    # Which language the page was in when they signed up -- who to write to in Pidgin.
    language = models.CharField(_('language'), max_length=10, blank=True)

    referral_code = models.CharField(
        _('referral code'), max_length=12, unique=True, editable=False, default=new_referral_code,
    )
    referred_by = models.ForeignKey(
        'self', verbose_name=_('referred by'), null=True, blank=True,
        on_delete=models.SET_NULL, related_name='referrals',
    )
    created_at = models.DateTimeField(_('joined'), auto_now_add=True)

    class Meta:
        verbose_name = _('waitlist signup')
        verbose_name_plural = _('waitlist signups')
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.name} <{self.email}>'

    def position(self):
        """Place in line: order of joining, moved up REFERRAL_BOOST places for
        every friend who joined through this person's link."""
        joined_before = WaitlistSignup.objects.filter(pk__lt=self.pk).count()
        return max(1, joined_before + 1 - REFERRAL_BOOST * self.referrals.count())

    def goal_labels(self):
        return [self.Goal(v).label for v in self.goals if v in self.Goal.values]

    def interest_labels(self):
        return [self.Interest(v).label for v in self.interests if v in self.Interest.values]
