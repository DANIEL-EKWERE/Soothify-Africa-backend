"""The library: what people listen to, watch and read.

Shaped by the app's ContentRepository, which asks for eight things --
categories, featured, recent, recommendations, a named shelf, a shelf's full
page, a category's items, one item, and search. Everything here exists to
answer one of those.
"""

from django.conf import settings
from django.db import models
from django.db.models import Avg, Count
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class MediaType(models.TextChoices):
    """Matches the app's MediaType enum; the values are what it parses."""

    AUDIO = 'audio', _('Audio')
    VIDEO = 'video', _('Video')
    ARTICLE = 'article', _('Article')


class Category(models.Model):
    """Calm, Sleep, Focus, Breathwork, Stories.

    The id is the slug, not a number: the app's mock uses 'focus' and
    'breathe', its screens pass those straight back as `categoryId`, and a
    readable id is far easier to curate content against.
    """

    id = models.SlugField(_('id'), primary_key=True, max_length=40)
    name = models.CharField(_('name'), max_length=80)
    description = models.CharField(_('description'), max_length=200, blank=True)
    cover = models.ImageField(_('cover'), upload_to='categories/', blank=True)
    position = models.PositiveIntegerField(_('position'), default=0)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        ordering = ('position', 'name')
        verbose_name = _('category')
        verbose_name_plural = _('categories')

    def __str__(self):
        return self.name


class MediaQuerySet(models.QuerySet):
    def live(self):
        """Published, and not dated into the future."""
        return self.filter(is_published=True, published_at__lte=timezone.now())

    def search(self, term):
        """Title or practitioner, which is what the app's search matches on."""
        term = (term or '').strip()
        if not term:
            return self.none()
        return self.filter(
            models.Q(title__icontains=term)
            | models.Q(subtitle__icontains=term)
            | models.Q(credited_name__icontains=term)
            | models.Q(practitioner__full_name__icontains=term),
        )


