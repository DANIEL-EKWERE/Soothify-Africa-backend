"""The pages nobody is meant to see.

Worth testing precisely because nobody looks at them: an error page that is
itself broken stays invisible until the day it matters. What is checked here
is that each one renders, that it carries the design's own parts, that the
app is handed JSON rather than a page, and -- the one that bites -- that the
500 path still answers when the machinery the other pages depend on has
stopped working.
"""

import json
from unittest import mock

from django.core.management import CommandError, call_command
from django.template import loader
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from home import errors

#: Every status with a page of its own. The first four Django serves; the
#: rest are for the web server to serve when Django cannot (see
#: make_error_pages).
SERVED_BY_DJANGO = (400, 403, 404, 429, 500)
SERVED_BY_THE_WEB_SERVER = (502, 503, 504, 508)


@override_settings(DEBUG=False)
class ErrorPageTests(TestCase):
    def setUp(self):
        translation.activate('en')
        self.addCleanup(translation.deactivate)

    # ------------------------------------------------------- every page

    def test_each_one_renders_with_its_number_and_a_way_out(self):
        for code in SERVED_BY_DJANGO + SERVED_BY_THE_WEB_SERVER:
            with self.subTest(code=code):
                page = self.render(code)

                self.assertIn('>{0}<'.format(code), page)
                self.assertIn('Go back to homepage', page)
                self.assertIn('oops__title', page)

    def test_each_one_says_something_of_its_own(self):
        headings = {self.heading(c) for c in SERVED_BY_DJANGO}

        # Nine pages that all say "something went wrong" would be one page
        # with nine URLs.
        self.assertEqual(len(headings), len(SERVED_BY_DJANGO))

    def test_they_keep_the_site_s_nav_and_footer(self):
        # The most useful thing an error page can do is look like somewhere
        # the visitor has already been, and carry the usual links.
        page = self.render(404)

        self.assertIn('page-head--light', page)
        self.assertIn(reverse('home:waitlist'), page)

    def render(self, code):
        from django.test import RequestFactory

        request = RequestFactory().get('/')
        return loader.render_to_string('{0}.html'.format(code), request=request)

    def heading(self, code):
        page = self.render(code)
        start = page.index('oops__title">') + len('oops__title">')
        return page[start:page.index('</h1>', start)].strip()

    # ------------------------------------------------------- the handlers

    def test_a_missing_page_is_a_page_not_a_stack_trace(self):
        response = self.client.get('/no-such-thing/')

        self.assertEqual(response.status_code, 404)
        self.assertIn('This Page Has Wandered Off', response.content.decode())

    def test_a_missing_page_is_a_page_in_pidgin_too(self):
        response = self.client.get('/pcm/no-such-thing/')

        self.assertEqual(response.status_code, 404)
        self.assertIn('404', response.content.decode())

    # -------------------------------------------------------------- the app

    def test_the_api_gets_json_not_a_page(self):
        # An HTML page handed to a JSON parser is a crash, not a message.
        response = self.client.get('/api/v1/no-such-endpoint/')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(json.loads(response.content)['detail'], 'Not found.')

    def test_only_the_api_gets_json(self):
        response = self.client.get('/apiary/')

        self.assertIn('text/html', response['Content-Type'])


class ServerErrorTests(SimpleTestCase):
    """The 500 path, which runs when something is already broken."""

    def request(self, path='/'):
        from django.test import RequestFactory

        return RequestFactory().get(path)

    def test_it_serves_the_designed_page(self):
        response = errors.server_error(self.request())

        self.assertEqual(response.status_code, 500)
        self.assertIn('Something Went Wrong At Our End', response.content.decode())

    def test_it_falls_back_when_that_page_cannot_be_rendered(self):
        # The full page needs the context processors, the stylesheet and the
        # static manifest, and whatever brought somebody here may be exactly
        # what stops one of those working.
        real = loader.render_to_string

        def fail_the_first(template, *args, **kwargs):
            if template == '500.html':
                raise ValueError('missing staticfiles manifest entry')
            return real(template, *args, **kwargs)

        with mock.patch.object(loader, 'render_to_string', fail_the_first):
            response = errors.server_error(self.request())

        page = response.content.decode()
        self.assertEqual(response.status_code, 500)
        self.assertIn('Something Went Wrong At Our End', page)
        # The bare page reaches for nothing: no stylesheet, no static files.
        self.assertNotIn('<link', page)
        self.assertIn('<style>', page)
        self.assertIn('href="/"', page)

    def test_the_app_gets_json_from_it_as_well(self):
        response = errors.server_error(self.request('/api/v1/anything/'))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response['Content-Type'], 'application/json')


class StaticErrorPageTests(SimpleTestCase):
    """`manage.py make_error_pages` -- what the web server serves alone."""

    def test_it_writes_a_file_for_every_failure_django_cannot_report(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as out:
            call_command('make_error_pages', out=out, verbosity=0)

            written = sorted(p.name for p in Path(out).glob('*.html'))

        self.assertEqual(
            written,
            ['{0}.html'.format(c) for c in (500,) + SERVED_BY_THE_WEB_SERVER],
        )

    def test_the_files_stand_on_their_own(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as out:
            call_command('make_error_pages', out=out, verbosity=0)
            page = (Path(out) / '508.html').read_text()

        self.assertIn('Too Many People Trying To Feel Better At Once', page)
        # Absolute, because the web server serves this file from disk at a
        # path that has nothing to do with the one the visitor asked for.
        self.assertIn('href="/static/', page)

    def test_it_says_to_run_collectstatic_first(self):
        # Django's own message names a missing image, not the step that was
        # skipped, which sends you looking in the wrong place.
        import tempfile

        missing = ValueError("Missing staticfiles manifest entry for 'img/x.jpg'")
        with tempfile.TemporaryDirectory() as out:
            with mock.patch.object(loader, 'render_to_string', side_effect=missing):
                with self.assertRaisesMessage(CommandError, 'collectstatic'):
                    call_command('make_error_pages', out=out, verbosity=0)

    def test_any_other_failure_is_left_alone(self):
        # Only the manifest gets the friendlier wording; swallowing the rest
        # would hide a broken template behind advice that does not apply.
        import tempfile

        with tempfile.TemporaryDirectory() as out:
            with mock.patch.object(
                loader, 'render_to_string', side_effect=ValueError('something else'),
            ):
                with self.assertRaisesMessage(ValueError, 'something else'):
                    call_command('make_error_pages', out=out, verbosity=0)
