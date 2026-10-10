"""The feed, its chips, the four switches, and push registration."""

from django.urls import reverse

from accounts.models import Profile
from api.testing import ApiTestCase
from notifications.models import Notification, Preferences, PushDevice


class NotificationTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.user = self.profile.user
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')

    def notify(self, **overrides):
        fields = {
            'user': self.user,
            'kind': 'social',
            'actor': 'Kendrick',
            'text': ' commented on your post...',
            'filters': 'whats_new',
        }
        fields.update(overrides)
        return Notification.objects.create(**fields)


class FeedTests(NotificationTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('api:notifications:feed')

    def test_needs_an_account(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_is_empty_for_a_new_account(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.json()['results'], [])

    def test_serves_every_kind_the_feed_draws(self):
        for kind in ('recommendation', 'social', 'reminder', 'digest', 'booking', 'breathe'):
            self.notify(kind=kind)

        kinds = {
            row['kind']
            for row in self.client.get(self.url, HTTP_AUTHORIZATION=self.token).json()['results']
        }

        self.assertEqual(
            kinds,
            {'recommendation', 'social', 'reminder', 'digest', 'booking', 'breathe'},
        )

    def test_a_reminder_carries_the_run_the_design_sets_in_bold(self):
        self.notify(
            kind='reminder', title='Reminder',
            body='You have Yoga session today at 5:00pm', emphasis='Yoga',
            filters='sessions',
        )

        row = self.client.get(self.url, HTTP_AUTHORIZATION=self.token).json()['results'][0]

        self.assertEqual(row['emphasis'], 'Yoga')

    def test_the_sessions_chip_narrows_to_session_entries(self):
        self.notify(filters='whats_new')
        self.notify(kind='booking', title='Booking', filters='sessions')

        response = self.client.get(
            '{0}?filter=sessions'.format(self.url), HTTP_AUTHORIZATION=self.token,
        )

        rows = response.json()['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'booking')

    def test_the_all_chip_narrows_nothing(self):
        self.notify(filters='whats_new')
        self.notify(kind='booking', filters='sessions')

        response = self.client.get(
            '{0}?filter=all'.format(self.url), HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(len(response.json()['results']), 2)

    def test_newest_first(self):
        self.notify(text=' first')
        self.notify(text=' second')

        rows = self.client.get(self.url, HTTP_AUTHORIZATION=self.token).json()['results']

        self.assertEqual(rows[0]['text'], ' second')

    def test_one_persons_feed_is_not_anothers(self):
        self.notify()
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.json()['results'], [])


class ReadStateTests(NotificationTestCase):
    def test_the_bell_counts_the_unread(self):
        self.notify()
        self.notify(unread=False)

        response = self.client.get(
            reverse('api:notifications:unread'), HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.json()['unread'], 1)

    def test_one_can_be_marked_read(self):
        entry = self.notify()

        self.client.post(
            reverse('api:notifications:read', args=[entry.pk]),
            HTTP_AUTHORIZATION=self.token,
        )

        entry.refresh_from_db()
        self.assertFalse(entry.unread)

    def test_the_whole_feed_can_be_marked_read(self):
        self.notify()
        self.notify()

        response = self.client.post(
            reverse('api:notifications:read-all'), HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.json()['marked'], 2)
        self.assertEqual(Notification.objects.filter(unread=True).count(), 0)

    def test_nobody_can_mark_someone_elses_as_read(self):
        other = Profile.create_user(email='bola@example.com', password='Soothify2026')
        entry = Notification.objects.create(user=other.user, kind='social')

        self.client.post(
            reverse('api:notifications:read', args=[entry.pk]),
            HTTP_AUTHORIZATION=self.token,
        )

        entry.refresh_from_db()
        self.assertTrue(entry.unread)


class PreferenceTests(NotificationTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('api:notifications:preferences')

    def test_all_four_start_off(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(
            response.json(),
            {
                'notify_balance': False, 'notify_meditation': False,
                'notify_mood': False, 'notify_offers': False,
            },
        )

    def test_a_switch_can_be_turned_on_and_stays_on(self):
        self.client.patch(
            self.url, {'notify_mood': True}, content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertTrue(response.json()['notify_mood'])
        self.assertTrue(Preferences.objects.get(user=self.user).notify_mood)

    def test_turning_one_on_leaves_the_others_alone(self):
        self.client.patch(
            self.url, {'notify_offers': True}, content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertTrue(response.json()['notify_offers'])
        self.assertFalse(response.json()['notify_balance'])

    def test_preferences_are_per_person(self):
        self.client.patch(
            self.url, {'notify_mood': True}, content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertFalse(response.json()['notify_mood'])


class PushDeviceTests(NotificationTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('api:notifications:devices')

    def register(self, token='fcm-token-1', platform='android', auth=None):
        return self.client.post(
            self.url, {'token': token, 'platform': platform},
            content_type='application/json',
            HTTP_AUTHORIZATION=auth or self.token,
        )

    def test_a_phone_registers_for_push(self):
        response = self.register()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(PushDevice.objects.count(), 1)

    def test_registering_the_same_phone_twice_keeps_one_row(self):
        self.register()

        response = self.register()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushDevice.objects.count(), 1)

    def test_a_handed_on_phone_follows_its_new_owner(self):
        self.register()
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        self.register(auth=self.signed_in_as('bola@example.com', 'Soothify2026'))

        device = PushDevice.objects.get()
        self.assertEqual(device.user.email, 'bola@example.com')

    def test_a_phone_can_be_unregistered(self):
        self.register()

        response = self.client.delete(
            self.url, {'token': 'fcm-token-1'}, content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(PushDevice.objects.count(), 0)
