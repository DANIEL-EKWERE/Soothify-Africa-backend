"""Recommendations, and the questionnaire they come from.

The thing worth pinning down is that none of this needs an account. The
questionnaire is answered before anybody has one, so an anonymous request
carrying its own answers has to be personalised exactly as a signed-in one
is -- and a request carrying nothing must still come back with something.
"""

from accounts.models import KycResponse, Profile
from api.testing import ApiTestCase
from content.models import Category, MediaItem

URL = '/api/v1/content/recommendations/'


class RecommendationTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        for key in ('calm', 'sleep', 'focus', 'breathe', 'stories'):
            Category.objects.create(id=key, name=key.title())

        self.made = {}
        for key in ('calm', 'sleep', 'focus', 'breathe', 'stories'):
            self.made[key] = MediaItem.objects.create(
                title='A {0} piece'.format(key),
                category_id=key,
                is_published=True,
            )
        # The staff-picked fallback, in no category at all.
        self.picked = MediaItem.objects.create(
            title='Staff pick', is_published=True, is_recommended=True,
        )

    def titles(self, response):
        return [row['title'] for row in response.json()]

    def categories(self, response):
        return [
            self.made_by_title[row['title']]
            for row in response.json()
            if row['title'] in self.made_by_title
        ]

    @property
    def made_by_title(self):
        return {item.title: key for key, item in self.made.items()}

    # ------------------------------------------------------- anonymous

    def test_a_stranger_can_ask_at_all(self):
        response = self.client.get(URL)

        self.assertEqual(response.status_code, 200)

    def test_a_stranger_who_sends_their_answers_is_answered_by_them(self):
        # Nobody is signed in. This is the onboarding case: the answers live
        # on the device and travel with the request.
        response = self.client.post(URL, {
            'concerns': ['sleep_disorder'],
        }, content_type='application/json')

        self.assertEqual(self.categories(response), ['sleep', 'stories'])

    def test_the_strongest_signal_leads(self):
        response = self.client.post(URL, {
            'concerns': ['sleep_disorder'],
            'goals': ['clarity'],
        }, content_type='application/json')

        # Concerns before goals, and within each, the order the map implies
        # -- not the order the database happens to return.
        self.assertEqual(
            self.categories(response), ['sleep', 'stories', 'focus', 'breathe'],
        )

    def test_one_answer_sent_as_a_bare_string_still_works(self):
        response = self.client.post(URL, {
            'concerns': 'stress',
        }, content_type='application/json')

        self.assertEqual(self.categories(response), ['calm', 'breathe'])

    # -------------------------------------------------------- signed in

    def a_member_who_answered(self, answers):
        user = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada',
        ).user
        KycResponse.objects.create(user=user, answers=answers)
        return self.signed_in_as('ada@example.com', 'Soothify2026')

    def test_a_signed_in_person_needs_to_send_nothing(self):
        token = self.a_member_who_answered({'goals': ['peace']})

        response = self.client.get(URL, HTTP_AUTHORIZATION=token)

        self.assertEqual(self.categories(response), ['calm', 'sleep'])

    def test_what_is_sent_wins_over_what_is_stored(self):
        # Re-answering on the device should change the screen at once,
        # without waiting for the account to catch up.
        token = self.a_member_who_answered({'goals': ['peace']})

        response = self.client.post(URL, {
            'concerns': ['anxiety'],
        }, content_type='application/json', HTTP_AUTHORIZATION=token)

        self.assertEqual(self.categories(response), ['breathe', 'calm'])

    # -------------------------------------------------------- fallbacks

    def test_nothing_to_go_on_means_the_staff_picks(self):
        response = self.client.get(URL)

        self.assertEqual(self.titles(response), ['Staff pick'])

    def test_an_answer_that_narrows_nothing_falls_back(self):
        # "Something else" says only that the five did not fit.
        response = self.client.post(URL, {
            'goals': ['something_else'],
        }, content_type='application/json')

        self.assertEqual(self.titles(response), ['Staff pick'])

    def test_an_option_we_have_never_heard_of_is_ignored_not_fatal(self):
        # The app can ship an option before the server knows it.
        response = self.client.post(URL, {
            'concerns': ['something_new_entirely'],
        }, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.titles(response), ['Staff pick'])

    def test_an_empty_library_for_that_interest_falls_back(self):
        MediaItem.objects.filter(category_id__in=('sleep', 'stories')).delete()

        response = self.client.post(URL, {
            'concerns': ['sleep_disorder'],
        }, content_type='application/json')

        # Better the staff list than a blank screen.
        self.assertEqual(self.titles(response), ['Staff pick'])

    def test_an_unpublished_piece_is_never_recommended(self):
        MediaItem.objects.filter(category_id='sleep').update(is_published=False)

        response = self.client.post(URL, {
            'concerns': ['sleep_disorder'],
        }, content_type='application/json')

        self.assertEqual(self.categories(response), ['stories'])
