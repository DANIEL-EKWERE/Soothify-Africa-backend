"""What someone records about themselves: how they feel, and what they write.

This is the most private data the product holds. It is never shown to another
user, never carries a display name, and every query in this app is scoped to
the person who owns the row.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class MoodEntryQuerySet(models.QuerySet):
    def for_day(self, user, day):
        return self.filter(user=user, recorded_on=day)


class MoodEntry(models.Model):
    """One check-in.

    The score is the slider's raw position, 0 (Awful) to 1 (Awesome), exactly
    as the app stores it -- "so the scale can gain steps later without
    rewriting anyone's history". The named level is derived from it, never
    stored.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='moods',
    )
    score = models.FloatField(_('score'))
    note = models.TextField(_('note'), blank=True)

    # The day this belongs to, in the user's own reckoning. Held separately
    # from the timestamp because "one entry per day" is a rule about days, and
    # comparing timestamps to find today's entry gets it wrong around midnight
    # and across time zones.
    recorded_on = models.DateField(_('day'), default=timezone.localdate)
    recorded_at = models.DateTimeField(_('recorded at'), default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MoodEntryQuerySet.as_manager()

    class Meta:
        ordering = ('-recorded_at',)
        verbose_name = _('mood entry')
        verbose_name_plural = _('mood entries')
        # One check-in per person per day: re-checking replaces today's rather
        # than stacking, which is what LocalMoodRepository.record does.
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'recorded_on'], name='one_mood_per_day',
            ),
        ]
        indexes = [models.Index(fields=['user', '-recorded_on'])]

    def __str__(self):
        return '{0} on {1}'.format(self.level_key, self.recorded_on)

    # The ten named stops, in the app's order. The key is what the API sends;
    # the app has its own labels and copy for each and does not need ours.
    LEVELS = (
        'awful', 'drained', 'anxious', 'low', 'neutral',
        'reflective', 'content', 'energized', 'joyful', 'awesome',
    )

    @property
    def level_key(self):
        """Which of the ten bands the score falls in.

        Ten even bands, matching MoodLevel.forScore -- the same arithmetic, so
        a score means the same thing on both sides.
        """
        position = min(max(self.score, 0.0), 1.0)
        index = int(position * len(self.LEVELS))
        return self.LEVELS[min(index, len(self.LEVELS) - 1)]


class JournalEntry(models.Model):
    """One note.

    The composer has no title field -- the heading a card shows is the note's
    own opening line. The app computes that with `JournalEntry.titleFrom`, and
    sends it, so the same heading survives a reinstall. When it sends nothing,
    this works it out the same way.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='journal',
    )
    title = models.CharField(_('title'), max_length=120, blank=True)
    body = models.TextField(_('note'))

    # What the composer offered, when it was written under one. Kept so the
    # app can show "you wrote this about ..." later; nothing reads it yet.
    prompt = models.CharField(_('prompt'), max_length=200, blank=True)

    created_at = models.DateTimeField(_('written at'), default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('journal entry')
        verbose_name_plural = _('journal entries')
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return self.title or self.body[:40]

    TITLE_LIMIT = 48

    @classmethod
    def title_from(cls, body):
        """The note's first non-empty line, cut on a word boundary.

        Mirrors JournalEntry.titleFrom in the app, so a note saved from an
        older build and one saved today get the same heading.
        """
        lines = [line.strip() for line in (body or '').split('\n')]
        first = next((line for line in lines if line), '')
        if len(first) <= cls.TITLE_LIMIT:
            return first
        cut = first.rfind(' ', 0, cls.TITLE_LIMIT)
        end = cut if cut > 20 else cls.TITLE_LIMIT
        return first[:end].rstrip() + '…'

    def save(self, *args, **kwargs):
        if not self.title:
            self.title = self.title_from(self.body)
        super().save(*args, **kwargs)
