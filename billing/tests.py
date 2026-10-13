"""Memberships, trials, session payments and the Bachs webhook.

Bachs is never actually called: every test patches the one module that talks
to it. What is tested is our side -- that only a signed delivery grants
anything, that a replayed one does not, and that a trial starting, ending and
renewing leaves the membership in the right state.
"""

import datetime
import hashlib
import hmac
import json
import time
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, Role
from api.testing import ApiTestCase
from billing.models import Membership, Payment, Plan, PromoCode
from experts.models import ExpertProfile, Session

SECRET = 'sk_sandbox_fake_key_for_tests'
WEBHOOK_SECRET = 'whsec_fake_signing_secret'

CHECKOUT = {
    'checkout_id': 'chk_123',
    'checkout_url': 'https://checkout.bachs.io/c/abc',
    'status': 'open',
}


def make_plans():
    core = Plan.objects.create(
        key='core', name='Soothify Core',
        description='Your sanctuary for daily balance.',
        price_naira=150000, price_suffix='/year', cadence='annual',
        action='trial', position=0, bachs_product_id='prod_core_annual',
        trial_days=7,
    )
    Plan.objects.create(
        key='core-monthly', name='Soothify Core', description='Monthly.',
        price_naira=15000, cadence='monthly', action='trial', position=1,
        bachs_product_id='prod_core_monthly', trial_days=7,
    )
    Plan.objects.create(
        key='passport', name='Soothify Passport',
        description='Your city-wide pass to physical movement.',
        action='waitlist', is_recommended=True, position=2,
    )
    return core


@override_settings(BACHS_SECRET_KEY=SECRET, BACHS_WEBHOOK_SECRET=WEBHOOK_SECRET)
class BillingTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.plan = make_plans()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.user = self.profile.user
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')

    def post_webhook(self, event, data, at=None, secret=None):
        """A delivery signed the way Bachs signs one."""
        payload = json.dumps({'event': event, 'data': data}).encode('utf-8')
        sent_at = int(at or time.time())
        signature = hmac.new(
            (secret or WEBHOOK_SECRET).encode('utf-8'),
            '{0}.'.format(sent_at).encode('utf-8') + payload,
            hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            reverse('api:billing:webhook'), payload,
            content_type='application/json',
            HTTP_X_BACHS_SIGNATURE_V2='t={0},v1={1}'.format(sent_at, signature),
        )

    def checkout(self, **overrides):
        payload = {'plan': 'core', 'cadence': 'annual', 'trial': True}
        payload.update(overrides)
        with patch('billing.bachs.checkout_for_product', return_value=CHECKOUT):
            return self.client.post(
                reverse('api:billing:checkout'), payload,
                content_type='application/json', HTTP_AUTHORIZATION=self.token,
            )

    def completed(self, status='trialing', trial_days=7, **extra):
        """The checkout.completed payload Bachs sends."""
        subscription = {
            'subscription_id': 'sub_123',
            'status': status,
        }
        if status == 'trialing':
            subscription['trial_end'] = (
                timezone.now() + datetime.timedelta(days=trial_days)
            ).isoformat()
        else:
            subscription['current_period_end'] = (
                timezone.now() + datetime.timedelta(days=365)
            ).isoformat()
        data = {
            'reference': Payment.objects.latest('created_at').reference,
            'customer': {'customer_id': 'cus_123'},
            'subscription': subscription,
            'payment_method': {'brand': 'mastercard', 'last4': '1234'},
        }
        data.update(extra)
        return data


