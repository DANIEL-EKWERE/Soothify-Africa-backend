"""Spaces Around Me, and the Passport waitlist behind the plan card."""

from django.urls import reverse

from accounts.models import Profile
from api.testing import ApiTestCase
from spaces.models import PassportWaitlistEntry, WellnessSpace


def make_space(pk='sage-and-she', **overrides):
    fields = {
        'id': pk,
        'name': 'Sage & She',
        'area': 'Lekki Phase 1',
        'city': 'Lagos',
        'category': 'pilates',
        'tags': ['Reformer Pilates', 'Mat Pilates'],
        'latitude': 6.4474,
        'longitude': 3.4730,
    }
    fields.update(overrides)
    return WellnessSpace.objects.create(**fields)


class SpaceListTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('api:spaces:list')
        make_space()
        make_space('asana-house', name='Asana House', area='Maitama',
                   city='Abuja', category='yoga', tags=['Hatha'],
                   latitude=9.0820, longitude=7.4913)

    def test_a_guest_can_browse_the_studios(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

    def test_the_all_chip_filters_nothing(self):
        response = self.client.get('{0}?category=all'.format(self.url))

        self.assertEqual(len(response.json()), 2)

    def test_a_chip_narrows_to_its_category(self):
        response = self.client.get('{0}?category=yoga'.format(self.url))

        self.assertEqual([row['name'] for row in response.json()], ['Asana House'])

    def test_search_matches_name_area_or_tag(self):
        by_area = self.client.get('{0}?q=maitama'.format(self.url)).json()
        by_tag = self.client.get('{0}?q=reformer'.format(self.url)).json()

        self.assertEqual([row['name'] for row in by_area], ['Asana House'])
        self.assertEqual([row['name'] for row in by_tag], ['Sage & She'])

    def test_distance_is_measured_from_where_the_phone_says_it_is(self):
        # Victoria Island, a few kilometres from the Lekki studio.
        response = self.client.get('{0}?lat=6.4281&lng=3.4219'.format(self.url))

        rows = {row['name']: row['distance_km'] for row in response.json()}
        self.assertLess(rows['Sage & She'], 10)
        self.assertGreater(rows['Asana House'], 500)   # Lagos to Abuja

    def test_without_a_location_a_card_claims_no_distance(self):
        response = self.client.get(self.url)

        self.assertIsNone(response.json()[0]['distance_km'])

    def test_an_unlisted_studio_is_not_served(self):
        make_space('closed', name='Closed Studio', is_active=False)

        names = [row['name'] for row in self.client.get(self.url).json()]

        self.assertNotIn('Closed Studio', names)

    def test_a_studio_profile_carries_its_contact_details(self):
        make_space('with-contacts', name='Contactable', instagram='@sageandshe',
                   phone='+2348030000000')

        response = self.client.get(
            reverse('api:spaces:detail', args=['with-contacts']),
        )

        self.assertEqual(response.json()['instagram'], '@sageandshe')
        self.assertEqual(response.json()['phone'], '+2348030000000')


class PassportWaitlistTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('api:spaces:passport-waitlist')

    def join(self, email='ada@example.com', token=None, **extra):
        payload = {'email': email}
        payload.update(extra)
        headers = {'HTTP_AUTHORIZATION': token} if token else {}
        return self.client.post(
            self.url, payload, content_type='application/json', **headers
        )

    def test_a_guest_can_reserve_a_place(self):
        response = self.join(city='Lagos', source='plans')

        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()['already_on_list'])
        entry = PassportWaitlistEntry.objects.get()
        self.assertEqual(entry.city, 'Lagos')
        self.assertIsNone(entry.user_id)

    def test_asking_twice_says_so_rather_than_failing(self):
        self.join()

        response = self.join()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['already_on_list'])
        self.assertEqual(PassportWaitlistEntry.objects.count(), 1)

    def test_the_address_is_stored_lower_case(self):
        self.join(email='Ada@Example.COM')

        self.assertEqual(PassportWaitlistEntry.objects.get().email, 'ada@example.com')

    def test_a_members_entry_is_tied_to_their_account(self):
        Profile.create_user(email='ada@example.com', password='Soothify2026')
        token = self.signed_in_as('ada@example.com', 'Soothify2026')

        self.join(token=token)

        self.assertIsNotNone(PassportWaitlistEntry.objects.get().user_id)

    def test_a_guest_entry_is_claimed_when_they_later_sign_up(self):
        self.join()
        Profile.create_user(email='ada@example.com', password='Soothify2026')
        token = self.signed_in_as('ada@example.com', 'Soothify2026')

        self.join(token=token)

        self.assertEqual(PassportWaitlistEntry.objects.count(), 1)
        self.assertIsNotNone(PassportWaitlistEntry.objects.get().user_id)

    def test_a_malformed_address_is_refused(self):
        self.assertEqual(self.join(email='not-an-email').status_code, 400)

    def test_this_list_is_separate_from_the_website_waitlist(self):
        from home.models import WaitlistSignup

        self.join()

        self.assertEqual(WaitlistSignup.objects.count(), 0)
