"""Memberships, the trial, session payments and the webhook.

Paystack is never actually called: every test patches the one module that
talks to it. What is tested is our side -- that only a signed webhook grants
anything, that a retried webhook does not pay twice, and that a trial becomes
a charge on day 8.
"""

import datetime
import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, Role
from api.testing import ApiTestCase
from billing.models import Membership, Payment, Plan, PromoCode
from experts.models import ExpertProfile, Session

SECRET = 'sk_test_fake_key_for_tests'


def make_plans():
    core = Plan.objects.create(
        key='core', name='Soothify Core',
        description='Your sanctuary for daily balance.',
        price_naira=150000, price_suffix='/year', cadence='annual',
        action='trial', position=0,
    )
    Plan.objects.create(
        key='core-monthly', name='Soothify Core', description='Monthly.',
        price_naira=15000, cadence='monthly', action='trial', position=1,
    )
    Plan.objects.create(
        key='passport', name='Soothify Passport',
        description='Your city-wide pass to physical movement.',
        action='waitlist', is_recommended=True, position=2,
    )
    return core


def signed(payload):
    body = json.dumps(payload).encode('utf-8')
    signature = hmac.new(SECRET.encode('utf-8'), body, hashlib.sha512).hexdigest()
    return body, signature


def charge_success(reference, last4='1234', brand='mastercard'):
    return {
        'event': 'charge.success',
        'data': {
            'reference': reference,
            'customer': {'customer_code': 'CUS_test'},
            'authorization': {
                'authorization_code': 'AUTH_test',
                'brand': brand,
                'last4': last4,
            },
        },
    }


@override_settings(PAYSTACK_SECRET_KEY=SECRET)
class BillingTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.plan = make_plans()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.user = self.profile.user
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')

    def post_webhook(self, payload):
        body, signature = signed(payload)
        return self.client.post(
            reverse('api:billing:webhook'), body,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

    def checkout(self, **overrides):
        payload = {'plan': 'core', 'cadence': 'annual', 'trial': True}
        payload.update(overrides)
        with patch('billing.paystack.initialize') as init:
            init.return_value = {
                'authorization_url': 'https://checkout.paystack.com/abc',
                'access_code': 'abc',
                'reference': 'ignored',
            }
            return self.client.post(
                reverse('api:billing:checkout'), payload,
                content_type='application/json', HTTP_AUTHORIZATION=self.token,
            )


class PlanTests(BillingTestCase):
    def test_the_cards_carry_what_the_app_parses(self):
        response = self.client.get(reverse('api:billing:plans'))

        row = response.json()[0]
        for field in ('id', 'name', 'description', 'price_display', 'is_recommended'):
            self.assertIn(field, row)
        self.assertEqual(row['price_display'], '₦150,000')

    def test_a_guest_can_read_the_plans(self):
        self.assertEqual(self.client.get(reverse('api:billing:plans')).status_code, 200)

    def test_a_priceless_card_still_shows_with_its_button(self):
        rows = {row['id']: row for row in self.client.get(reverse('api:billing:plans')).json()}

        self.assertEqual(rows['passport']['price_display'], '')
        self.assertEqual(rows['passport']['action'], 'waitlist')

    def test_the_period_toggle_narrows_to_that_cadence(self):
        response = self.client.get('{0}?period=monthly'.format(reverse('api:billing:plans')))

        keys = {row['id'] for row in response.json()}
        self.assertIn('core-monthly', keys)
        self.assertNotIn('core', keys)
        # A card with no cadence belongs under both toggles.
        self.assertIn('passport', keys)


class TrialTests(BillingTestCase):
    def test_a_new_account_has_no_membership(self):
        response = self.client.get(
            reverse('api:billing:membership'), HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.json()['state'], 'inactive')
        self.assertFalse(response.json()['is_active'])

    def test_starting_a_trial_charges_only_the_card_check(self):
        response = self.checkout()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['amount_naira'], 50)
        self.assertEqual(Payment.objects.get().kind, 'card_check')

    def test_checkout_alone_does_not_grant_the_trial(self):
        """Access waits on the webhook. A client cannot talk itself in."""
        self.checkout()

        membership = Membership.objects.get()
        self.assertEqual(membership.state, 'inactive')
        self.assertFalse(membership.is_active)

    def test_the_webhook_starts_the_seven_days_and_saves_the_card(self):
        self.checkout()
        reference = Payment.objects.get().reference

        self.post_webhook(charge_success(reference))

        membership = Membership.objects.get()
        self.assertEqual(membership.state, 'trial')
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.card_label, 'Mastercard ending in 1234')
        days = (membership.trial_ends_at - timezone.now()).days
        self.assertEqual(days, 6)          # 7 days, minus the hours elapsed

    def test_a_trial_cannot_be_started_twice(self):
        self.checkout()
        self.post_webhook(charge_success(Payment.objects.get().reference))

        response = self.checkout()

        self.assertEqual(response.status_code, 400)

    def test_paying_outright_skips_the_trial_and_charges_the_plan(self):
        response = self.checkout(trial=False)

        self.assertEqual(response.json()['amount_naira'], 150000)
        self.assertEqual(Payment.objects.get().kind, 'membership')

    def test_a_full_payment_makes_the_membership_active(self):
        self.checkout(trial=False)

        self.post_webhook(charge_success(Payment.objects.get().reference))

        membership = Membership.objects.get()
        self.assertEqual(membership.state, 'active')
        self.assertGreater(membership.renews_at, timezone.now())