class PlanTests(BillingTestCase):
    def test_the_cards_carry_what_the_app_parses(self):
        row = self.client.get(reverse('api:billing:plans')).json()[0]

        for field in ('id', 'name', 'description', 'price_display', 'is_recommended'):
            self.assertIn(field, row)
        self.assertEqual(row['price_display'], '₦150,000')

    def test_a_priceless_card_still_shows_with_its_button(self):
        rows = {row['id']: row for row in self.client.get(reverse('api:billing:plans')).json()}

        self.assertEqual(rows['passport']['price_display'], '')
        self.assertEqual(rows['passport']['action'], 'waitlist')

    def test_the_period_toggle_narrows_to_that_cadence(self):
        keys = {
            row['id'] for row in
            self.client.get('{0}?period=monthly'.format(reverse('api:billing:plans'))).json()
        }

        self.assertIn('core-monthly', keys)
        self.assertNotIn('core', keys)
        self.assertIn('passport', keys)      # no cadence: shows under both


class CheckoutTests(BillingTestCase):
    def test_checkout_returns_a_url_and_takes_nothing_today(self):
        response = self.checkout()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['checkout_url'], CHECKOUT['checkout_url'])
        self.assertEqual(body['due_today_naira'], 0)
        self.assertEqual(body['trial_days'], 7)

    def test_the_card_is_saved_rather_than_charged_for_a_trial(self):
        """No verification charge: Bachs saves the method at checkout."""
        self.checkout()

        payment = Payment.objects.get()
        self.assertEqual(payment.kind, 'membership')
        self.assertEqual(payment.amount_naira, 150000)
        self.assertEqual(payment.status, 'pending')

    def test_checkout_alone_does_not_grant_anything(self):
        self.checkout()

        membership = Membership.objects.get()
        self.assertEqual(membership.state, 'inactive')
        self.assertFalse(membership.is_active)

    def test_a_plan_with_no_product_at_bachs_says_so(self):
        Plan.objects.filter(key='core').update(bachs_product_id='')

        response = self.checkout()

        self.assertEqual(response.status_code, 503)

    def test_a_plan_that_sells_nothing_cannot_be_bought(self):
        response = self.checkout(plan='passport')

        self.assertEqual(response.status_code, 400)

    def test_a_refusal_from_bachs_is_passed_on_rather_than_swallowed(self):
        from billing.bachs import BachsError

        with patch('billing.bachs.checkout_for_product', side_effect=BachsError('no')):
            response = self.client.post(
                reverse('api:billing:checkout'), {'plan': 'core'},
                content_type='application/json', HTTP_AUTHORIZATION=self.token,
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(Payment.objects.get().status, 'failed')


class TrialTests(BillingTestCase):
    def test_completing_checkout_starts_the_trial_with_their_date(self):
        self.checkout()

        self.post_webhook('checkout.completed', self.completed())

        membership = Membership.objects.get()
        self.assertEqual(membership.state, 'trial')
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.subscription_id, 'sub_123')
        self.assertEqual(membership.customer_id, 'cus_123')
        self.assertEqual((membership.trial_ends_at - timezone.now()).days, 6)

    def test_a_trial_leaves_no_invoice_because_nothing_was_charged(self):
        self.checkout()
        self.post_webhook('checkout.completed', self.completed())

        response = self.client.get(
            reverse('api:billing:invoices'), HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.json()['results'], [])

    def test_the_first_charge_at_the_end_of_the_trial_makes_it_active(self):
        self.checkout()
        self.post_webhook('checkout.completed', self.completed())

        self.post_webhook('invoice.paid', {
            'invoice_id': 'inv_1',
            'subscription_id': 'sub_123',
            'amount_paid': '150000.00',
            'period_end': (timezone.now() + datetime.timedelta(days=365)).isoformat(),
            'payment_method': {'brand': 'mastercard', 'last4': '1234'},
        })

        membership = Membership.objects.get()
        self.assertEqual(membership.state, 'active')
        self.assertIsNone(membership.trial_ends_at)
        self.assertEqual((membership.renews_at - timezone.now()).days, 364)

    def test_that_charge_appears_on_billing_history(self):
        self.checkout()
        self.post_webhook('checkout.completed', self.completed())
        self.post_webhook('invoice.paid', {
            'invoice_id': 'inv_1', 'subscription_id': 'sub_123',
            'amount_paid': '150000.00',
            'payment_method': {'brand': 'mastercard', 'last4': '1234'},
        })

        rows = self.client.get(
            reverse('api:billing:invoices'), HTTP_AUTHORIZATION=self.token,
        ).json()['results']

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['amount_naira'], 150000)
        self.assertEqual(rows[0]['status_label'], 'Paid (Mastercard 1234)')

    def test_a_repeated_invoice_does_not_grant_a_second_period(self):
        self.checkout()
        self.post_webhook('checkout.completed', self.completed())
        invoice = {
            'invoice_id': 'inv_1', 'subscription_id': 'sub_123',
            'amount_paid': '150000.00',
        }
        self.post_webhook('invoice.paid', invoice)
        first = Membership.objects.get().renews_at

        self.post_webhook('invoice.paid', invoice)

        self.assertEqual(Membership.objects.get().renews_at, first)
        self.assertEqual(Payment.objects.filter(status='paid').count(), 1)

    def test_a_failed_charge_marks_it_past_due_rather_than_gone(self):
        self.checkout()
        self.post_webhook('checkout.completed', self.completed())

        self.post_webhook('invoice.payment_failed', {
            'invoice_id': 'inv_2', 'subscription_id': 'sub_123',
        })

        self.assertEqual(Membership.objects.get().state, 'past_due')

    def test_paying_outright_skips_the_trial(self):
        self.checkout()

        self.post_webhook('checkout.completed', self.completed(status='active'))

        membership = Membership.objects.get()
        self.assertEqual(membership.state, 'active')
        self.assertIsNone(membership.trial_ends_at)

    def test_a_trial_cannot_be_started_twice(self):
        self.checkout()
        self.post_webhook('checkout.completed', self.completed())

        self.assertEqual(self.checkout().status_code, 400)


