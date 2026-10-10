"""Starting, finishing and eventually destroying recordings."""

import logging

from django.utils import timezone

from api import jobs

from . import scanning, zego
from .models import Flag, Recording, Transcript

logger = logging.getLogger(__name__)


@jobs.task
def start_recording(session_id):
    """Begin recording a session's room.

    Started when the first person is let into the room rather than when both
    are, so nothing said at the beginning is missed.
    """
    from experts.models import Session

    session = Session.objects.filter(pk=session_id).first()
    if session is None:
        return False

    recording, created = Recording.objects.get_or_create(
        session=session,
        defaults={'room_id': session.room_id, 'started_at': timezone.now()},
    )
    if not created and recording.task_id:
        return True          # already running

    if not zego.configured():
        recording.status = 'failed'
        recording.save(update_fields=['status'])
        logger.warning('cannot record session %s: no ZEGOCLOUD credentials', session_id)
        return False

    try:
        recording.task_id = zego.start(
            room_id=session.room_id,
            output_prefix='recordings/{0}/'.format(session.pk),
        )
    except zego.ZegoError as failure:
        recording.status = 'failed'
        recording.save(update_fields=['status'])
        logger.warning('could not start recording %s: %s', session_id, failure)
        return False

    recording.status = 'recording'
    recording.started_at = recording.started_at or timezone.now()
    recording.save(update_fields=['task_id', 'status', 'started_at'])
    return True


@jobs.task
def stop_recording(session_id):
    """End a recording once the session is over."""
    recording = Recording.objects.filter(
        session_id=session_id, status='recording',
    ).first()
    if recording is None or not recording.task_id:
        return False
    try:
        zego.stop(recording.task_id)
    except zego.ZegoError as failure:
        logger.warning('could not stop recording %s: %s', recording.pk, failure)
        return False
    recording.finished_at = timezone.now()
    recording.save(update_fields=['finished_at'])
    return True


@jobs.task
def transcribe_recording(recording_id):
    """Transcribe stored audio and scan what comes back."""
    from . import transcribe as transcriber

    recording = Recording.objects.filter(pk=recording_id).first()
    if recording is None or not recording.has_audio:
        return False
    if not transcriber.available():
        logger.info('transcription is not set up on this machine')
        return False

    try:
        text = transcriber.transcribe(
            audio_key=recording.audio_key,
            job_name='session-{0}'.format(recording.session_id),
            wait=True,
        )
    except transcriber.TranscriptionError as failure:
        logger.warning('transcription failed for %s: %s', recording_id, failure)
        return False

    Transcript.objects.update_or_create(
        recording=recording,
        defaults={
            'text': text,
            'language': transcriber.LANGUAGE,
            'engine': 'aws-transcribe',
        },
    )
    recording.status = 'transcribed'
    recording.save(update_fields=['status'])
    return flag_recording(recording.pk)


def flag_recording(recording_id):
    """Scan a transcript and queue anything a person should look at."""
    recording = Recording.objects.filter(pk=recording_id).select_related(
        'transcript',
    ).first()
    transcript = getattr(recording, 'transcript', None)
    if transcript is None:
        return 0

    made = 0
    for finding in scanning.scan(transcript.text):
        _created = Flag.objects.get_or_create(
            recording=recording,
            kind=finding['kind'],
            excerpt=finding['excerpt'],
            defaults={
                'matched': finding['matched'],
                'confidence': finding['confidence'],
            },
        )[1]
        made += 1 if _created else 0

    if made:
        logger.info('recording %s raised %s flags', recording_id, made)
    return made


@jobs.task
def purge_old_recordings():
    """Delete audio past its retention date.

    The file goes; the Recording row stays, so there is a record that a
    session was recorded and when it was destroyed. A transcript is kept only
    where a flag still refers to it.
    """
    from django.core.files.storage import default_storage

    due = Recording.objects.filter(
        delete_after__lte=timezone.now(), deleted_at__isnull=True,
    ).exclude(audio_key='')

    purged = 0
    for recording in due:
        try:
            if default_storage.exists(recording.audio_key):
                default_storage.delete(recording.audio_key)
        except Exception:
            logger.exception('could not delete audio for recording %s', recording.pk)
            continue

        keep_transcript = recording.flags.exclude(status='dismissed').exists()
        if not keep_transcript:
            Transcript.objects.filter(recording=recording).delete()

        recording.audio_key = ''
        recording.status = 'deleted'
        recording.deleted_at = timezone.now()
        recording.save(update_fields=['audio_key', 'status', 'deleted_at'])
        purged += 1

    logger.info('purged %s recordings', purged)
    return purged