class WebhookTests(BillingTestCase):
    def test_an_unsigned_webhook_is_refused(self):
        self.checkout()

        response = self.client.post(
            reverse('api:billing:webhook'),
            json.dumps(charge_success(Payment.objects.get().reference)),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Payment.objects.get().status, 'pending')

    def test_a_wrongly_signed_webhook_is_refused(self):
        self.checkout()

        response = self.client.post(
            reverse('api:billing:webhook'),
            json.dumps(charge_success(Payment.objects.get().reference)),
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE='not-the-signature',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Membership.objects.get().state, 'inactive')

    def test_a_retried_webhook_does_not_pay_twice(self):
        self.checkout(trial=False)
        reference = Payment.objects.get().reference
        self.post_webhook(charge_success(reference))
        first_renewal = Membership.objects.get().renews_at

        self.post_webhook(charge_success(reference))

        self.assertEqual(Membership.objects.get().renews_at, first_renewal)

    def test_an_unknown_reference_is_ignored_quietly(self):
        response = self.post_webhook(charge_success('sfy_nothing_here'))

        self.assertEqual(response.status_code, 200)

    def test_other_events_are_accepted_so_paystack_stops_retrying(self):
        response = self.post_webhook({'event': 'customer.create', 'data': {}})

        self.assertEqual(response.status_code, 200)


class RenewalTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.checkout()
        self.post_webhook(charge_success(Payment.objects.get().reference))
        self.membership = Membership.objects.get()

    def test_a_trial_that_has_run_out_is_charged(self):
        from billing.tasks import charge_due_subscriptions

        Membership.objects.update(renews_at=timezone.now() - datetime.timedelta(minutes=1))

        with patch('billing.paystack.charge_authorization') as charge:
            charge.return_value = {'status': 'success'}
            charged = charge_due_subscriptions()

        self.assertEqual(charged, 1)
        self.assertEqual(charge.call_args.kwargs['naira'], 150000)
        self.assertEqual(charge.call_args.kwargs['authorization_code'], 'AUTH_test')

    def test_a_trial_still_running_is_left_alone(self):
        from billing.tasks import charge_due_subscriptions

        with patch('billing.paystack.charge_authorization') as charge:
            charged = charge_due_subscriptions()

        self.assertEqual(charged, 0)
        charge.assert_not_called()

    def test_a_failed_card_marks_the_membership_past_due_rather_than_gone(self):
        from billing.paystack import PaystackError
        from billing.tasks import charge_due_subscriptions

        Membership.objects.update(renews_at=timezone.now() - datetime.timedelta(minutes=1))

        with patch('billing.paystack.charge_authorization', side_effect=PaystackError('declined')):
            charge_due_subscriptions()

        membership = Membership.objects.get()
        self.assertEqual(membership.state, 'past_due')
        self.assertEqual(Payment.objects.filter(status='failed').count(), 1)

    def test_cancelling_keeps_access_until_the_period_ends(self):
        response = self.client.post(
            reverse('api:billing:membership-cancel'), HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.json()['state'], 'cancelled')
        self.assertTrue(Membership.objects.get().is_active)

    def test_a_cancelled_membership_is_not_charged_again(self):
        from billing.tasks import charge_due_subscriptions

        self.membership.cancel()
        Membership.objects.update(renews_at=timezone.now() - datetime.timedelta(minutes=1))

        with patch('billing.paystack.charge_authorization') as charge:
            charge_due_subscriptions()

        charge.assert_not_called()

    def test_billing_history_lists_the_charges_and_not_the_card_check(self):
        response = self.client.get(
            reverse('api:billing:invoices'), HTTP_AUTHORIZATION=self.token,
        )

        kinds = {row['description'] for row in response.json()['results']}
        self.assertNotIn('Card verification for the 7-day trial', kinds)


class SessionPaymentTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        coach = Profile.create_user(
            email='coach@example.com', password='Soothify2026',
            full_name='Baraqhat', role=Role.PRACTITIONER,
        )
        expert = ExpertProfile.objects.create(profile=coach, disciplines=['therapy'])
        self.session = Session.objects.create(
            client=self.user, expert=expert, discipline='therapy',
            starts_at=timezone.now() + datetime.timedelta(days=2),
            ends_at=timezone.now() + datetime.timedelta(days=2, minutes=50),
            status='pending', price_naira=25000,
        )
        self.url = reverse('api:billing:session-checkout', args=[self.session.pk])

    def pay(self, **data):
        with patch('billing.paystack.initialize') as init:
            init.return_value = {
                'authorization_url': 'https://checkout.paystack.com/x',
                'access_code': 'x', 'reference': 'ignored',
            }
            return self.client.post(
                self.url, data, content_type='application/json',
                HTTP_AUTHORIZATION=self.token,
            )

    def test_checkout_quotes_the_session_price(self):
        response = self.pay()

        self.assertEqual(response.json()['amount_naira'], 25000)

    def test_the_session_is_only_confirmed_by_the_webhook(self):
        self.pay()

        self.assertEqual(Session.objects.get().status, 'pending')

        self.post_webhook(charge_success(Payment.objects.get(kind='session').reference))

        self.assertEqual(Session.objects.get().status, 'confirmed')

    def test_nobody_can_pay_for_someone_elses_session(self):
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.client.post(
            self.url, {}, content_type='application/json',
            HTTP_AUTHORIZATION=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.status_code, 404)

    def test_a_promo_code_comes_off_the_price(self):
        PromoCode.objects.create(code='CALM20', percent_off=20)

        response = self.pay(promo_code='CALM20')

        self.assertEqual(response.json()['amount_naira'], 20000)

    def test_an_expired_code_is_simply_not_applied(self):
        PromoCode.objects.create(
            code='OLD', percent_off=50,
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )

        response = self.pay(promo_code='OLD')

        self.assertEqual(response.json()['amount_naira'], 25000)

    def test_a_code_cannot_make_a_bill_larger(self):
        promo = PromoCode.objects.create(code='ODD', amount_off_naira=99999)

        self.assertEqual(promo.apply_to(25000), 0)


class PaidContentTests(BillingTestCase):
    """The paywall from the content side: a member may play, a visitor may not."""

    def setUp(self):
        super().setUp()
        from content.models import MediaItem

        self.item = MediaItem.objects.create(
            title='Deep rest', is_free=False, external_url='https://cdn/x.mp3',
        )
        self.url = reverse('api:content:item', args=[self.item.pk])

    def test_without_a_membership_there_is_no_playable_link(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.json()['media_url'], '')
        self.assertTrue(response.json()['requires_subscription'])

    def test_a_member_on_trial_can_play_it(self):
        self.checkout()
        self.post_webhook(charge_success(Payment.objects.get().reference))

        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.json()['media_url'], 'https://cdn/x.mp3')
        self.assertFalse(response.json()['requires_subscription'])
