from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import translate_url
from django.utils import translation


class PreferredLanguageMiddleware:
    """Keep a visitor's chosen language across the whole site.

    English lives at "/" and Pidgin at "/pcm/", so a visitor who picked Pidgin
    and then opens an address without the prefix -- a bookmark, a shared link,
    the bare domain -- would silently get English back. This sends them to the
    same page in the language they chose. Changing language on any page still
    wins, because the switcher writes the cookie as it redirects.

    Must sit after LocaleMiddleware, which is what reads the URL and the cookie.
    """

    # Left alone: the admin, the language switcher itself, and static files.
    SKIP_PREFIXES = ('/admin/', '/i18n/', settings.STATIC_URL or '/static/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_redirect(request):
            chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
            target = translate_url(request.get_full_path(), chosen)
            if target != request.get_full_path():
                response = HttpResponseRedirect(target)
                response.headers['Vary'] = 'Cookie'
                return response
        return self.get_response(request)

    def _should_redirect(self, request):
        if request.method not in ('GET', 'HEAD'):
            return False
        if request.path.startswith(self.SKIP_PREFIXES):
            return False
        chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        supported = dict(settings.LANGUAGES)
        if chosen not in supported or chosen == translation.get_language():
            return False
        # Only when the URL itself does not name a language: a /pcm/ page stays
        # Pidgin for an English visitor, so shared links keep working.
        return translation.get_language() == settings.LANGUAGE_CODE