class WebhookSecurityTests(BillingTestCase):
    def test_an_unsigned_delivery_is_refused(self):
        self.checkout()

        response = self.client.post(
            reverse('api:billing:webhook'),
            json.dumps({'event': 'checkout.completed', 'data': self.completed()}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Membership.objects.get().state, 'inactive')

    def test_a_delivery_signed_with_the_wrong_secret_is_refused(self):
        self.checkout()

        response = self.post_webhook(
            'checkout.completed', self.completed(), secret='whsec_not_ours',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Membership.objects.get().state, 'inactive')

    def test_an_old_delivery_cannot_be_replayed(self):
        """Captured today, sent back tomorrow: outside the 300-second window."""
        self.checkout()

        response = self.post_webhook(
            'checkout.completed', self.completed(), at=time.time() - 3600,
        )

        self.assertEqual(response.status_code, 401)

    def test_a_second_signature_is_accepted_while_a_secret_rotates(self):
        from billing import bachs

        body = b'{"event":"ping"}'
        sent_at = int(time.time())
        good = hmac.new(
            WEBHOOK_SECRET.encode(), '{0}.'.format(sent_at).encode() + body,
            hashlib.sha256,
        ).hexdigest()
        header = 't={0},v1={1},v1={2}'.format(sent_at, 'a' * 64, good)

        self.assertTrue(bachs.signature_is_valid(body, header, WEBHOOK_SECRET))

    def test_events_we_do_not_handle_are_accepted_so_they_stop_retrying(self):
        response = self.post_webhook('dispute.created', {'dispute_id': 'dp_1'})

        self.assertEqual(response.status_code, 200)

    def test_an_unknown_reference_is_ignored_quietly(self):
        response = self.post_webhook(
            'collection.succeeded', {'reference': 'sfy_nothing_here'},
        )

        self.assertEqual(response.status_code, 200)


class CancellationTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.checkout()
        self.post_webhook('checkout.completed', self.completed())

    def test_cancelling_tells_bachs_and_keeps_access_to_the_period_end(self):
        with patch('billing.bachs.cancel_subscription') as cancel:
            response = self.client.post(
                reverse('api:billing:membership-cancel'), HTTP_AUTHORIZATION=self.token,
            )

        cancel.assert_called_once_with('sub_123')
        self.assertEqual(response.json()['state'], 'cancelled')
        self.assertTrue(Membership.objects.get().is_active)

    def test_if_bachs_cannot_be_told_nothing_is_cancelled_here(self):
        """A cancellation they do not know about is a charge that still lands."""
        from billing.bachs import BachsError

        with patch('billing.bachs.cancel_subscription', side_effect=BachsError('down')):
            response = self.client.post(
                reverse('api:billing:membership-cancel'), HTTP_AUTHORIZATION=self.token,
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(Membership.objects.get().state, 'trial')

    def test_their_deletion_event_cancels_it_here_too(self):
        self.post_webhook('customer.subscription.deleted', {'subscription_id': 'sub_123'})

        self.assertEqual(Membership.objects.get().state, 'cancelled')


class ReconcileTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.checkout()
        self.post_webhook('checkout.completed', self.completed())

    def test_a_missed_invoice_webhook_is_caught_up(self):
        from billing.tasks import reconcile_subscriptions

        Membership.objects.update(
            renews_at=timezone.now() - datetime.timedelta(days=1),
        )
        period_end = timezone.now() + datetime.timedelta(days=364)

        with patch('billing.bachs.get_subscription') as read:
            read.return_value = {
                'status': 'active',
                'current_period_end': period_end.isoformat(),
            }
            fixed = reconcile_subscriptions()

        self.assertEqual(fixed, 1)
        membership = Membership.objects.get()
        self.assertEqual(membership.state, 'active')
        self.assertIsNone(membership.trial_ends_at)

    def test_a_membership_that_is_not_overdue_is_left_alone(self):
        from billing.tasks import reconcile_subscriptions

        with patch('billing.bachs.get_subscription') as read:
            reconcile_subscriptions()

        read.assert_not_called()


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
        with patch('billing.bachs.checkout_for_amount', return_value=CHECKOUT) as init:
            response = self.client.post(
                self.url, data, content_type='application/json',
                HTTP_AUTHORIZATION=self.token,
            )
            self.init = init
            return response

    def test_a_session_is_charged_as_an_amount_not_a_product(self):
        response = self.pay()

        self.assertEqual(response.json()['amount_naira'], 25000)
        self.assertEqual(self.init.call_args.kwargs['naira'], 25000)

    def test_the_session_is_only_confirmed_by_the_webhook(self):
        self.pay()

        self.assertEqual(Session.objects.get().status, 'pending')

        self.post_webhook('collection.succeeded', {
            'reference': Payment.objects.get(kind='session').reference,
            'payment_method': {'brand': 'visa', 'last4': '4242'},
        })

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


class MoneyFormatTests(BillingTestCase):
    """Their amounts are decimal strings, not minor units."""

    def test_naira_go_out_as_a_decimal_string(self):
        from billing import bachs

        self.assertEqual(bachs.money(25000), '25000.00')
        self.assertEqual(bachs.money(150000), '150000.00')

    def test_amounts_come_back_as_whole_naira(self):
        from billing import bachs

        self.assertEqual(bachs.from_money('150000.00'), 150000)
        self.assertEqual(bachs.from_money('0.00'), 0)
        self.assertEqual(bachs.from_money(None), 0)

    def test_the_key_chooses_the_host_so_sandbox_cannot_hit_production(self):
        from billing import bachs

        self.assertEqual(bachs.base_url(), bachs.SANDBOX_URL)

        with override_settings(BACHS_SECRET_KEY='sk_live_whatever'):
            self.assertEqual(bachs.base_url(), bachs.LIVE_URL)


class PaidContentTests(BillingTestCase):
    """The paywall from the content side."""

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
        self.post_webhook('checkout.completed', self.completed())

        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.json()['media_url'], 'https://cdn/x.mp3')
