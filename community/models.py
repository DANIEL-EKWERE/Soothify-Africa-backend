"""The forum.

People write here about depression, burnout and worse, under a handle they
choose rather than their account name. Two consequences run through this app:
nothing exposes the author's real identity, and timestamps are served whole
but rendered coarsely by the app ("1 hour ago") because, as it puts it, "a
precise timestamp on a mental-health post is more identifying than it is
useful".
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Discussion(models.Model):
    """A thread."""

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='discussions',
    )
    # The handle as it stood when the post was made. Copied rather than looked
    # up so that changing your handle does not rewrite what you said last year
    # under the old one -- and so a deleted account's posts do not break.
    author_handle = models.CharField(_('posted as'), max_length=40)

    title = models.CharField(_('title'), max_length=140)
    body = models.TextField(_('body'))

    # Counters kept on the row: the forum list shows them on every card, and a
    # count per card is a query per card.
    like_count = models.PositiveIntegerField(_('likes'), default=0)
    comment_count = models.PositiveIntegerField(_('comments'), default=0)
    share_count = models.PositiveIntegerField(_('shares'), default=0)

    is_removed = models.BooleanField(
        _('removed'), default=False,
        help_text=_('Hidden from the forum. The row is kept for moderation.'),
    )

    created_at = models.DateTimeField(_('posted at'), default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('discussion')
        verbose_name_plural = _('discussions')
        indexes = [models.Index(fields=['is_removed', '-created_at'])]

    def __str__(self):
        return self.title

    def refresh_counts(self):
        self.comment_count = self.comments.filter(is_removed=False).count()
        self.like_count = self.likes.count()
        self.save(update_fields=['comment_count', 'like_count', 'updated_at'])


class Comment(models.Model):
    """A reply on a thread."""

    discussion = models.ForeignKey(
        Discussion, on_delete=models.CASCADE, related_name='comments',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='comments',
    )
    author_handle = models.CharField(_('posted as'), max_length=40)
    body = models.TextField(_('body'))
    is_removed = models.BooleanField(_('removed'), default=False)
    created_at = models.DateTimeField(_('posted at'), default=timezone.now)

    class Meta:
        ordering = ('created_at',)
        verbose_name = _('comment')
        verbose_name_plural = _('comments')
        indexes = [models.Index(fields=['discussion', 'created_at'])]

    def __str__(self):
        return '{0} on {1}'.format(self.author_handle, self.discussion)


class DiscussionLike(models.Model):
    """One person's like. A row rather than a counter, so it can be undone."""

    discussion = models.ForeignKey(
        Discussion, on_delete=models.CASCADE, related_name='likes',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='liked_posts',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('discussion', 'user')
        verbose_name = _('like')
        verbose_name_plural = _('likes')

    def __str__(self):
        return '{0} likes {1}'.format(self.user.get_username(), self.discussion)


class Report(models.Model):
    """Someone flagging a post or a reply.

    A forum about mental health needs a way to say "this is harmful" that does
    not depend on staff reading everything. Nothing here acts automatically --
    a report is a queue for a person.
    """

    REASONS = (
        ('harm', _('Encourages self-harm')),
        ('abuse', _('Abusive or harassing')),
        ('spam', _('Spam')),
        ('other', _('Something else')),
    )

    discussion = models.ForeignKey(
        Discussion, on_delete=models.CASCADE, null=True, blank=True,
        related_name='reports',
    )
    comment = models.ForeignKey(
        Comment, on_delete=models.CASCADE, null=True, blank=True,
        related_name='reports',
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reports',
    )
    reason = models.CharField(_('reason'), max_length=20, choices=REASONS)
    detail = models.TextField(_('detail'), blank=True)
    handled_at = models.DateTimeField(_('handled at'), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('handled_at', '-created_at')
        verbose_name = _('report')
        verbose_name_plural = _('reports')

    def __str__(self):
        return '{0} report'.format(self.get_reason_display())
