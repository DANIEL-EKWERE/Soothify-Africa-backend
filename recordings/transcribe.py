"""Turning session audio into text, with AWS Transcribe.

One honest limit, stated where it will be read rather than buried: **Nigerian
Pidgin is not supported**. English models render it badly, which means both
missed violations and false flags on Pidgin sessions. The app offers sessions
in Pidgin, so a real share of recordings will transcribe poorly. Treat a
Pidgin transcript as weak evidence, and lean on the audio when reviewing one.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

# en-NG exists in Transcribe's list but is thin; en-GB tends to do better on
# Nigerian English in practice. Worth re-testing on real sessions.
LANGUAGE = os.environ.get('TRANSCRIBE_LANGUAGE', 'en-NG')


class TranscriptionError(RuntimeError):
    """The audio could not be transcribed."""


def available():
    try:
        import boto3  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get('AWS_STORAGE_BUCKET_NAME'))


def transcribe(audio_key, job_name, bucket=None, wait=False):
    """Start a transcription job for one recording.

    Returns the job name. With `wait`, polls until it finishes and returns
    the text instead -- used by the management command, not by the web path,
    where nothing should block on a transcription.
    """
    try:
        import boto3
    except ImportError:
        raise TranscriptionError('boto3 is not installed on this machine.')

    bucket = bucket or os.environ.get('AWS_STORAGE_BUCKET_NAME', '')
    if not bucket:
        raise TranscriptionError('No bucket configured for transcription.')

    client = boto3.client('transcribe')
    client.start_transcription_job(
        TranscriptionJobName=job_name,
        LanguageCode=LANGUAGE,
        MediaFormat='mp3',
        Media={'MediaFileUri': 's3://{0}/{1}'.format(bucket, audio_key)},
        Settings={
            # Two voices, so a reviewer can tell who said the thing that was
            # flagged -- the practitioner or the client.
            'ShowSpeakerLabels': True,
            'MaxSpeakerLabels': 2,
        },
    )
    if not wait:
        return job_name
    return _await(client, job_name)


def _await(client, job_name, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get_transcription_job(TranscriptionJobName=job_name)
        status = job['TranscriptionJob']['TranscriptionJobStatus']
        if status == 'COMPLETED':
            uri = job['TranscriptionJob']['Transcript']['TranscriptFileUri']
            return _fetch(uri)
        if status == 'FAILED':
            raise TranscriptionError(
                job['TranscriptionJob'].get('FailureReason', 'Transcription failed.'),
            )
        time.sleep(10)
    raise TranscriptionError('Transcription did not finish in time.')


def _fetch(uri):
    import json
    from urllib import request

    with request.urlopen(uri, timeout=60) as response:
        payload = json.loads(response.read().decode('utf-8'))
    results = payload.get('results', {}).get('transcripts', [])
    return results[0].get('transcript', '') if results else ''
