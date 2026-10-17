"""The staff dashboard.

The first class is the important one: every page here shows other people's
data -- their email addresses, their bookings, flags raised on their therapy
sessions -- so "can a logged-in stranger reach this" is asked of each URL
rather than assumed from the decorator.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.models import PasswordResetCode, Profile, Role
from api.testing import ApiTestCase
from content.models import Category, MediaItem, Shelf, ShelfItem
from experts.models import Application, ApplicationDocument, ExpertProfile
from home.models import WaitlistSignup

PAGES = (
    '/dashboard/',
    '/dashboard/waitlist/',
    '/dashboard/waitlist/export/',
    '/dashboard/media/',
    '/dashboard/media/new/',
    '/dashboard/sessions/',
    '/dashboard/recordings/',
    '/dashboard/people/',
    '/dashboard/applications/',
)


def make_staff(email='staff@soothifyafrica.com.ng'):
    profile = Profile.create_user(
        email=email, password='Soothify2026', full_name='Rita Peter',
    )
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])
    return profile.user


class AccessTests(ApiTestCase):
    def test_a_stranger_is_sent_to_sign_in(self):
        for url in PAGES:
            response = self.client.get(url)

            self.assertEqual(response.status_code, 302, msg=url)
            self.assertIn('/dashboard/login/', response['Location'], msg=url)

    def test_an_ordinary_account_cannot_get_in(self):
        """A member with a password is not a member of staff."""
        Profile.create_user(email='ada@example.com', password='Soothify2026')
        self.client.login(username='ada@example.com', password='Soothify2026')

        for url in PAGES:
            response = self.client.get(url)

            self.assertEqual(response.status_code, 302, msg=url)
            self.assertIn('/dashboard/login/', response['Location'], msg=url)

    def test_staff_can_open_every_page(self):
        self.client.force_login(make_staff())

        for url in PAGES:
            response = self.client.get(url)

            self.assertEqual(response.status_code, 200, msg=url)

    def test_posting_without_staff_changes_nothing(self):
        item = MediaItem.objects.create(title='Daily focus', is_published=True)
        Profile.create_user(email='ada@example.com', password='Soothify2026')
        self.client.login(username='ada@example.com', password='Soothify2026')

        self.client.post('/dashboard/media/{0}/publish/'.format(item.pk))

        item.refresh_from_db()
        self.assertTrue(item.is_published)


class StaffTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_staff()
        self.client.force_login(self.staff)


class OverviewTests(StaffTestCase):
    def test_it_counts_what_is_there(self):
        WaitlistSignup.objects.create(name='Ada Obi', email='ada@example.com')
        MediaItem.objects.create(title='Breath work', is_published=True)

        response = self.client.get('/dashboard/')

        cards = {card['label']: card['value'] for card in response.context['cards']}
        self.assertEqual(cards['Waitlist'], 1)
        self.assertEqual(cards['In the library'], 1)

    def test_an_empty_install_says_so_rather_than_breaking(self):
        response = self.client.get('/dashboard/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'The library is empty')

    def test_the_chart_covers_a_fortnight_whether_or_not_anyone_signed_up(self):
        response = self.client.get('/dashboard/')

        bars = response.context['bars']
        self.assertEqual(len(bars), 14)
        self.assertTrue(all(bar['height'] == 0 for bar in bars))

    def test_a_first_week_shows_no_trend_rather_than_a_fake_zero(self):
        WaitlistSignup.objects.create(name='Ada Obi', email='ada@example.com')

        response = self.client.get('/dashboard/')

        waitlist_card = response.context['cards'][0]
        self.assertIsNone(waitlist_card['trend'])


class WaitlistTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        WaitlistSignup.objects.create(
            name='Ada Obi', email='ada@example.com', source='social', wants_updates=True,
        )
        WaitlistSignup.objects.create(
            name='Paul Eze', email='paul@example.com', source='friend',
        )

    def test_it_lists_the_signups(self):
        response = self.client.get('/dashboard/waitlist/')

        self.assertContains(response, 'ada@example.com')
        self.assertContains(response, 'paul@example.com')

    def test_search_narrows_by_name_or_email(self):
        response = self.client.get('/dashboard/waitlist/?q=paul')

        self.assertContains(response, 'paul@example.com')
        self.assertNotContains(response, 'ada@example.com')

    def test_the_csv_holds_every_row(self):
        response = self.client.get('/dashboard/waitlist/export/')

        body = response.content.decode()
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('ada@example.com', body)
        self.assertIn('paul@example.com', body)
        self.assertIn('attachment; filename=', response['Content-Disposition'])

    def test_the_passport_list_is_shown_separately(self):
        from spaces.models import PassportWaitlistEntry

        PassportWaitlistEntry.objects.create(email='passport@example.com', city='Lagos')

        response = self.client.get('/dashboard/waitlist/')

        self.assertContains(response, 'passport@example.com')
        self.assertContains(response, 'do not mail them together')


class UploadTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.category = Category.objects.create(id='calm', name='Calm')
        self.shelf = Shelf.objects.create(key='sounds', title='Sounds')

    def upload(self, **overrides):
        payload = {
            'title': 'Evening wind-down',
            'subtitle': 'Sleep',
            'tag': 'Sounds',
            'description': '',
            'media_type': 'audio',
            'duration_seconds': 1800,
            'external_url': 'https://cdn.example/audio.mp3',
            'category': 'calm',
            'credited_name': 'Jacob Samuel',
            'is_free': 'on',
            'is_published': 'on',
        }
        payload.update(overrides)
        return self.client.post('/dashboard/media/new/', payload)

    def test_uploading_adds_it_to_the_library(self):
        response = self.upload()

        self.assertEqual(response.status_code, 302)
        item = MediaItem.objects.get()
        self.assertEqual(item.title, 'Evening wind-down')
        self.assertEqual(item.category_id, 'calm')
        self.assertTrue(item.is_published)

    def test_a_real_file_can_be_uploaded(self):
        audio = SimpleUploadedFile('wind.mp3', b'not really audio', content_type='audio/mpeg')

        self.upload(media_file=audio, external_url='')

        self.assertTrue(MediaItem.objects.get().media_file.name.endswith('.mp3'))

    def test_something_with_no_file_and_no_address_is_refused(self):
        response = self.upload(external_url='')

        self.assertEqual(response.status_code, 200)       # the form comes back
        self.assertEqual(MediaItem.objects.count(), 0)
        self.assertContains(response, 'Upload a file, or give the address')

    def test_an_article_needs_its_text_but_no_file(self):
        refused = self.upload(media_type='article', external_url='', description='')
        self.assertEqual(MediaItem.objects.count(), 0)
        self.assertContains(refused, 'An article needs its text here.')

        self.upload(
            media_type='article', external_url='',
            description='Three ways to settle before bed.',
        )

        self.assertEqual(MediaItem.objects.get().media_type, 'article')

    def test_choosing_shelves_puts_it_on_them(self):
        self.upload(shelves=[self.shelf.pk])

        self.assertEqual(ShelfItem.objects.count(), 1)
        self.assertEqual(ShelfItem.objects.get().shelf, self.shelf)

    def test_editing_can_take_it_off_a_shelf_again(self):
        self.upload(shelves=[self.shelf.pk])
        item = MediaItem.objects.get()

        self.client.post('/dashboard/media/{0}/'.format(item.pk), {
            'title': item.title, 'subtitle': '', 'tag': '', 'description': '',
            'media_type': 'audio', 'duration_seconds': 1800,
            'external_url': 'https://cdn.example/audio.mp3',
            'category': 'calm', 'credited_name': '', 'is_published': 'on',
        })

        self.assertEqual(ShelfItem.objects.count(), 0)

    def test_publishing_can_be_toggled_from_the_list(self):
        self.upload()
        item = MediaItem.objects.get()

        self.client.post('/dashboard/media/{0}/publish/'.format(item.pk))

        item.refresh_from_db()
        self.assertFalse(item.is_published)

    def test_a_draft_is_not_served_to_the_app(self):
        self.upload(is_published='')

        response = self.client.get(reverse('api:content:items'))

        self.assertEqual(response.json()['results'], [])


class LibraryTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        MediaItem.objects.create(title='Daily focus', media_type='audio')
        MediaItem.objects.create(title='Morning flow', media_type='video')

    def test_the_kind_filter_narrows_the_list(self):
        response = self.client.get('/dashboard/media/?type=video')

        self.assertContains(response, 'Morning flow')
        self.assertNotContains(response, 'Daily focus')

    def test_search_finds_by_title(self):
        response = self.client.get('/dashboard/media/?q=daily')

        self.assertContains(response, 'Daily focus')
        self.assertNotContains(response, 'Morning flow')


class LookTests(StaffTestCase):
    """It should read as the same product as the landing page."""

    def test_pages_use_the_dashboard_stylesheet_and_the_brand_face(self):
        response = self.client.get('/dashboard/')

        self.assertContains(response, 'css/dashboard.css')
        self.assertContains(response, 'Nunito+Sans')

    def test_the_current_page_is_marked_in_the_sidebar(self):
        response = self.client.get('/dashboard/waitlist/')

        self.assertContains(response, 'nav__item is-on')

    def test_the_recordings_page_warns_before_anyone_listens(self):
        response = self.client.get('/dashboard/recordings/')

        self.assertContains(response, 'recorded against your name')


class ApplicationReviewTests(StaffTestCase):
    """Reading an application, and deciding on it.

    Approving is the part that matters: it is what lets somebody take
    sessions with clients, so it has to do the whole job -- the role, the
    expert profile, and a way in for an applicant who has no account.
    """

    def setUp(self):
        super().setUp()
        self.application = Application.objects.create(
            full_name='Dera Eze',
            email='dera@example.com',
            phone='+2348030000000',
            source='web',
            disciplines=['therapy', 'yoga'],
            languages=['english'],
            experience_years=6,
            bio='Ten years in private practice.',
        )
        ApplicationDocument.objects.create(
            application=self.application, kind='certification',
            file=SimpleUploadedFile('licence.pdf', b'%PDF-1.4', content_type='application/pdf'),
        )
        self.url = reverse('dashboard:application-detail', args=[self.application.pk])

    def decide(self, decision, notes=''):
        return self.client.post(
            reverse('dashboard:application-decide', args=[self.application.pk]),
            {'decision': decision, 'notes': notes},
        )

    def test_the_queue_lists_who_is_waiting(self):
        page = self.client.get('/dashboard/applications/').content.decode()

        self.assertIn('Dera Eze', page)
        self.assertIn('Therapy, Yoga', page)
        # Where it came from, because a website application has no account
        # behind it and that changes what approving does.
        self.assertIn('Website', page)

    def test_an_application_reads_in_full(self):
        page = self.client.get(self.url).content.decode()

        self.assertIn('Ten years in private practice.', page)
        self.assertIn('+2348030000000', page)
        # In words: "english, pidgin" is how it is stored, not how it reads.
        self.assertIn('English', page)
        self.assertNotIn('>english<', page)
        # The certificate, behind a link that expires: it goes through the
        # signed endpoint and carries a signature, rather than pointing at
        # the file where it sits.
        self.assertIn('Open', page)
        self.assertIn('/api/v1/media/', page)
        self.assertNotIn('href="/media/', page)
        self.assertNotIn('src="/media/', page)

    def test_approving_makes_them_a_practitioner(self):
        self.decide('approve', notes='Checked the register.')

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'approved')
        self.assertEqual(self.application.reviewed_by, self.staff)

        user = get_user_model().objects.get(email='dera@example.com')
        self.assertEqual(user.profile.role, Role.PRACTITIONER)
        expert = ExpertProfile.objects.get(profile=user.profile)
        # Everything they said they practise, not just the first.
        self.assertEqual(expert.disciplines, ['therapy', 'yoga'])

    def test_an_applicant_with_no_account_gets_one_without_a_password(self):
        self.decide('approve')

        user = get_user_model().objects.get(email='dera@example.com')
        # A password nobody chose is one somebody else might guess.
        self.assertFalse(user.has_usable_password())
        # And a code to choose one with.
        self.assertTrue(PasswordResetCode.objects.filter(user=user).exists())

    def test_approving_twice_does_not_make_a_second_expert(self):
        self.decide('approve')
        self.decide('approve')

        self.assertEqual(ExpertProfile.objects.count(), 1)

    def test_turning_one_down_keeps_the_reason_for_us(self):
        self.decide('reject', notes='Certificate is for a different person.')

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'rejected')
        self.assertIn('different person', self.application.review_notes)
        # Nobody is made a practitioner by being turned down.
        self.assertFalse(ExpertProfile.objects.exists())

    def test_the_applicant_is_never_shown_the_review_notes(self):
        self.decide('reject', notes='Too thin to judge.')

        # They are written for colleagues; the applicant hears from us in
        # our own words. Nothing in the API serves this field.
        from experts.serializers import ApplicationSerializer
        self.assertNotIn('review_notes', ApplicationSerializer.Meta.fields)

    def test_an_ordinary_member_cannot_decide(self):
        Profile.create_user(email='ada@example.com', password='Soothify2026')
        self.client.force_login(get_user_model().objects.get(email='ada@example.com'))

        response = self.decide('approve')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard/login/', response['Location'])
        self.assertFalse(ExpertProfile.objects.exists())
