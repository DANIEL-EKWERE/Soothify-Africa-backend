"""Physical places: the studios behind "Spaces Around Me", and the Passport
waitlist that sells access to them.

The app shows these as a searchable list and as pins on a map, and the
Passport plan card has no price yet -- it collects an email instead. Both
screens currently run on sample data held in the app and an email saved to the
phone, so nothing a visitor types today actually reaches anyone.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SpaceCategory(models.TextChoices):
    """The filter chips. Values match the app's SpaceCategory enum keys."""

    PILATES = 'pilates', _('Pilates Studios')
    YOGA = 'yoga', _('Yoga Sanctuaries')
    SPA = 'spa', _('Wellness Spas')


class WellnessSpace(models.Model):
    """One studio, gym or spa a member can visit."""

    id = models.SlugField(_('id'), primary_key=True, max_length=60)
    name = models.CharField(_('name'), max_length=120)

    # "Lekki Phase 1" on a card; the profile prints "Maitama, Abuja".
    area = models.CharField(_('area'), max_length=120)
    city = models.CharField(_('city'), max_length=80, blank=True)

    category = models.CharField(
        _('category'), max_length=20, choices=SpaceCategory.choices,
    )
    # The grey pills under the name -- "Reformer Pilates", "Mat Pilates".
    tags = models.JSONField(_('tags'), default=list, blank=True)

    about = models.TextField(_('about'), blank=True)
    photo = models.ImageField(_('photo'), upload_to='spaces/', blank=True)

    instagram = models.CharField(_('Instagram'), max_length=120, blank=True)
    phone = models.CharField(_('phone'), max_length=40, blank=True)

    # Real coordinates, not the design's fractional pin: the frame ships a
    # flat image with a pin placed on it, which cannot survive a real map or
    # tell anyone how far away a studio is.
    latitude = models.FloatField(
        _('latitude'), null=True, blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.FloatField(
        _('longitude'), null=True, blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )

    is_active = models.BooleanField(_('listed'), default=True)
    position = models.PositiveIntegerField(_('position'), default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('position', 'name')
        verbose_name = _('wellness space')
        verbose_name_plural = _('wellness spaces')
        indexes = [models.Index(fields=['is_active', 'category'])]

    def __str__(self):
        return self.name


class PassportWaitlistEntry(models.Model):
    """Someone asking to be told when the Passport pass opens.

    The Passport card sells no price yet; it collects an address. Separate
    from the website's own waitlist (home.WaitlistSignup) on purpose -- that
    one is for the product launch, this one is for a plan that does not exist
    yet, and mailing the wrong list would be a bad surprise for both.
    """

    email = models.EmailField(_('email'), unique=True)

    # Set when a signed-in member asks; blank when the sheet is used by
    # someone browsing without an account, which the app allows.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='passport_interest',
    )
    city = models.CharField(_('city'), max_length=80, blank=True)
    source = models.CharField(
        _('asked from'), max_length=40, blank=True,
        help_text=_('Which screen the sheet was opened from.'),
    )
    notified_at = models.DateTimeField(_('told about launch'), null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('Passport waitlist entry')
        verbose_name_plural = _('Passport waitlist')

    def __str__(self):
        return self.email
