"""Booking a session, and the practitioner's own screens.

The rules under test are the ones that cost money or privacy: a slot cannot
be double-booked, an unpaid hold expires, only the two people on a session can
join its call, and a client never sees the practitioner's notes.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, Role
from api.testing import ApiTestCase
from experts.models import (
    Application,
    AvailabilitySlot,
    ExpertProfile,
    Payout,
    PayoutMethod,
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


class ApplicationTests(ApiTestCase):
    """Becoming a practitioner: the form, its files, and what follows."""

    def setUp(self):
        super().setUp()
        Profile.create_user(
            email='dera@example.com', password='Soothify2026', full_name='Dera Eze',
        )
        self.token = self.signed_in_as('dera@example.com', 'Soothify2026')
        self.url = reverse('api:experts:applications')

    def document(self, name='licence.pdf', content=b'%PDF-1.4 fake'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def apply(self, token=None, **overrides):
        """As the app sends it: multipart, because the files travel with it."""
        payload = {
            'full_name': 'Dera Eze',
            # Repeated keys, which is all a multipart form can do with a list.
            'disciplines': ['therapy', 'yoga'],
            'languages': ['english', 'pidgin'],
            'experience_years': 6,
            'bio': 'Ten years in private practice.',
            'portfolio_url': 'https://dera.example',
            'licence_number': 'NG-PSY-4421',
            'insurance_provider': 'Leadway',
            'certification': self.document(),
            'identity': self.document('nin.jpg', b'\xff\xd8\xff fake'),
        }
        payload.update(overrides)
        return self.client.post(
            self.url, payload, HTTP_AUTHORIZATION=token or self.token,
        )

    def test_an_application_arrives_with_both_documents(self):
        response = self.apply()

        self.assertEqual(response.status_code, 201)
        application = Application.objects.get()
        self.assertEqual(application.full_name, 'Dera Eze')
        self.assertEqual(application.status, 'submitted')
        self.assertEqual(
            {doc.kind for doc in application.documents.all()},
            {'certification', 'identity'},
        )

    def test_more_than_one_discipline_survives_the_form(self):
        # A multipart form repeats the key rather than nesting JSON. Reading
        # only the last value is how a therapist who also teaches yoga used
        # to arrive as a yoga teacher.
        self.apply()

        self.assertEqual(
            Application.objects.get().disciplines, ['therapy', 'yoga'],
        )

    def test_a_comma_joined_list_is_read_the_same_way(self):
        # Which is what an HTML form with one hidden input sends -- the web
        # version of this flow.
        self.apply(disciplines='therapy,pilates', languages='english')

        self.assertEqual(
            Application.objects.get().disciplines, ['therapy', 'pilates'],
        )

    def test_the_rest_of_the_form_is_kept(self):
        self.apply()

        application = Application.objects.get()
        self.assertEqual(application.portfolio_url, 'https://dera.example')
        self.assertEqual(application.licence_number, 'NG-PSY-4421')
        self.assertEqual(application.insurance_provider, 'Leadway')
        self.assertEqual(application.experience_years, 6)

    def test_an_application_with_no_discipline_is_refused(self):
        response = self.apply(disciplines=[])

        self.assertEqual(response.status_code, 400)
        self.assertIn('disciplines', response.json())
        self.assertFalse(Application.objects.exists())

    def test_something_we_do_not_offer_is_refused(self):
        response = self.apply(disciplines=['astrology'])

        self.assertEqual(response.status_code, 400)

    def test_an_applicant_can_see_where_theirs_stands(self):
        self.apply()

        rows = self.client.get(self.url, HTTP_AUTHORIZATION=self.token).json()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'submitted')
        self.assertEqual(rows[0]['status_display'], 'Submitted')
        # The documents come back as links, not as files.
        self.assertEqual(len(rows[0]['documents']), 2)

    def test_nobody_sees_anybody_elses_application(self):
        self.apply()
        Profile.create_user(email='bola@example.com', password='Soothify2026')
        other = self.signed_in_as('bola@example.com', 'Soothify2026')

        rows = self.client.get(self.url, HTTP_AUTHORIZATION=other).json()

        self.assertEqual(rows, [])

    def test_applying_needs_an_account(self):
        response = self.client.post(self.url, {'full_name': 'Nobody'})

        self.assertEqual(response.status_code, 401)

    def test_approving_carries_every_discipline_onto_the_expert(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from experts.admin import ApplicationAdmin

        self.apply()
        application = Application.objects.get()
        # A real request: the action tells the reviewer how many it approved,
        # and messages need somewhere to go.
        request = RequestFactory().post('/admin/')
        request.user = application.user
        request.session = {}
        request._messages = FallbackStorage(request)

        ApplicationAdmin(Application, AdminSite()).approve(
            request, Application.objects.all(),
        )

        expert = ExpertProfile.objects.get()
        # Both, not just the first: that is the whole point of the change.
        self.assertEqual(expert.disciplines, ['therapy', 'yoga'])
        application.refresh_from_db()
        self.assertEqual(application.status, 'approved')


class PayoutMethodTests(ApiTestCase):
    """Adding the account a practitioner is paid into."""

    def setUp(self):
        super().setUp()
        self.expert = make_expert(email='coach2@example.com')
        self.token = self.signed_in_as('coach2@example.com', 'Soothify2026')
        self.url = reverse('api:experts:my-payout-methods')

    def add(self, **overrides):
        payload = {
            'kind': 'bank',
            'bank_name': 'GTBank',
            'account_number': '0123456789',
            'bank_code': '058',
        }
        payload.update(overrides)
        return self.client.post(
            self.url, payload, content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

    def test_the_account_is_resolved_and_turned_into_a_destination(self):
        with patch('billing.bachs.resolve_account',
                   return_value={'account_name': 'DERA EZE'}) as resolve, \
             patch('billing.bachs.create_payout_destination',
                   return_value={'destination_id': 'dest_1'}) as destination:
            response = self.add()

        self.assertEqual(response.status_code, 201)
        resolve.assert_called_once_with('0123456789', '058')
        self.assertEqual(destination.call_args.kwargs['account_number'], '0123456789')

        method = PayoutMethod.objects.get()
        self.assertEqual(method.destination_id, 'dest_1')
        # Their name on the account, not whatever was typed: a payout to a
        # mistyped name is somebody else's money.
        self.assertEqual(method.account_name, 'DERA EZE')

    def test_the_account_number_itself_is_never_stored(self):
        with patch('billing.bachs.resolve_account', return_value={'account_name': 'D E'}), \
             patch('billing.bachs.create_payout_destination',
                   return_value={'destination_id': 'dest_1'}):
            self.add()

        method = PayoutMethod.objects.get()
        self.assertEqual(method.last4, '6789')
        stored = ' '.join(
            str(value) for value in PayoutMethod.objects.values().first().values()
        )
        self.assertNotIn('0123456789', stored)

    def test_a_bank_account_needs_its_number_and_its_bank(self):
        response = self.client.post(
            self.url, {'kind': 'bank', 'bank_name': 'GTBank'},
            content_type='application/json', HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('account_number', response.json())

    def test_an_account_bachs_will_not_take_is_not_saved(self):
        from billing.bachs import BachsError

        with patch('billing.bachs.resolve_account',
                   side_effect=BachsError('No such account')):
            response = self.add()

        self.assertEqual(response.status_code, 502)
        self.assertIn('No such account', response.json()['detail'])
        self.assertFalse(PayoutMethod.objects.exists())

    def test_a_new_default_account_demotes_the_old_one(self):
        with patch('billing.bachs.resolve_account', return_value={'account_name': 'D E'}), \
             patch('billing.bachs.create_payout_destination',
                   return_value={'destination_id': 'dest_1'}):
            self.add()
            self.add(bank_name='Kuda', account_number='9876543210')

        defaults = PayoutMethod.objects.filter(is_default=True)
        self.assertEqual(defaults.count(), 1)
        self.assertEqual(defaults.get().bank_name, 'Kuda')

    def test_a_client_has_no_payout_methods_to_add(self):
        Profile.create_user(email='ada2@example.com', password='Soothify2026')
        client_token = self.signed_in_as('ada2@example.com', 'Soothify2026')

        response = self.client.get(self.url, HTTP_AUTHORIZATION=client_token)

        self.assertEqual(response.status_code, 403)

    def test_the_bank_list_comes_from_bachs_and_is_cached(self):
        from django.core.cache import cache

        cache.delete('bachs:banks:NG')
        url = reverse('api:experts:my-banks')

        with patch('billing.bachs.list_banks',
                   return_value=[{'code': '058', 'name': 'GTBank'}]) as listed:
            first = self.client.get(url, HTTP_AUTHORIZATION=self.token).json()
            second = self.client.get(url, HTTP_AUTHORIZATION=self.token).json()

        self.assertEqual(first, [{'code': '058', 'name': 'GTBank'}])
        self.assertEqual(second, first)
        # The same answer for everyone all day; asking twice is a wasted call.
        self.assertEqual(listed.call_count, 1)


class WithdrawalTests(ApiTestCase):
    """Asking to be paid, and what it takes for money to actually move."""

    def setUp(self):
        super().setUp()
        self.expert = make_expert(email='coach3@example.com')
        self.token = self.signed_in_as('coach3@example.com', 'Soothify2026')
        self.url = reverse('api:experts:my-payouts')
        self.method = PayoutMethod.objects.create(
            expert=self.expert, bank_name='GTBank', account_name='C O',
            last4='6789', destination_id='dest_1', is_default=True,
        )
        Profile.create_user(
            email='ada3@example.com', password='Soothify2026', full_name='Ada',
        )
        self.client_user = get_user_model().objects.get(email='ada3@example.com')

    def earn(self, naira=50000):
        """A completed session is where an available balance comes from."""
        Session.objects.create(
            expert=self.expert, client=self.client_user, discipline='therapy',
            starts_at=timezone.now() - datetime.timedelta(days=2),
            ends_at=timezone.now() - datetime.timedelta(days=2, hours=-1),
            status='completed', price_naira=naira,
        )

    def ask_for(self, naira):
        return self.client.post(
            self.url, {'amount_naira': naira},
            content_type='application/json', HTTP_AUTHORIZATION=self.token,
        )

    def test_a_withdrawal_is_recorded_pending_not_paid(self):
        self.earn()

        response = self.ask_for(20000)

        self.assertEqual(response.status_code, 201)
        payout = Payout.objects.get()
        # Recorded, not sent: a person sends it.
        self.assertEqual(payout.status, 'pending')
        self.assertEqual(payout.amount_naira, 20000)
        self.assertEqual(payout.method, self.method)

    def test_nobody_can_withdraw_more_than_they_have_earned(self):
        self.earn(50000)

        response = self.ask_for(60000)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['available_naira'], 50000)
        self.assertFalse(Payout.objects.exists())

    def test_a_pending_request_counts_against_the_balance(self):
        self.earn(50000)
        self.ask_for(40000)

        # Asking twice for most of it is how a balance gets paid out twice.
        response = self.ask_for(40000)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Payout.objects.count(), 1)

    def test_a_withdrawal_needs_somewhere_to_land(self):
        self.earn()
        PayoutMethod.objects.all().delete()

        response = self.ask_for(10000)

        self.assertEqual(response.status_code, 400)
        self.assertIn('account', response.json()['detail'])

    def test_the_destination_is_not_the_requester_to_choose(self):
        self.earn()

        # A destination in the request body is ignored: it is the account on
        # file, verified with the bank when it was added.
        self.client.post(
            self.url,
            {'amount_naira': 10000, 'method': 999, 'destination_id': 'dest_evil'},
            content_type='application/json', HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(Payout.objects.get().method, self.method)

    def test_a_client_cannot_ask_to_be_paid(self):
        other = self.signed_in_as('ada3@example.com', 'Soothify2026')

        response = self.client.post(
            self.url, {'amount_naira': 1000},
            content_type='application/json', HTTP_AUTHORIZATION=other,
        )

        self.assertEqual(response.status_code, 403)

    def test_sending_one_pays_it_through_bachs_once(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from experts.admin import PayoutAdmin

        self.earn()
        self.ask_for(20000)
        request = RequestFactory().post('/admin/')
        request.user = self.client_user
        request.session = {}
        request._messages = FallbackStorage(request)
        admin = PayoutAdmin(Payout, AdminSite())

        with patch('billing.bachs.create_payout',
                   return_value={'payout_id': 'po_1'}) as pay:
            admin.send_payout(request, Payout.objects.all())
            # A second click must not pay a second time.
            admin.send_payout(request, Payout.objects.all())

        self.assertEqual(pay.call_count, 1)
        payout = Payout.objects.get()
        self.assertEqual(payout.status, 'sent')
        self.assertEqual(payout.reference, 'po_1')
        self.assertIsNotNone(payout.paid_on)

    def test_a_payout_bachs_refuses_stays_pending(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from billing.bachs import BachsError
        from experts.admin import PayoutAdmin

        self.earn()
        self.ask_for(20000)
        request = RequestFactory().post('/admin/')
        request.user = self.client_user
        request.session = {}
        request._messages = FallbackStorage(request)

        with patch('billing.bachs.create_payout', side_effect=BachsError('no')):
            PayoutAdmin(Payout, AdminSite()).send_payout(
                request, Payout.objects.all(),
            )

        # Still owed, and still visible as owed.
        self.assertEqual(Payout.objects.get().status, 'pending')
