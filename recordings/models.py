"""Session recordings, their transcripts, and what is flagged in them.

Every session is recorded, in the cloud rather than on either person's phone,
so a practitioner cannot turn it off and a crashed app does not lose it.

This is the most sensitive data the platform holds: audio of people talking
about their mental health. Three rules run through the whole app.

    Told, always   nobody is recorded without having been shown that they
                   will be. The notice is recorded per session, per person,
                   with the wording they saw.
    Short-lived    audio is deleted after RETENTION_DAYS. What survives is a
                   flag and the few words around it, never the session.
    Accountable    every staff member who opens a recording or a transcript
                   leaves a row behind. Nobody browses this quietly.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# How long audio is kept. Long enough to look into a complaint, short enough
# that a breach exposes little.
RETENTION_DAYS = 30

# The wording people are shown. Versioned: if it changes, what somebody was
# actually told is still on their row.
NOTICE_VERSION = '2026-10-10'
NOTICE_TEXT = _(
    'Sessions are recorded for safety. Recordings are deleted after 30 days '
    'and are reviewed only if something is reported or flagged.'
)


class Recording(models.Model):
    """The audio of one session."""

    STATUS = (
        ('starting', _('Starting')),
        ('recording', _('Recording')),
        ('stored', _('Stored')),
        ('transcribed', _('Transcribed')),
        ('failed', _('Failed')),
        ('deleted', _('Deleted')),
    )

    session = models.OneToOneField(
        'experts.Session', on_delete=models.CASCADE, related_name='recording',
    )
    room_id = models.CharField(_('room'), max_length=80)

    # ZEGOCLOUD's handle for the running recording, needed to stop it.
    task_id = models.CharField(_('task'), max_length=120, blank=True)

    status = models.CharField(_('status'), max_length=20, choices=STATUS, default='starting')
    audio_key = models.CharField(
        _('file'), max_length=300, blank=True,
        help_text=_('Where the audio sits in the bucket. Emptied on deletion.'),
    )
    duration_seconds = models.PositiveIntegerField(_('length'), default=0)

    started_at = models.DateTimeField(_('started'), null=True, blank=True)
    finished_at = models.DateTimeField(_('finished'), null=True, blank=True)

    # Set when the audio arrives, so the purge has one field to sort on.
    delete_after = models.DateTimeField(_('delete after'), null=True, blank=True)
    deleted_at = models.DateTimeField(_('deleted'), null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('recording')
        verbose_name_plural = _('recordings')
        indexes = [models.Index(fields=['status', 'delete_after'])]

    def __str__(self):
        return 'recording of {0}'.format(self.session)

    @property
    def has_audio(self):
        return bool(self.audio_key) and self.deleted_at is None

    def mark_stored(self, audio_key, duration_seconds=0):
        self.audio_key = audio_key
        self.duration_seconds = duration_seconds
        self.status = 'stored'
        self.finished_at = timezone.now()
        self.delete_after = timezone.now() + timezone.timedelta(days=RETENTION_DAYS)
        self.save()


class RecordingNotice(models.Model):
    """That this person was told, before joining, that they were recorded.

    Written when the server hands out the token to join -- which is the last
    point before anyone can be heard, and the only one the app cannot skip.
    """

    session = models.ForeignKey(
        'experts.Session', on_delete=models.CASCADE, related_name='recording_notices',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recording_notices',
    )
    version = models.CharField(_('wording'), max_length=20, default=NOTICE_VERSION)
    shown_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ('session', 'user')
        verbose_name = _('recording notice')
        verbose_name_plural = _('recording notices')

    def __str__(self):
        return 'told {0} about {1}'.format(self.user.get_username(), self.session_id)


class Transcript(models.Model):
    """Words from the audio.

    Kept after the audio is deleted only where a flag refers to it; an
    unflagged transcript goes with its recording, because a searchable record
    of therapy sessions is the same problem as the audio in slower form.
    """

    recording = models.OneToOneField(
        Recording, on_delete=models.CASCADE, related_name='transcript',
    )
    text = models.TextField(_('text'), blank=True)
    language = models.CharField(_('language'), max_length=20, blank=True)
    engine = models.CharField(_('made by'), max_length=40, blank=True)
    confidence = models.FloatField(_('confidence'), default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('transcript')
        verbose_name_plural = _('transcripts')

    def __str__(self):
        return 'transcript of {0}'.format(self.recording_id)


class Flag(models.Model):
    """Something in a session that a person should look at.

    Nothing here acts on its own: a flag suspends nobody and tells nobody off.
    It puts a short excerpt in a queue, and a human decides.
    """

    KINDS = (
        ('contact', _('Shared a phone number or handle')),
        ('off_platform', _('Suggested booking or paying elsewhere')),
        ('safety', _('Possible risk of harm')),
    )
    STATUS = (
        ('new', _('New')),
        ('reviewing', _('Being reviewed')),
        ('upheld', _('Upheld')),
        ('dismissed', _('Dismissed')),
    )

    recording = models.ForeignKey(
        Recording, on_delete=models.CASCADE, related_name='flags',
    )
    kind = models.CharField(_('kind'), max_length=20, choices=KINDS)

    # The few words around the match, not the session. What a reviewer needs
    # to judge it, and no more.
    excerpt = models.CharField(_('excerpt'), max_length=300)
    matched = models.CharField(_('matched'), max_length=120, blank=True)
    confidence = models.FloatField(_('confidence'), default=0)

    status = models.CharField(_('status'), max_length=20, choices=STATUS, default='new')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_flags',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(_('notes'), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('status', '-created_at')
        verbose_name = _('flag')
        verbose_name_plural = _('flags')
        indexes = [models.Index(fields=['status', '-created_at'])]

    def __str__(self):
        return '{0} ({1})'.format(self.get_kind_display(), self.get_status_display())


class AccessLog(models.Model):
    """A staff member opened a recording or a transcript.

    Written before the file is served, not after, so a failure to log is a
    failure to listen.
    """

    ACTIONS = (
        ('audio', _('Listened to the audio')),
        ('transcript', _('Read the transcript')),
        ('export', _('Downloaded it')),
    )

    recording = models.ForeignKey(
        Recording, on_delete=models.CASCADE, related_name='access_log',
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='recording_access',
    )
    action = models.CharField(_('did'), max_length=20, choices=ACTIONS)
    reason = models.CharField(_('why'), max_length=200, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-at',)
        verbose_name = _('access log entry')
        verbose_name_plural = _('access log')

    def __str__(self):
        return '{0} {1} {2}'.format(self.staff, self.get_action_display(), self.recording_id)
