"""Write the error pages the web server has to serve by itself.

502, 503, 504 and 508 all mean the same thing from a visitor's side: the
Python app is not answering. Django cannot apologise for being down, so
these are rendered to flat files that LiteSpeed serves straight from disk,
and pointed at with ErrorDocument lines in .htaccess (see deploy/CPANEL.md).

Rendering them from the same templates as the rest is the whole point: the
copy and the layout live in one place, and `collectstatic`-style staleness
is the only way the two can drift. Run it after collectstatic, which is
what puts the stylesheet and the images where these files expect them.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.template import loader
from django.test import RequestFactory
from django.utils import translation

#: What gets written. Everything here is a failure the app cannot report on
#: its own behalf, plus 500, because the Python process can die between
#: accepting a request and answering it.
CODES = (500, 502, 503, 504, 508)


class Command(BaseCommand):
    help = 'Render the static error pages the web server serves when the app is down.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--out',
            help=(
                'Where to write them. Defaults to an "errors" folder beside '
                'the collected static files, which is inside the document '
                'root on cPanel.'
            ),
        )

    def handle(self, *args, **options):
        loud = options['verbosity'] >= 1
        out = Path(options['out'] or Path(settings.STATIC_ROOT) / 'errors')
        out.mkdir(parents=True, exist_ok=True)

        # A request the templates can read: the nav and the footer reverse
        # URLs through a context processor, and that needs one. The host
        # only has to be allowed, since nothing in these pages is
        # host-relative.
        request = RequestFactory().get('/', HTTP_HOST=settings.ALLOWED_HOSTS[0])

        # English only. These are served by a web server that knows nothing
        # about the visitor's language cookie, so there is no choice to make
        # and a Pidgin copy would be served to everybody or to nobody.
        with translation.override('en'):
            for code in CODES:
                try:
                    page = loader.render_to_string(
                        '{0}.html'.format(code), request=request,
                    )
                except ValueError as error:
                    # The hashed-filename manifest is written by
                    # collectstatic. Said plainly, because the message
                    # Django raises names a missing image and not the step
                    # that was skipped.
                    if 'manifest' not in str(error):
                        raise
                    raise CommandError(
                        'Run collectstatic first -- these pages link to the '
                        'stylesheet and the logo by their hashed names, and '
                        'the manifest that holds them does not exist yet.',
                    )
                target = out / '{0}.html'.format(code)
                target.write_text(page, encoding='utf-8')
                if loud:
                    self.stdout.write('wrote {0}'.format(target))

        if loud:
            self.stdout.write(self.style.SUCCESS(
                '{0} pages in {1}'.format(len(CODES), out),
            ))