class MediaItem(models.Model):
    """One piece of content. Audio, video and articles share this table.

    The app's own note: "audio and video share one entity and the player
    branches on type rather than the app carrying two parallel models".
    """

    title = models.CharField(_('title'), max_length=160)
    subtitle = models.CharField(_('subtitle'), max_length=160, blank=True)

    # The white pill over the cover art -- "Pilates & Core", "Voice over".
    # Per item and curated, not derived from the media type.
    tag = models.CharField(_('tag'), max_length=60, blank=True)
    description = models.TextField(_('description'), blank=True)

    media_type = models.CharField(
        _('type'), max_length=10, choices=MediaType.choices, default=MediaType.AUDIO,
    )
    duration_seconds = models.PositiveIntegerField(_('duration (seconds)'), default=0)

    cover = models.ImageField(_('cover art'), upload_to='covers/', blank=True)

    # Either an uploaded file or a URL elsewhere. Whichever is set, the app
    # only ever sees a short-lived signed link (see api/media.py).
    media_file = models.FileField(_('file'), upload_to='media/', blank=True)
    external_url = models.URLField(_('external URL'), blank=True)

    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='items', verbose_name=_('category'),
    )
    practitioner = models.ForeignKey(
        'accounts.Profile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='content', verbose_name=_('practitioner'),
    )
    # Used when the voice on a recording is not someone with an account here.
    credited_name = models.CharField(_('credited to'), max_length=120, blank=True)

    is_free = models.BooleanField(
        _('free'), default=True,
        help_text=_('Unticked, this needs a subscription to play.'),
    )
    is_featured = models.BooleanField(
        _('featured'), default=False,
        help_text=_("Shows in Home's 'Recommended for you' row."),
    )
    is_recommended = models.BooleanField(
        _('on the recommendations page'), default=False,
    )

    is_published = models.BooleanField(_('published'), default=True)
    published_at = models.DateTimeField(_('published at'), default=timezone.now)
    position = models.PositiveIntegerField(_('position'), default=0)

    # Kept on the row rather than averaged per request: a card shows the score
    # in a list of twenty, and an aggregate per card is a query per card.
    rating_average = models.FloatField(_('rating'), default=0)
    rating_count = models.PositiveIntegerField(_('ratings'), default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MediaQuerySet.as_manager()

    class Meta:
        ordering = ('position', '-published_at')
        verbose_name = _('media item')
        verbose_name_plural = _('media items')
        indexes = [
            models.Index(fields=['is_published', 'published_at']),
            models.Index(fields=['category', 'is_published']),
        ]

    def __str__(self):
        return self.title

    @property
    def length_label(self):
        """How long it runs, for a person: "45 min", "90 sec", or nothing.

        Articles have no duration, and a card that says "0 min" reads like a
        mistake rather than an absence.
        """
        if not self.duration_seconds:
            return ''
        if self.duration_seconds < 60:
            return '{0} sec'.format(self.duration_seconds)
        return '{0} min'.format(round(self.duration_seconds / 60))

    @property
    def practitioner_name(self):
        if self.practitioner_id and self.practitioner.full_name:
            return self.practitioner.full_name
        return self.credited_name

    def refresh_rating(self):
        """Recalculate from the rows. Called after a rating is written."""
        stats = self.ratings.aggregate(avg=Avg('score'), count=Count('id'))
        self.rating_average = round(stats['avg'] or 0, 2)
        self.rating_count = stats['count'] or 0
        self.save(update_fields=['rating_average', 'rating_count', 'updated_at'])


class Shelf(models.Model):
    """A named row -- 'voice-overs', 'sound-effects', 'yoga-flow', 'sounds'.

    The app fetches one by its key and expects that shelf's own slice: the
    design gives Voice Overs three cards and Sound Effects two different ones,
    so a shelf is a curated, ordered list rather than a filter over a category.
    """

    key = models.SlugField(_('key'), primary_key=True, max_length=60)
    title = models.CharField(_('title'), max_length=120)
    description = models.CharField(_('description'), max_length=200, blank=True)

    # How many the row previews before "See All"; the full page returns all.
    preview_limit = models.PositiveSmallIntegerField(_('cards in the row'), default=3)
    position = models.PositiveIntegerField(_('position'), default=0)
    is_active = models.BooleanField(_('active'), default=True)

    items = models.ManyToManyField(
        MediaItem, through='ShelfItem', related_name='shelves', verbose_name=_('items'),
    )

    class Meta:
        ordering = ('position', 'title')
        verbose_name = _('shelf')
        verbose_name_plural = _('shelves')

    def __str__(self):
        return self.title

    def ordered_items(self):
        return MediaItem.objects.live().filter(
            shelfitem__shelf=self,
        ).order_by('shelfitem__position')


class ShelfItem(models.Model):
    """One card's place on one shelf.

    Ordered explicitly because the design lays covers out a/b/a -- the order
    is a layout decision, not an alphabetical or chronological one.
    """

    shelf = models.ForeignKey(Shelf, on_delete=models.CASCADE)
    item = models.ForeignKey(MediaItem, on_delete=models.CASCADE)
    position = models.PositiveIntegerField(_('position'), default=0)

    class Meta:
        ordering = ('position',)
        unique_together = ('shelf', 'item')
        verbose_name = _('shelf item')
        verbose_name_plural = _('shelf items')

    def __str__(self):
        return '{0} on {1}'.format(self.item, self.shelf)


class PlaybackEvent(models.Model):
    """That someone played something, and how far they got.

    Two jobs: the Discovery "Recent" shelf, and the minutes on the Profile
    stats card. Kept as events rather than a single "last played" column so
    the stats can be counted over a period without inventing history.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='playbacks',
    )
    item = models.ForeignKey(
        MediaItem, on_delete=models.CASCADE, related_name='playbacks',
    )
    seconds_played = models.PositiveIntegerField(_('seconds played'), default=0)
    completed = models.BooleanField(_('finished it'), default=False)
    played_at = models.DateTimeField(_('played at'), default=timezone.now)

    class Meta:
        ordering = ('-played_at',)
        verbose_name = _('playback')
        verbose_name_plural = _('playbacks')
        indexes = [models.Index(fields=['user', '-played_at'])]

    def __str__(self):
        return '{0} played {1}'.format(self.user.get_username(), self.item)


class MediaRating(models.Model):
    """One person's score for one item, out of 5.

    Held per person so a score can be changed rather than counted twice, which
    a bare running average on the item could not do.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ratings',
    )
    item = models.ForeignKey(
        MediaItem, on_delete=models.CASCADE, related_name='ratings',
    )
    score = models.PositiveSmallIntegerField(_('score'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'item')
        verbose_name = _('rating')
        verbose_name_plural = _('ratings')

    def __str__(self):
        return '{0}/5 for {1}'.format(self.score, self.item)
