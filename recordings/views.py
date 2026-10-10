"""The callback ZEGOCLOUD makes when a recording file is ready.

Nothing else in this app is reachable over HTTP: recordings are read in the
admin, by staff, with every read logged. There is no endpoint that hands a
client their own session audio, and that is deliberate -- it would also hand
it to anyone who took their phone.
"""

import hashlib
import logging

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api import jobs

from .models import Recording
from .tasks import transcribe_recording

logger = logging.getLogger(__name__)


@csrf_exempt
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def recording_ready(request):
    """ZEGOCLOUD telling us a file has been uploaded.

    Verified the way their callbacks are signed: md5 of the app's secret with
    the nonce and timestamp they send. An unverified callback is dropped --
    anyone could otherwise point a recording row at a file of their choosing.
    """
    if not _signature_is_valid(request):
        logger.warning('rejected a recording callback with a bad signature')
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    payload = request.data or {}
    room_id = payload.get('room_id') or payload.get('RoomId') or ''
    file_info = payload.get('file_info') or payload.get('FileInfo') or {}
    key = file_info.get('url') or file_info.get('OutputFileName') or ''
    duration = int(file_info.get('duration') or file_info.get('Duration') or 0)

    recording = Recording.objects.filter(room_id=room_id).first()
    if recording is None:
        logger.warning('recording callback for an unknown room %s', room_id)
        return Response({'received': True})

    recording.mark_stored(audio_key=key, duration_seconds=duration)
    jobs.run(transcribe_recording, recording.pk)
    return Response({'received': True})


def _signature_is_valid(request):
    from experts import calls

    secret = calls.server_secret()
    if not secret:
        return False

    signature = request.META.get('HTTP_X_ZEGO_SIGNATURE', '')
    nonce = request.META.get('HTTP_X_ZEGO_NONCE', '')
    timestamp = request.META.get('HTTP_X_ZEGO_TIMESTAMP', '')
    if not signature:
        return False

    import hmac

    expected = hashlib.md5(
        '{0}{1}{2}'.format(nonce, secret, timestamp).encode('utf-8'),
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
