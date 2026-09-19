from django.test import TestCase, override_settings

# The deployed settings run with DEBUG off, which redirects http to https and
# serves static files from a collectstatic manifest. Both are right for the
# live site but get in the way of the test client, which speaks plain http and
# runs without collectstatic -- so the tests switch just those two off.
PLAIN_TEST_SETTINGS = {
    'SECURE_SSL_REDIRECT': False,
    'STORAGES': {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
}
from django.urls import reverse


@override_settings(**PLAIN_TEST_SETTINGS)
class LanguageStickinessTests(TestCase):
    """A chosen language should hold across the site, not just on links that
    already carry the /pcm/ prefix."""

    def choose(self, code):
        self.client.post(reverse('set_language'), {'language': code, 'next': '/'})

    def test_pidgin_choice_redirects_unprefixed_pages(self):
        self.choose('pcm')
        for path, expected in (('/', '/pcm/'),
                               ('/waitlist/', '/pcm/waitlist/'),
                               ('/privacy-policy/', '/pcm/privacy-policy/'),
                               ('/terms-of-use/', '/pcm/terms-of-use/')):
            resp = self.client.get(path)
            self.assertRedirects(resp, expected, msg_prefix=path)

    def test_pidgin_pages_serve_pidgin(self):
        self.choose('pcm')
        self.assertContains(self.client.get('/pcm/'), 'Life dey move fast')

    def test_english_choice_keeps_plain_urls(self):
        self.choose('en')
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/waitlist/').status_code, 200)

    def test_a_pcm_link_still_opens_for_an_english_visitor(self):
        self.choose('en')
        self.assertEqual(self.client.get('/pcm/').status_code, 200)   # shared links keep working

    def test_switching_again_wins(self):
        self.choose('pcm')
        self.choose('en')
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_admin_and_language_endpoint_are_untouched(self):
        self.choose('pcm')
        self.assertEqual(self.client.get('/admin/login/').status_code, 200)

    def test_form_posts_are_not_redirected(self):
        # A Pidgin visitor is sent to /pcm/waitlist/ before they see the form,
        # and the form posts back to the page it is on -- so that is the post
        # to check. (Django 4.2 itself redirects a post to the unprefixed
        # address for such a visitor, dropping the data, which is why the
        # form must never be served at the English address to them.)
        self.choose('pcm')
        page = self.client.get('/waitlist/', follow=True)
        self.assertEqual(page.request['PATH_INFO'], '/pcm/waitlist/')
        self.assertContains(page, 'action="/pcm/waitlist/"')
        resp = self.client.post('/pcm/waitlist/', {'name': 'Ada', 'email': 'lang@example.com'})
        self.assertRedirects(resp, '/pcm/waitlist/done/', fetch_redirect_response=False)
