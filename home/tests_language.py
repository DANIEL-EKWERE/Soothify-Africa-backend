from django.test import TestCase
from django.urls import reverse


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
        self.choose('pcm')
        resp = self.client.post('/waitlist/', {'name': 'Ada', 'email': 'lang@example.com'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('home:waitlist_done'))
