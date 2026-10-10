"""Short-lived links to media files.

The Flutter app's MediaItem says its playback URL is "expected to be a
short-lived signed URL ... so it must not be persisted long-term". Both
deployments honour that, by different means:

    AWS      the S3/R2 storage backend signs the URL itself; it expires after
             MEDIA_URL_TTL_SECONDS.
    cPanel   files sit on disk, so this module signs the path with the
             project's SECRET_KEY and `serve_media` checks the signature.
             Same promise, no object store needed.

Nothing outside this module knows which is in play.
"""

import mimetypes
import os
import posixpath

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.urls import reverse

_signer = TimestampSigner(salt='soothify.media')


def signed_url(name, request=None):
    """An absolute, expiring URL for the stored file `name`.

    Returns '' for an empty name, so a serializer can hand the result straight
    to the client without a None creeping into the JSON.
    """
    if not name:
        return ''

    if getattr(settings, 'ON_AWS', False):
        # The storage backend already returns a signed, expiring URL.
        url = default_storage.url(name)
    else:
        token = _signer.sign(name)
        url = reverse('api:media', kwargs={'token': token})

    if request is not None and url.startswith('/'):
        return request.build_absolute_uri(url)
    return url


def serve_media(request, token):
    """Hand back a file whose signed link is still valid (cPanel only).

    Range requests matter here -- the app seeks within audio and video -- and
    FileResponse answers them, which is what the landing site's own hero video
    needed from the web server.
    """
    try:
        name = _signer.unsign(token, max_age=settings.MEDIA_URL_TTL_SECONDS)
    except SignatureExpired:
        return HttpResponseForbidden('This link has expired.')
    except BadSignature:
        return HttpResponseForbidden('Invalid link.')

    # Defence in depth: a signature can only ever have been made for a path
    # inside MEDIA_ROOT, but never let one walk out of it.
    safe = posixpath.normpath(name).lstrip('/')
    if safe.startswith('..'):
        raise Http404
    if not default_storage.exists(safe):
        raise Http404

    content_type, _ = mimetypes.guess_type(safe)
    response = FileResponse(
        default_storage.open(safe, 'rb'),
        content_type=content_type or 'application/octet-stream',
    )
    response['Content-Disposition'] = 'inline; filename="{0}"'.format(
        os.path.basename(safe),
    )
    return response
