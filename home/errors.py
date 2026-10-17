"""What the site says when a request cannot be answered.

Two audiences reach these, and they want opposite things. A person wants a
page in the site's own language with a way back to it; the Flutter app wants
JSON, because an HTML page handed to a JSON parser is not an error message,
it is a crash.

Django's built-in handlers only know the first. These pick by path: anything
under /api/ is answered the way the rest of the API answers, with the same
`detail` key DRF uses, so the app needs no special case for "the URL was
wrong".
"""

import logging

from django.http import HttpResponse, JsonResponse
from django.template import loader
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

#: The page behind the page: see server_error.
BARE_500 = '500_bare.html'

#: Everything below this prefix is the mobile API. Spelled out here rather
#: than imported from urls.py, which imports this module.
API_PREFIX = '/api/'

#: The JSON half. Lazy, so each is translated when it is sent rather than
#: when this module is first imported -- which happens once, in whatever
#: language the first request to the server happened to be in.
MESSAGES = {
    400: _('That request could not be read.'),
    403: _('You do not have access to that.'),
    404: _('Not found.'),
    500: _('Something went wrong at our end.'),
}


def wants_json(request):
    return request.path.startswith(API_PREFIX)


def _html(content, status):
    return HttpResponse(
        content, status=status, content_type='text/html; charset=utf-8',
    )


def _page(request, status, template, context=None):
    """The page, or the JSON for an API path.

    If the page itself cannot be rendered -- a missing staticfiles manifest
    is the usual way, since the designed pages ask for the stylesheet and
    the logo by their hashed names -- it falls back to the bare page rather
    than letting the exception turn a 404 into a 500. The status the visitor
    gets should be the one that actually happened.
    """
    if wants_json(request):
        return JsonResponse({'detail': MESSAGES[status]}, status=status)

    try:
        page = loader.render_to_string(template, context or {}, request=request)
    except Exception:
        logger.exception('the %s page could not be rendered', status)
        page = _bare(status)

    return _html(page, status)


def _bare(status):
    """The apology that reaches for nothing, with the right number on it."""
    page = loader.render_to_string(BARE_500)
    if status == 500:
        return page
    return page.replace('>500<', '>{0}<'.format(status)).replace(
        'Something Went Wrong At Our End', str(MESSAGES[status]),
    )


def not_found(request, exception=None, template_name='404.html'):
    # The path is shown on the page so somebody reporting a broken link can
    # say which one. Django's own handler passes it the same way.
    return _page(request, 404, template_name, {'request_path': request.path})


def forbidden(request, exception=None, template_name='403.html'):
    return _page(request, 403, template_name)


def bad_request(request, exception=None, template_name='400.html'):
    return _page(request, 400, template_name)


def server_error(request, template_name='500.html'):
    """The one that runs when something is already broken.

    It tries the designed page first -- the nav, the footer, the site's own
    type -- and falls back to 500_bare.html if rendering that raises. The
    full page needs the context processors, the stylesheet and the static
    manifest, and whatever brought somebody here may be precisely what stops
    one of those working. An apology that 500s is worse than a plain one.
    """
    if wants_json(request):
        return JsonResponse({'detail': MESSAGES[500]}, status=500)

    try:
        page = loader.render_to_string(template_name, request=request)
    except Exception:
        logger.exception('the 500 page could not be rendered; serving the bare one')
        page = _bare(500)

    return _html(page, 500)
