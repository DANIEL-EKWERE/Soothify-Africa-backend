"""The practitioner application, as the website takes it.

The same application the app sends through the API, from somebody who has
no account and should not need one to ask — and in the same shape as the
app's own screens: an intro, three steps, the acknowledgement.

What is tested here is the part the API's own tests cannot see: that the
steps hold what was answered, that none of them can be skipped, that the
files are checked, and that the person is written to.
"""

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from experts.forms import MAX_UPLOAD_BYTES
from experts.models import Application, ApplicationDocument


#: What the site's header and footer put on a page. Used to assert the
#: application flow carries none of it -- see the test at the foot of this
#: file, which first checks an ordinary page does.
CHROME = ('page-head', 'class="nav', '<footer', 'Join Waitlist')


def a_file(name='licence.pdf', content=b'%PDF-1.4 test', content_type='application/pdf'):
    return SimpleUploadedFile(name, content, content_type=content_type)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    # The acknowledgement is a job; inline here, as it is on cPanel.
    CELERY_BROKER_URL='',
)
class PractitionerApplicationTests(TestCase):
    def setUp(self):
        # English, explicitly. `reverse()` under i18n_patterns answers in
        # whatever language is active, and the language a previous test left
        # active leaks into this one -- which silently turned these URLs
        # into the /pcm/ ones and the assertions into a Pidgin page.
        translation.activate('en')
        self.addCleanup(translation.deactivate)

        self.intro = reverse('home:practitioner_apply')
        self.step = lambda n: reverse('home:practitioner_apply_step', args=[n])
        self.done = reverse('home:practitioner_apply_done')

    # ------------------------------------------------------- walking it

    def one(self, **overrides):
        payload = {
            'disciplines': ['therapy', 'yoga'],
            'full_name': 'Dera Eze',
            'email': 'Dera@Example.com',
            'phone': '+2348030000000',
            'experience_years': '6',
            'bio': 'Ten years in private practice in Lagos.',
            'portfolio_url': 'https://dera.example',
        }
        payload.update(overrides)
        return {k: v for k, v in payload.items() if v is not None}

    def two(self, **overrides):
        payload = {'languages': ['english', 'pidgin']}
        payload.update(overrides)
        return {k: v for k, v in payload.items() if v is not None}

    def three(self, **overrides):
        payload = {
            'licence_number': 'NG-PSY-4421',
            'insurance_provider': 'Leadway',
            'certification': a_file(),
            'identity': a_file('nin.jpg', b'\xff\xd8\xff test', 'image/jpeg'),
            'agreed': 'on',
        }
        payload.update(overrides)
        return {k: v for k, v in payload.items() if v is not None}

    def apply_fully(self):
        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())
        return self.client.post(self.step(3), self.three())

    # ----------------------------------------------------------- the pages

    def test_the_intro_is_the_app_s_own_first_screen(self):
        page = self.client.get(self.intro).content.decode()

        self.assertIn('Join the Soothify Expert Network', page)
        self.assertIn('Bring your practice to a global audience', page)
        self.assertIn('Start Application', page)
        # And it leads into the form rather than being the form.
        self.assertIn(self.step(1), page)

    def test_each_step_asks_what_the_app_asks(self):
        asked = {
            1: 'What are your primary areas of expertise?',
            2: 'Which languages are you completely fluent in for client sessions?',
            3: 'License / Certification Number',
        }

        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())
        for step, question in asked.items():
            page = self.client.get(self.step(step)).content.decode()
            self.assertIn(question, page, 'step {0}'.format(step))

    def test_the_progress_bar_fills_as_it_goes(self):
        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())

        first = self.client.get(self.step(1)).content.decode()
        third = self.client.get(self.step(3)).content.decode()

        self.assertEqual(first.count('xp-steps__seg is-done'), 1)
        self.assertEqual(third.count('xp-steps__seg is-done'), 3)

    def test_the_pages_are_reachable_in_pidgin(self):
        # Both addresses worked out before either request: a request to a
        # /pcm/ page leaves Pidgin active, and `reverse()` would then
        # prefix the next one a second time.
        intro, step_one = '/pcm' + self.intro, '/pcm' + self.step(1)

        self.assertEqual(self.client.get(intro).status_code, 200)
        self.assertEqual(self.client.get(step_one).status_code, 200)

    def test_the_footer_points_at_it(self):
        # A page nobody can find is not finished.
        landing = self.client.get(reverse('home:landing')).content.decode()

        self.assertIn(self.intro, landing)

    # --------------------------------------------------------- the answers

    def test_an_application_is_saved_with_both_documents(self):
        response = self.apply_fully()

        self.assertRedirects(response, self.done)
        application = Application.objects.get()
        self.assertEqual(application.full_name, 'Dera Eze')
        self.assertEqual(application.status, 'submitted')
        self.assertEqual(application.source, 'web')
        # No account: applying should not require making one first.
        self.assertIsNone(application.user)
        self.assertEqual(
            set(application.documents.values_list('kind', flat=True)),
            {'certification', 'identity'},
        )

    def test_the_first_two_steps_survive_the_third(self):
        self.apply_fully()

        application = Application.objects.get()
        self.assertEqual(application.disciplines, ['therapy', 'yoga'])
        self.assertEqual(application.languages, ['english', 'pidgin'])
        self.assertEqual(application.experience_years, 6)
        self.assertEqual(application.portfolio_url, 'https://dera.example')
        self.assertEqual(application.licence_number, 'NG-PSY-4421')

    def test_a_step_comes_back_filled_in(self):
        self.client.post(self.step(1), self.one())

        page = self.client.get(self.step(1)).content.decode()

        # Stepping back must not mean typing it again.
        self.assertIn('Dera Eze', page)
        self.assertIn('dera@example.com', page)

    def test_the_address_is_stored_lower_cased(self):
        self.apply_fully()

        # So the same person reads as the same person when the application
        # is matched to an account later.
        self.assertEqual(Application.objects.get().email, 'dera@example.com')

    def test_nothing_is_saved_until_the_last_step(self):
        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())

        # Two steps in, there is no half-application for anybody to read.
        self.assertFalse(Application.objects.exists())

    def test_the_steps_cannot_be_skipped(self):
        self.assertRedirects(self.client.get(self.step(2)), self.step(1))
        self.assertRedirects(self.client.get(self.step(3)), self.step(1))

        self.client.post(self.step(1), self.one())
        self.assertRedirects(self.client.get(self.step(3)), self.step(2))

    def test_starting_again_starts_again(self):
        self.client.post(self.step(1), self.one())

        self.client.get(self.intro)

        # A draft left from a previous visit would quietly pre-fill a form
        # the person thinks is blank.
        self.assertRedirects(self.client.get(self.step(2)), self.step(1))

    # ------------------------------------------------------- what follows

    def test_the_applicant_is_written_to(self):
        self.apply_fully()

        sent = [m for m in mail.outbox if 'dera@example.com' in m.to]
        self.assertEqual(len(sent), 1)
        self.assertIn('application', sent[0].subject.lower())
        self.assertIn('24 to 48 hours', sent[0].body)

    def test_the_acknowledgement_is_the_app_s_own_words(self):
        self.apply_fully()

        page = self.client.get(self.done).content.decode()

        self.assertIn('Application Submitted!', page)
        self.assertIn('within 24 to 48 hours once', page)
        self.assertIn('dera@example.com', page)

    def test_that_page_cannot_be_read_without_applying(self):
        self.assertRedirects(self.client.get(self.done), self.intro)

    # --------------------------------------------------------- refusals

    def test_a_step_with_no_choice_is_refused(self):
        response = self.client.post(self.step(1), self.one(disciplines=None))

        self.assertEqual(response.status_code, 200)
        self.assertIn('Choose at least one', response.content.decode())

        # And the same on the languages step, which has to be reached
        # before it can refuse anything.
        self.client.post(self.step(1), self.one())
        language = self.client.post(self.step(2), self.two(languages=None))

        self.assertEqual(language.status_code, 200)
        self.assertIn('Choose at least one', language.content.decode())

    def test_the_certificate_is_required(self):
        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())

        response = self.client.post(self.step(3), self.three(certification=None))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Application.objects.exists())

    def test_a_spreadsheet_is_not_a_certificate(self):
        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())

        response = self.client.post(self.step(3), self.three(
            certification=a_file('accounts.xlsx', b'PK', 'application/vnd.ms-excel'),
        ))

        self.assertIn('PDF, JPG or PNG', response.content.decode())
        self.assertFalse(Application.objects.exists())

    def test_a_file_too_large_is_refused_with_its_size(self):
        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())

        response = self.client.post(self.step(3), self.three(
            certification=a_file('scan.pdf', b'x' * (MAX_UPLOAD_BYTES + 1)),
        ))

        self.assertIn('Keep it under 10 MB', response.content.decode())
        self.assertFalse(Application.objects.exists())

    def test_nothing_is_sent_without_the_declaration(self):
        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())

        response = self.client.post(self.step(3), self.three(agreed=None))

        self.assertIn('Please confirm', response.content.decode())
        self.assertFalse(Application.objects.exists())

    def test_a_second_application_while_one_is_being_read_is_turned_away(self):
        self.apply_fully()

        response = self.client.post(self.step(1), self.one())

        self.assertIn('already have an application', response.content.decode())
        self.assertEqual(Application.objects.count(), 1)

    def test_applying_again_after_a_decision_is_allowed(self):
        self.apply_fully()
        Application.objects.update(status='rejected')

        self.apply_fully()

        # Somebody turned down once -- for a missing document, say -- is not
        # turned away for good.
        self.assertEqual(Application.objects.count(), 2)

    def test_a_filled_honeypot_is_refused(self):
        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())

        response = self.client.post(self.step(3), self.three(
            website='http://spam.example',
        ))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Application.objects.exists())

    def test_the_documents_are_not_public(self):
        self.apply_fully()

        document = ApplicationDocument.objects.first()

        # Stored out of the public media tree: these are the most sensitive
        # files the system holds, and they are served to staff through a
        # signed link rather than by path.
        self.assertTrue(document.file.name.startswith('private/'))

    # ------------------------------------------------- the app's own chrome

    def test_no_page_in_the_flow_carries_the_site_s_header_or_footer(self):
        # The app's screens have neither, and this is meant to be the same
        # flow -- a nav bar and a marketing footer around it would make the
        # website's version a different thing asking the same questions.
        self.client.post(self.step(1), self.one())
        self.client.post(self.step(2), self.two())

        # Asserted against a page that does carry them, so this cannot pass
        # by looking for markers that were never in the HTML.
        ordinary = self.client.get(reverse('home:privacy')).content.decode()
        for marker in CHROME:
            self.assertIn(marker, ordinary, marker)

        pages = [self.intro, self.step(1), self.step(2), self.step(3)]
        for url in pages:
            page = self.client.get(url).content.decode()
            for marker in CHROME:
                with self.subTest(url=url, marker=marker):
                    self.assertNotIn(marker, page)

    def test_the_only_way_back_is_the_flow_s_own(self):
        # Removing the header removes the site nav with it, so each screen
        # has to carry its own Back -- the app's does.
        intro = self.client.get(self.intro).content.decode()
        self.assertIn(reverse('home:landing'), intro)

        self.client.post(self.step(1), self.one())
        second = self.client.get(self.step(2)).content.decode()
        self.assertIn('xp-back', second)
        self.assertIn(self.step(1), second)
