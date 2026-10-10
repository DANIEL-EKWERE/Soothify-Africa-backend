"""Booking a session, and the practitioner's own screens.

The rules under test are the ones that cost money or privacy: a slot cannot
be double-booked, an unpaid hold expires, only the two people on a session can
join its call, and a client never sees the practitioner's notes.
"""

import datetime

from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, Role
from api.testing import ApiTestCase
from experts.models import (
    AvailabilitySlot,
    ExpertProfile,
    Payout,
    Session,
    SessionNote,
)


def make_expert(email='coach@example.com', name='Baraqhat Ibrahim',
                disciplines=('therapy',), **overrides):
    profile = Profile.create_user(
        email=email, password='Soothify2026', full_name=name, role=Role.PRACTITIONER,
    )
    fields = {
        'profile': profile,
        'disciplines': list(disciplines),
        'languages': ['english'],
    }
    fields.update(overrides)
    return ExpertProfile.objects.create(**fields)


def open_slot(expert, day=None, start_minute=17 * 60):
    """17:00 on a day a week out, which is never in the past."""
    day = day or (timezone.localdate() + datetime.timedelta(days=7))
    return AvailabilitySlot.objects.create(
        expert=expert, day=day, start_minute=start_minute,
        end_minute=start_minute + 60,
    )


class BookingTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.expert = make_expert()
        self.slot = open_slot(self.expert)
        Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')
        self.url = reverse('api:experts:sessions')

    def book(self, token=None, **overrides):
        payload = {
            'discipline': 'therapy',
            'day': str(self.slot.day),
            'start_minute': self.slot.start_minute,
            'mode': 'video',
            'plan': 'single',
        }
        payload.update(overrides)
        return self.client.post(
            self.url, payload, content_type='application/json',
            HTTP_AUTHORIZATION=token or self.token,
        )


class MatchingTests(BookingTestCase):
    def test_matches_an_expert_who_practises_what_was_asked_for(self):
        make_expert('yogi@example.com', 'Yoga Only', disciplines=('yoga',))

        response = self.client.get(
            '{0}?discipline=therapy&day={1}&start_minute={2}'.format(
                reverse('api:experts:match'), self.slot.day, self.slot.start_minute,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'Baraqhat Ibrahim')

    def test_the_match_percentage_is_earned_rather_than_decorative(self):
        response = self.client.get(
            '{0}?discipline=therapy&day={1}&start_minute={2}&language=english'.format(
                reverse('api:experts:match'), self.slot.day, self.slot.start_minute,
            ),
        )

        # Practises it (40) + free then (30) + speaks the language (15) + an
        # empty week (5). No rating yet, so no rating points.
        self.assertEqual(response.json()['match_percent'], 90)

    def test_says_so_when_nobody_is_free(self):
        response = self.client.get(
            '{0}?discipline=therapy&day={1}&start_minute={2}'.format(
                reverse('api:experts:match'), self.slot.day, 9 * 60,
            ),
        )

        self.assertEqual(response.status_code, 404)

    def test_an_expert_not_taking_clients_is_not_matched(self):
        self.expert.is_accepting = False
        self.expert.save(update_fields=['is_accepting'])

        response = self.client.get(
            '{0}?discipline=therapy'.format(reverse('api:experts:match')),
        )

        self.assertEqual(response.status_code, 404)


class BookingTests(BookingTestCase):
    def test_booking_reserves_the_slot_but_does_not_confirm_it(self):
        response = self.book()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['status'], 'pending')
        self.assertEqual(body['price_naira'], 25000)
        self.assertIsNotNone(Session.objects.get().expires_at)

    def test_a_session_runs_for_fifty_minutes(self):
        self.book()

        session = Session.objects.get()
        self.assertEqual((session.ends_at - session.starts_at).seconds // 60, 50)

    def test_prices_come_from_the_discipline_and_the_plan(self):
        yoga = make_expert('yogi@example.com', 'Yoga Coach', disciplines=('yoga',))
        slot = open_slot(yoga, start_minute=11 * 60)

        response = self.book(
            discipline='yoga', day=str(slot.day), start_minute=slot.start_minute,
            plan='monthly',
        )

        self.assertEqual(response.json()['price_naira'], 36000)

    def test_the_same_hour_cannot_be_booked_twice(self):
        self.book()
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.book(token=self.signed_in_as('bola@example.com', 'Soothify2026'))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Session.objects.count(), 1)

    def test_a_booked_hour_disappears_from_the_calendar(self):
        self.book()

        response = self.client.get(
            reverse('api:experts:slots', args=[self.expert.pk]),
        )

        self.assertEqual(response.json(), [])

    def test_an_hour_the_expert_never_opened_cannot_be_booked(self):
        response = self.book(start_minute=3 * 60)

        self.assertEqual(response.status_code, 400)

    def test_a_day_in_the_past_is_refused(self):
        response = self.book(
            day=str(timezone.localdate() - datetime.timedelta(days=1)),
        )

        self.assertEqual(response.status_code, 400)

    def test_an_unpaid_hold_is_released_so_the_hour_frees_up(self):
        from experts.tasks import release_expired_holds

        self.book()
        Session.objects.update(expires_at=timezone.now() - datetime.timedelta(minutes=1))

        released = release_expired_holds()

        self.assertEqual(released, 1)
        self.assertEqual(Session.objects.get().status, 'cancelled')

    def test_booking_needs_an_account(self):
        response = self.client.post(
            self.url, {'discipline': 'therapy'}, content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)

    def test_you_only_see_your_own_sessions(self):
        self.book()
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.json()['results'], [])


