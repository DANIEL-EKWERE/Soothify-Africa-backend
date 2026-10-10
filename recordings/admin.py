"""Where flags are reviewed.

Built around one idea: a reviewer should see the least that lets them judge.
So the queue shows excerpts, not sessions; opening the audio or the full
transcript is a deliberate act that writes an access log row first.
"""

from django.contrib import admin
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from api.media import signed_url

from .models import AccessLog, Flag, Recording, RecordingNotice, Transcript


@admin.register(Flag)
class FlagAdmin(admin.ModelAdmin):
    """The queue. This is the screen a moderator lives in."""

    list_display = ('kind', 'status', 'confidence', 'short_excerpt', 'session_link', 'created_at')
    list_filter = ('status', 'kind', 'created_at')
    search_fields = ('excerpt', 'matched')
    readonly_fields = (
        'recording', 'kind', 'matched', 'excerpt', 'confidence', 'created_at',
        'reviewed_by', 'reviewed_at', 'listen',
    )
    fields = (
        'kind', 'confidence', 'excerpt', 'matched', 'listen',
        'status', 'review_notes', 'reviewed_by', 'reviewed_at', 'created_at',
    )
    actions = ['uphold', 'dismiss']

    def has_add_permission(self, request):
        return False

    @admin.display(description='excerpt')
    def short_excerpt(self, obj):
        return obj.excerpt[:80]

    @admin.display(description='session')
    def session_link(self, obj):
        return obj.recording.session_id

    @admin.display(description='audio')
    def listen(self, obj):
        """A link that logs the listen, then redirects to the file."""
        if not obj.recording.has_audio:
            return 'Deleted'
        return format_html(
            '<a href="{0}">Listen (this is logged)</a>',
            reverse('admin:recordings_recording_listen', args=[obj.recording_id]),
        )

    def save_model(self, request, obj, form, change):
        if 'status' in form.changed_data and obj.status in ('upheld', 'dismissed'):
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)

    @admin.action(description='Uphold')
    def uphold(self, request, queryset):
        self._review(request, queryset, 'upheld')

    @admin.action(description='Dismiss')
    def dismiss(self, request, queryset):
        self._review(request, queryset, 'dismissed')

    def _review(self, request, queryset, status):
        updated = queryset.update(
            status=status, reviewed_by=request.user, reviewed_at=timezone.now(),
        )
        self.message_user(request, '{0} marked {1}.'.format(updated, status))


@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    list_display = ('session', 'status', 'duration_minutes', 'flag_count',
                    'delete_after', 'deleted_at')
    list_filter = ('status', 'created_at')
    search_fields = ('session__client__email', 'room_id')
    readonly_fields = [field.name for field in Recording._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.display(description='length')
    def duration_minutes(self, obj):
        return '{0} min'.format(round(obj.duration_seconds / 60)) if obj.duration_seconds else '-'

    @admin.display(description='flags')
    def flag_count(self, obj):
        return obj.flags.count()

    def get_urls(self):
        return [
            path(
                '<int:pk>/listen/',
                self.admin_site.admin_view(self.listen_view),
                name='recordings_recording_listen',
            ),
        ] + super().get_urls()

    def listen_view(self, request, pk):
        """Log first, then hand over a short-lived link to the audio."""
        recording = get_object_or_404(Recording, pk=pk)
        if not recording.has_audio:
            self.message_user(request, 'That audio has been deleted.')
            return redirect('admin:recordings_recording_changelist')

        AccessLog.objects.create(
            recording=recording,
            staff=request.user,
            action='audio',
            reason=request.GET.get('reason', ''),
        )
        return redirect(signed_url(recording.audio_key, request))


@admin.register(Transcript)
class TranscriptAdmin(admin.ModelAdmin):
    list_display = ('recording', 'language', 'engine', 'created_at')
    readonly_fields = ('recording', 'text', 'language', 'engine', 'confidence', 'created_at')
    search_fields = ('recording__session__client__email',)

    def has_add_permission(self, request):
        return False

    def change_view(self, request, object_id, form_url='', extra_context=None):
        # Reading a whole transcript is reading somebody's therapy session.
        transcript = Transcript.objects.filter(pk=object_id).first()
        if transcript is not None:
            AccessLog.objects.create(
                recording=transcript.recording, staff=request.user, action='transcript',
            )
        return super().change_view(request, object_id, form_url, extra_context)


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    """Who looked at what. Nothing here can be edited or deleted."""

    list_display = ('at', 'staff', 'action', 'recording', 'reason')
    list_filter = ('action', 'at')
    search_fields = ('staff__email',)
    readonly_fields = [field.name for field in AccessLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RecordingNotice)
class RecordingNoticeAdmin(admin.ModelAdmin):
    """Proof that someone was told. Read-only, for the same reason."""

    list_display = ('session', 'user', 'version', 'shown_at')
    list_filter = ('version', 'shown_at')
    search_fields = ('user__email',)
    readonly_fields = ('session', 'user', 'version', 'shown_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