class CallTokenTests(BookingTestCase):
    def setUp(self):
        super().setUp()
        self.session = Session.objects.create(
            client=Profile.objects.get(user__email='ada@example.com').user,
            expert=self.expert,
            discipline='therapy',
            starts_at=timezone.now() + datetime.timedelta(minutes=5),
            ends_at=timezone.now() + datetime.timedelta(minutes=55),
            status='confirmed',
            price_naira=25000,
        )
        self.url = reverse('api:experts:session-call-token', args=[self.session.pk])

    def test_a_stranger_cannot_get_a_token_for_someone_elses_session(self):
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.client.post(
            self.url,
            HTTP_AUTHORIZATION=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.status_code, 404)

    def test_the_room_does_not_open_days_early(self):
        Session.objects.update(
            starts_at=timezone.now() + datetime.timedelta(days=2),
            ends_at=timezone.now() + datetime.timedelta(days=2, minutes=50),
        )

        response = self.client.post(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.status_code, 400)

    def test_an_unconfirmed_session_has_no_room(self):
        Session.objects.update(status='pending')

        response = self.client.post(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.status_code, 400)

    def test_without_credentials_it_says_so_rather_than_minting_nonsense(self):
        from django.test import override_settings

        # Cleared explicitly: this deployment has real credentials set, and a
        # test that only passes on a machine without them is no test at all.
        with override_settings(ZEGO_APP_ID='', ZEGO_SERVER_SECRET=''):
            response = self.client.post(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.status_code, 503)
        self.assertIn('ZEGO_APP_ID', response.json()['detail'])

    def test_a_token_is_minted_once_the_credentials_are_set(self):
        from django.test import override_settings

        with override_settings(ZEGO_APP_ID='123456789', ZEGO_SERVER_SECRET='a' * 32):
            response = self.client.post(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['token'].startswith('04'))
        self.assertEqual(response.json()['room_id'], 'session-{0}'.format(self.session.pk))


class PractitionerTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.expert = make_expert()
        self.token = self.signed_in_as('coach@example.com', 'Soothify2026')
        Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.client_token = self.signed_in_as('ada@example.com', 'Soothify2026')

    def test_a_client_cannot_reach_the_practitioner_screens(self):
        response = self.client.get(
            reverse('api:experts:my-earnings'), HTTP_AUTHORIZATION=self.client_token,
        )

        self.assertEqual(response.status_code, 403)

    def test_an_expert_sets_their_own_availability(self):
        day = timezone.localdate() + datetime.timedelta(days=3)

        response = self.client.post(
            reverse('api:experts:my-availability'),
            {'day': str(day), 'start_minute': 540, 'end_minute': 600},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(AvailabilitySlot.objects.get().expert, self.expert)

    def test_a_slot_that_ends_before_it_starts_is_refused(self):
        response = self.client.post(
            reverse('api:experts:my-availability'),
            {'day': str(timezone.localdate()), 'start_minute': 600, 'end_minute': 540},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.status_code, 400)

    def test_earnings_are_counted_from_completed_sessions(self):
        ada = Profile.objects.get(user__email='ada@example.com').user
        for _ in range(3):
            Session.objects.create(
                client=ada, expert=self.expert, discipline='therapy',
                starts_at=timezone.now() - datetime.timedelta(days=1),
                ends_at=timezone.now() - datetime.timedelta(days=1),
                status='completed', price_naira=25000,
            )
        Payout.objects.create(
            expert=self.expert, amount_naira=25000, status='sent',
            paid_on=timezone.localdate(),
        )

        response = self.client.get(
            reverse('api:experts:my-earnings'), HTTP_AUTHORIZATION=self.token,
        )

        body = response.json()
        self.assertEqual(body['total_earned'], 75000)
        self.assertEqual(body['total_sessions'], 3)
        self.assertEqual(body['available'], 50000)   # less what was paid out

    def test_a_new_expert_earns_nothing_rather_than_a_sample_figure(self):
        response = self.client.get(
            reverse('api:experts:my-earnings'), HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.json()['total_earned'], 0)
        self.assertEqual(response.json()['available'], 0)

    def test_notes_are_the_practitioners_and_the_client_never_sees_them(self):
        ada = Profile.objects.get(user__email='ada@example.com').user
        session = Session.objects.create(
            client=ada, expert=self.expert, discipline='therapy',
            starts_at=timezone.now(), ends_at=timezone.now(), status='completed',
        )
        url = reverse('api:experts:session-note', args=[session.pk])

        written = self.client.put(
            url, {'body': 'Discussed sleep; follow up in two weeks.'},
            content_type='application/json', HTTP_AUTHORIZATION=self.token,
        )
        read_by_client = self.client.get(url, HTTP_AUTHORIZATION=self.client_token)

        self.assertEqual(written.status_code, 200)
        self.assertEqual(read_by_client.status_code, 403)
        self.assertEqual(SessionNote.objects.count(), 1)


class RatingTests(BookingTestCase):
    def setUp(self):
        super().setUp()
        self.session = Session.objects.create(
            client=Profile.objects.get(user__email='ada@example.com').user,
            expert=self.expert, discipline='therapy',
            starts_at=timezone.now() - datetime.timedelta(hours=2),
            ends_at=timezone.now() - datetime.timedelta(hours=1),
            status='completed', price_naira=25000,
        )
        self.url = reverse('api:experts:session-rating', args=[self.session.pk])

    def test_rating_a_finished_session_updates_the_experts_score(self):
        response = self.client.post(
            self.url, {'rating': 5, 'feedback': 'Really helpful'},
            content_type='application/json', HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.expert.refresh_from_db()
        self.assertEqual(self.expert.rating_average, 5.0)

    def test_a_session_that_has_not_happened_cannot_be_rated(self):
        Session.objects.update(status='confirmed')

        response = self.client.post(
            self.url, {'rating': 5}, content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.status_code, 400)
