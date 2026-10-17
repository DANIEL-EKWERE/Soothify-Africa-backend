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

from django.test import override_settings, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from django.contrib.auth import get_user_model

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


# These cover the arrangement where Bachs runs the cycle. It is not what
# the site is set to while NGN subscriptions are refused, so it is pinned
# here rather than inherited -- the one-period-at-a-time path has its own
# case below.
@override_settings(
    BACHS_SECRET_KEY=SECRET,
    BACHS_WEBHOOK_SECRET=WEBHOOK_SECRET,
    MEMBERSHIP_BILLING='subscription',
)
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


class RefusalWordingTests(SimpleTestCase):
    """What a refusal from Bachs is turned into before the app sees it."""

    def test_the_reason_is_read_from_whichever_key_carries_it(self):
        from billing.bachs import _message

        # Their checkout endpoint answers with `detail`; reading only
        # `message` and `error` lost the reason and left every refusal
        # reading "Bachs refused the request."
        self.assertEqual(
            _message('{"detail": "That product is archived"}'),
            'That product is archived',
        )
        self.assertEqual(
            _message('{"message": "Bad key"}'), 'Bad key',
        )
        self.assertEqual(
            _message('{"error": {"message": "Nested"}}'), 'Nested',
        )

    def test_a_known_limitation_is_said_in_our_own_words(self):
        from billing.bachs import _message

        answer = _message(
            '{"detail": "NGN subscriptions are not available right now",'
            ' "error_code": "NGN_SUBSCRIPTIONS_NOT_ENABLED"}'
        )

        # The person holding the phone is not integrating an API, and can
        # still book a session -- which is the part worth telling them.
        self.assertIn('naira', answer)
        self.assertIn('Single sessions', answer)

    def test_a_body_that_is_not_json_says_nothing_rather_than_guessing(self):
        from billing.bachs import _message

        self.assertEqual(_message('<html>502 Bad Gateway</html>'), '')


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


@override_settings(
    BACHS_SECRET_KEY=SECRET,
    BACHS_WEBHOOK_SECRET=WEBHOOK_SECRET,
    MEMBERSHIP_BILLING='one_off',
    # The card check has its own case below; these are about what a period
    # costs and what a payment buys.
    TRIAL_REQUIRES_CARD=False,
)
class OnePeriodAtATimeTests(BillingTestCase):
    """Memberships sold one period at a time, which is how they are sold now.

    Bachs refuse recurring charges in naira on this account, so nothing
    renews itself: the free week is given outright, a period is bought with
    the same plain charge a session uses, and a job reminds the member
    before it runs out.
    """

    def buy(self, **overrides):
        """Ask to start a membership, with Bachs' one-off checkout stubbed."""
        payload = {'plan': 'core', 'cadence': 'annual', 'trial': True}
        payload.update(overrides)
        with patch('billing.bachs.checkout_for_amount', return_value=CHECKOUT) as call:
            response = self.client.post(
                reverse('api:billing:checkout'), payload,
                content_type='application/json', HTTP_AUTHORIZATION=self.token,
            )
        self.bachs_call = call
        return response

    def membership(self):
        return Membership.objects.get(user=self.user)

    # ----------------------------------------------------------- the trial

    def test_the_free_week_is_given_outright_with_nothing_to_pay(self):
        with patch('billing.bachs.checkout_for_amount') as never:
            response = self.client.post(
                reverse('api:billing:checkout'),
                {'plan': 'core', 'cadence': 'annual', 'trial': True},
                content_type='application/json', HTTP_AUTHORIZATION=self.token,
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        # No card to save, so no checkout to open and nothing to wait for.
        never.assert_not_called()
        self.assertTrue(body['trial_started'])
        self.assertEqual(body['checkout_url'], '')
        self.assertEqual(body['due_today_naira'], 0)

        membership = self.membership()
        self.assertEqual(membership.state, 'trial')
        self.assertTrue(membership.is_active)
        self.assertFalse(membership.auto_renew)
        self.assertIsNotNone(membership.trial_started_at)

    def test_the_free_week_leaves_no_invoice_because_nothing_was_charged(self):
        self.buy()

        self.assertFalse(Payment.objects.exists())

    def test_a_second_free_week_sells_a_period_instead(self):
        self.buy()
        membership = self.membership()
        membership.lapse()

        response = self.buy()

        body = response.json()
        self.assertFalse(body['trial_started'])
        self.assertEqual(body['checkout_url'], CHECKOUT['checkout_url'])
        # The whole period is due now; there is no free week left to give.
        self.assertEqual(body['due_today_naira'], 150000)
        self.assertFalse(self.membership().is_active)

    def test_the_offer_says_whether_a_free_week_is_still_going(self):
        before = self.client.get(
            reverse('api:billing:membership'), HTTP_AUTHORIZATION=self.token,
        ).json()
        self.assertTrue(before['trial_available'])

        self.buy()

        after = self.client.get(
            reverse('api:billing:membership'), HTTP_AUTHORIZATION=self.token,
        ).json()
        self.assertFalse(after['trial_available'])
        self.assertFalse(after['auto_renew'])

    # ---------------------------------------------------------- buying one

    def test_buying_a_period_charges_the_plan_price(self):
        response = self.buy(trial=False)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.bachs_call.call_args.kwargs['naira'], 150000)
        self.assertEqual(response.json()['amount_naira'], 150000)
        # A plain charge, not a product: their recurring products refuse naira.
        self.assertEqual(Payment.objects.get().kind, 'membership')

    def test_a_promo_code_comes_off_the_price(self):
        PromoCode.objects.create(code='CALM20', percent_off=20, is_active=True)

        response = self.buy(trial=False, promo_code='CALM20')

        self.assertEqual(response.json()['amount_naira'], 120000)
        self.assertEqual(self.bachs_call.call_args.kwargs['naira'], 120000)

    def test_a_checkout_alone_grants_nothing(self):
        self.buy(trial=False)

        self.assertEqual(self.membership().state, 'inactive')
        self.assertEqual(Payment.objects.get().status, 'pending')

    def test_the_webhook_grants_exactly_one_period(self):
        self.buy(trial=False)

        self.post_webhook('collection.succeeded', {
            'reference': Payment.objects.get().reference,
            'payment_method': {'brand': 'visa', 'last4': '4242'},
        })

        membership = self.membership()
        self.assertEqual(membership.state, 'active')
        self.assertFalse(membership.auto_renew, 'nothing renews itself here')
        # An annual plan buys a year, worked out from the cadence since no
        # subscription exists to tell us.
        self.assertAlmostEqual(
            (membership.renews_at - timezone.now()).days, 365, delta=1,
        )
        self.assertEqual(Payment.objects.get().status, 'paid')

    def test_a_repeated_delivery_does_not_buy_a_second_period(self):
        self.buy(trial=False)
        reference = Payment.objects.get().reference
        event = {'reference': reference, 'payment_method': {'last4': '4242'}}

        self.post_webhook('collection.succeeded', event)
        first = self.membership().renews_at
        self.post_webhook('collection.succeeded', event)

        self.assertEqual(self.membership().renews_at, first)

    def test_a_period_bought_after_the_free_week_runs_from_today(self):
        self.buy()                       # the free week
        membership = self.membership()
        membership.lapse()

        self.buy()                       # now a real period
        self.post_webhook('collection.succeeded', {
            'reference': Payment.objects.get().reference,
        })

        self.assertEqual(self.membership().state, 'active')


@override_settings(
    BACHS_SECRET_KEY=SECRET,
    BACHS_WEBHOOK_SECRET=WEBHOOK_SECRET,
    MEMBERSHIP_BILLING='one_off',
)
class RenewalRemindersTests(BillingTestCase):
    """The jobs that stand in for a subscription: a warning, then an ending."""

    def today_at(self, hour):
        return timezone.now().replace(
            hour=hour, minute=0, second=0, microsecond=0,
        )

    def at(self, moment):
        """Run the jobs as though it were `moment`."""
        return patch('billing.tasks.timezone.now', return_value=moment)

    def member(self, state='active', days=2, auto_renew=False, reminded_at=None):
        """A member whose period ends at six in the evening, `days` from now.

        A fixed hour, and the jobs run at a fixed hour too: the rule is
        about calendar days, and a period ending at one in the morning falls
        on a different day from one ending at eleven at night. Without this
        the suite would pass or fail by the time of day it was run.
        """
        membership = Membership.objects.get_or_create(user=self.user)[0]
        membership.plan = self.plan
        membership.state = state
        membership.auto_renew = auto_renew
        membership.renews_at = self.today_at(18) + datetime.timedelta(days=days)
        membership.reminded_at = reminded_at
        membership.save()
        return membership

    def notifications(self):
        from notifications.models import Notification

        return Notification.objects.filter(user=self.user)

    def test_a_member_is_warned_while_there_is_time_to_act(self):
        from billing.tasks import send_renewal_reminders

        self.member(days=2)

        with self.at(self.today_at(6)):
            self.assertEqual(send_renewal_reminders(), 1)
        notice = self.notifications().get()
        self.assertIn('ends', notice.title)
        self.assertIn('150,000', notice.body)

    def test_two_days_off_is_worded_as_two_days(self):
        from billing.tasks import send_renewal_reminders

        # The wording and the decision have to count the same way. Counting
        # elapsed hours for one and calendar days for the other had a
        # reminder sent two days ahead saying "tomorrow", because 47h59m is
        # one day and a bit.
        self.member(days=2)

        with self.at(self.today_at(6)):
            send_renewal_reminders()

        self.assertIn('in 2 days', self.notifications().get().body)

    def test_the_free_week_ending_is_worded_as_a_free_week(self):
        from billing.tasks import send_renewal_reminders

        self.member(state='trial', days=0)

        with self.at(self.today_at(6)):
            send_renewal_reminders()

        self.assertIn('free week', self.notifications().get().title)

    def test_nobody_is_told_the_same_thing_twice_in_a_day(self):
        from billing.tasks import send_renewal_reminders

        self.member(days=2)

        with self.at(self.today_at(6)):
            send_renewal_reminders()
            self.assertEqual(send_renewal_reminders(), 0)
        self.assertEqual(self.notifications().count(), 1)

    def test_a_subscription_of_theirs_warns_for_itself(self):
        from billing.tasks import send_renewal_reminders

        # Bachs run their own subscriptions, warnings included; two
        # warnings is worse than one.
        membership = self.member(days=2, auto_renew=True)
        membership.subscription_id = 'sub_123'
        membership.save(update_fields=['subscription_id'])

        with self.at(self.today_at(6)):
            self.assertEqual(send_renewal_reminders(), 0)

    def test_a_period_with_weeks_left_is_not_mentioned_yet(self):
        from billing.tasks import send_renewal_reminders

        self.member(days=30)

        with self.at(self.today_at(6)):
            self.assertEqual(send_renewal_reminders(), 0)

    def test_a_period_that_ran_out_ends_the_access_it_paid_for(self):
        from billing.tasks import expire_lapsed_memberships

        self.member(days=-1)

        with self.at(self.today_at(6)):
            self.assertEqual(expire_lapsed_memberships(), 1)
        membership = Membership.objects.get(user=self.user)
        self.assertEqual(membership.state, 'inactive')
        self.assertFalse(membership.is_active)
        # And they are told, rather than finding out by being locked out.
        self.assertEqual(self.notifications().count(), 1)

    def test_an_overdue_subscription_is_left_to_the_reconciler(self):
        from billing.tasks import expire_lapsed_memberships

        # Bachs may simply be a few minutes behind with the renewal.
        self.member(days=-1, auto_renew=True)

        with self.at(self.today_at(6)):
            self.assertEqual(expire_lapsed_memberships(), 0)
        self.assertEqual(Membership.objects.get(user=self.user).state, 'active')

    def test_a_member_in_good_standing_is_not_ended(self):
        from billing.tasks import expire_lapsed_memberships

        self.member(days=10)

        with self.at(self.today_at(6)):
            self.assertEqual(expire_lapsed_memberships(), 0)


@override_settings(
    BACHS_SECRET_KEY=SECRET,
    BACHS_WEBHOOK_SECRET=WEBHOOK_SECRET,
    MEMBERSHIP_BILLING='one_off',
    TRIAL_REQUIRES_CARD=True,
    TRIAL_CARD_CHECK_NAIRA=100,
)
class CardCheckedTrialTests(BillingTestCase):
    """The free week, behind a card.

    An email address costs nothing, so a free week per address is a free
    library. A card is asked for to make the second one visible. Nothing is
    kept: the hundred naira that proves the card is given straight back,
    because Bachs offer no way to check a naira card for nothing.
    """

    CARD = {
        'payment_method_id': 'pm_123',
        'type': 'card',
        'card_brand': 'visa',
        'card_last4': '4242',
        'card_exp_month': 11,
        'card_exp_year': 2030,
        'is_default': True,
    }

    def ask_for_the_week(self, token=None):
        with patch('billing.bachs.checkout_saving_card', return_value=CHECKOUT) as call:
            response = self.client.post(
                reverse('api:billing:checkout'),
                {'plan': 'core', 'cadence': 'annual', 'trial': True},
                content_type='application/json',
                HTTP_AUTHORIZATION=token or self.token,
            )
        self.bachs_call = call
        return response

    def card_arrives(self, email=None, customer_id=None, **overrides):
        """The `payment_method.saved` delivery, as Bachs describes it.

        Each address is its own customer there, so the id follows the email
        rather than being shared -- which is what makes two accounts on one
        card distinguishable only by the card itself.
        """
        email = email or self.user.email
        data = dict(self.CARD, customer={
            'id': customer_id or 'cust_{0}'.format(abs(hash(email)) % 10000),
            'email': email,
        })
        data.update(overrides)
        return self.post_webhook('payment_method.saved', data)

    def membership(self, user=None):
        return Membership.objects.get(user=user or self.user)

    # ------------------------------------------------------- asking for it

    def test_the_week_begins_with_a_card_not_a_promise(self):
        response = self.ask_for_the_week()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body['card_check'])
        self.assertEqual(body['card_check_naira'], 100)
        self.assertEqual(body['checkout_url'], CHECKOUT['checkout_url'])
        # Nothing is being bought, so nothing is due.
        self.assertEqual(body['due_today_naira'], 0)
        # And the week has not started: an unfinished checkout is not a card.
        self.assertEqual(self.membership().state, 'inactive')

    def test_the_charge_is_the_smallest_bachs_allow_and_keeps_the_card(self):
        self.ask_for_the_week()

        self.assertEqual(self.bachs_call.call_args.kwargs['naira'], 100)
        self.assertEqual(Payment.objects.get().kind, 'card_check')

    def test_abandoning_the_checkout_grants_nothing(self):
        self.ask_for_the_week()

        # A checkout nobody finished is not a card, so the week is still
        # there to be taken.
        self.assertEqual(self.membership().state, 'inactive')
        self.assertFalse(self.membership().trial_used)

    # ------------------------------------------------------- the card lands

    def test_a_card_that_works_starts_the_week(self):
        self.ask_for_the_week()

        self.card_arrives()

        membership = self.membership()
        self.assertEqual(membership.state, 'trial')
        self.assertTrue(membership.is_active)
        # A card we are allowed to use means the week will try to renew
        # itself when it ends, and the screen says so.
        self.assertTrue(membership.auto_renew)
        self.assertEqual(membership.card_last4, '4242')
        self.assertEqual(membership.payment_method_id, 'pm_123')
        self.assertTrue(membership.card_signature)

    def test_the_same_card_cannot_have_a_second_week_under_a_new_email(self):
        # Ada takes her week.
        self.ask_for_the_week()
        self.card_arrives()
        self.assertEqual(self.membership().state, 'trial')

        # Bola signs up with a different address and the same card.
        Profile.create_user(email='bola@example.com', password='Soothify2026')
        bola_token = self.signed_in_as('bola@example.com', 'Soothify2026')
        self.ask_for_the_week(token=bola_token)
        self.card_arrives(email='bola@example.com')

        bola = get_user_model().objects.get(email='bola@example.com')
        self.assertEqual(self.membership(bola).state, 'inactive')
        self.assertIsNone(self.membership(bola).trial_started_at)
        # And told why, rather than left wondering.
        from notifications.models import Notification
        notice = Notification.objects.get(user=bola)
        self.assertIn('already had a free week', notice.title)

    def test_a_refused_week_is_not_offered_again(self):
        self.ask_for_the_week()
        self.card_arrives()

        Profile.create_user(email='bola@example.com', password='Soothify2026')
        bola_token = self.signed_in_as('bola@example.com', 'Soothify2026')
        self.ask_for_the_week(token=bola_token)
        self.card_arrives(email='bola@example.com')

        offer = self.client.get(
            reverse('api:billing:membership'), HTTP_AUTHORIZATION=bola_token,
        ).json()

        # Otherwise the next tap takes another hundred naira to reach the
        # same answer.
        self.assertFalse(offer['trial_available'])

    def test_a_refused_week_does_not_count_as_a_week_on_that_card(self):
        self.ask_for_the_week()
        self.card_arrives()

        Profile.create_user(email='bola@example.com', password='Soothify2026')
        bola_token = self.signed_in_as('bola@example.com', 'Soothify2026')
        self.ask_for_the_week(token=bola_token)
        self.card_arrives(email='bola@example.com')

        bola = get_user_model().objects.get(email='bola@example.com')
        # The card has one week against it -- Ada's -- not two.
        self.assertIsNone(self.membership(bola).trial_started_at)
        self.assertIsNotNone(self.membership(bola).trial_refused_at)

    def test_a_different_card_is_a_different_person_as_far_as_we_know(self):
        self.ask_for_the_week()
        self.card_arrives()

        Profile.create_user(email='bola@example.com', password='Soothify2026')
        bola_token = self.signed_in_as('bola@example.com', 'Soothify2026')
        self.ask_for_the_week(token=bola_token)
        self.card_arrives(email='bola@example.com', card_last4='1881')

        bola = get_user_model().objects.get(email='bola@example.com')
        self.assertEqual(self.membership(bola).state, 'trial')

    def test_the_card_of_an_existing_member_is_filed_not_judged(self):
        self.ask_for_the_week()
        self.card_arrives()

        # The same card arriving again -- a renewal, say -- must not be read
        # as somebody taking a second week.
        self.card_arrives()

        self.assertEqual(self.membership().state, 'trial')

    def test_the_signature_is_a_hash_and_not_the_card(self):
        self.ask_for_the_week()
        self.card_arrives()

        signature = self.membership().card_signature
        self.assertEqual(len(signature), 64)
        self.assertNotIn('4242', signature)
        # The same four details always name the same card.
        self.assertEqual(
            signature,
            Membership.signature_for('visa', '4242', 11, 2030),
        )

    # ---------------------------------------------------- changing the card

    def test_a_member_can_change_the_card_on_file(self):
        with patch('billing.bachs.checkout_saving_card', return_value=CHECKOUT) as call:
            response = self.client.post(
                reverse('api:billing:card'), {},
                content_type='application/json', HTTP_AUTHORIZATION=self.token,
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['checkout_url'], CHECKOUT['checkout_url'])
        # The same hundred-naira check as the free week, and nothing bought.
        self.assertEqual(body['card_check_naira'], 100)
        self.assertEqual(body['due_today_naira'], 0)
        self.assertEqual(call.call_args.kwargs['naira'], 100)
        self.assertEqual(Payment.objects.get().kind, 'card_check')

    def test_changing_the_card_gives_the_check_back_too(self):
        with patch('billing.bachs.checkout_saving_card', return_value=CHECKOUT):
            self.client.post(
                reverse('api:billing:card'), {},
                content_type='application/json', HTTP_AUTHORIZATION=self.token,
            )

        with patch('billing.bachs.refund', return_value={'refund_id': 'rfnd_9'}) as refund:
            self.post_webhook('collection.succeeded', {
                'reference': Payment.objects.get().reference,
                'charge_id': 'ch_9',
            })

        refund.assert_called_once()
        self.assertEqual(Payment.objects.get().status, 'refunded')

    def test_the_new_card_replaces_the_old_one_on_file(self):
        with patch('billing.bachs.checkout_saving_card', return_value=CHECKOUT):
            self.client.post(
                reverse('api:billing:card'), {},
                content_type='application/json', HTTP_AUTHORIZATION=self.token,
            )

        self.card_arrives(card_last4='1881', payment_method_id='pm_new')

        membership = self.membership()
        self.assertEqual(membership.card_last4, '1881')
        self.assertEqual(membership.payment_method_id, 'pm_new')

    # ------------------------------------------------------ giving it back

    def test_the_hundred_naira_goes_straight_back(self):
        self.ask_for_the_week()
        payment = Payment.objects.get()

        with patch('billing.bachs.refund', return_value={'refund_id': 'rfnd_1'}) as refund:
            self.post_webhook('collection.succeeded', {
                'reference': payment.reference,
                'charge_id': 'ch_123',
                'payment_method': {'brand': 'visa', 'last4': '4242'},
            })

        self.assertEqual(refund.call_args.kwargs['charge_id'], 'ch_123')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'refunded')
        self.assertEqual(payment.refund_id, 'rfnd_1')

    def test_a_refund_that_fails_is_left_visible_rather_than_swallowed(self):
        from billing.bachs import BachsError
        from billing.tasks import give_back_card_check

        self.ask_for_the_week()
        payment = Payment.objects.get()
        payment.charge_id = 'ch_123'
        payment.save(update_fields=['charge_id'])

        with patch('billing.bachs.refund', side_effect=BachsError('no')):
            self.assertFalse(give_back_card_check(payment.pk))

        payment.refresh_from_db()
        # Still showing as money taken, because it is.
        self.assertEqual(payment.status, 'paid' if payment.paid_at else 'pending')
        self.assertEqual(payment.refund_id, '')

    def test_the_card_check_never_appears_on_billing_history(self):
        self.ask_for_the_week()
        # Patched, or the refund the webhook kicks off would be a real call
        # to Bachs from a test run.
        with patch('billing.bachs.refund', return_value={'refund_id': 'rfnd_2'}):
            self.post_webhook('collection.succeeded', {
                'reference': Payment.objects.get().reference,
                'charge_id': 'ch_123',
            })

        history = self.client.get(
            reverse('api:billing:invoices'), HTTP_AUTHORIZATION=self.token,
        ).json()

        rows = history.get('results', history)
        self.assertEqual(rows, [])


@override_settings(
    BACHS_SECRET_KEY=SECRET,
    BACHS_WEBHOOK_SECRET=WEBHOOK_SECRET,
    MEMBERSHIP_BILLING='one_off',
    MEMBERSHIP_AUTO_CHARGE=True,
    MEMBERSHIP_CHARGE_GRACE_HOURS=48,
)
class ChargingTheSavedCardTests(BillingTestCase):
    """Taking the next period from a card we hold.

    This is what stands in for a subscription. It is allowed to fail, and
    it often will: an off-session charge cannot ask for the OTP most banks
    here want, so what matters as much as the charge working is what
    happens when it does not.
    """

    def member(self, days=0, **overrides):
        """A member whose period ends `days` from now, with a card on file."""
        membership = Membership.objects.get_or_create(user=self.user)[0]
        membership.plan = self.plan
        membership.state = 'trial'
        membership.auto_renew = True
        membership.customer_id = 'cust_123'
        membership.payment_method_id = 'pm_123'
        membership.card_brand = 'Mastercard'
        membership.card_last4 = '8821'
        membership.renews_at = timezone.now() + datetime.timedelta(days=days)
        for field, value in overrides.items():
            setattr(membership, field, value)
        membership.save()
        return membership

    def notices(self):
        from notifications.models import Notification

        return Notification.objects.filter(user=self.user)

    def test_the_card_is_charged_the_day_the_period_ends(self):
        from billing.tasks import charge_due_memberships

        self.member(days=0)

        with patch('billing.bachs.charge_saved_card',
                   return_value={'payment_id': 'ch_1', 'status': 'processing'}) as charge:
            self.assertEqual(charge_due_memberships(), 1)

        asked = charge.call_args.kwargs
        self.assertEqual(asked['customer_id'], 'cust_123')
        self.assertEqual(asked['payment_method_id'], 'pm_123')
        self.assertEqual(asked['naira'], 150000)

    def test_processing_is_not_paid(self):
        from billing.tasks import charge_due_memberships

        membership = self.member(days=0)
        was = membership.renews_at

        with patch('billing.bachs.charge_saved_card',
                   return_value={'status': 'processing'}):
            charge_due_memberships()

        # "Processing" is not money. Only the webhook may extend anything.
        membership.refresh_from_db()
        self.assertEqual(membership.renews_at, was)
        self.assertEqual(membership.state, 'trial')
        self.assertEqual(Payment.objects.get(kind='membership').status, 'pending')

    def test_the_webhook_turns_the_charge_into_a_period(self):
        from billing.tasks import charge_due_memberships

        self.member(days=0)
        with patch('billing.bachs.charge_saved_card', return_value={'status': 'processing'}):
            charge_due_memberships()

        self.post_webhook('collection.succeeded', {
            'reference': Payment.objects.get(kind='membership').reference,
            'charge_id': 'ch_1',
        })

        membership = Membership.objects.get(user=self.user)
        self.assertEqual(membership.state, 'active')
        self.assertAlmostEqual(
            (membership.renews_at - timezone.now()).days, 365, delta=1,
        )
        # Still ours to charge next time, since the card is still here.
        self.assertTrue(membership.auto_renew)

    def test_a_bank_that_wants_its_customer_hands_over_to_the_reminders(self):
        from billing.bachs import BachsError
        from billing.tasks import charge_due_memberships

        self.member(days=0)

        with patch('billing.bachs.charge_saved_card',
                   side_effect=BachsError('PAYMENT_METHOD_UNUSABLE')):
            charge_due_memberships()

        membership = Membership.objects.get(user=self.user)
        # Not retried tomorrow, and not treated as a fault: the member is
        # asked to pay, which is where they would have been anyway.
        self.assertFalse(membership.auto_renew)
        self.assertEqual(Payment.objects.get(kind='membership').status, 'failed')
        notice = self.notices().get()  # one message, not a pile of them
        self.assertIn('confirm your payment', notice.title)
        self.assertIn('8821', notice.body + membership.card_label)

    def test_a_failed_charge_leaves_time_to_pay_by_hand(self):
        from billing.bachs import BachsError
        from billing.tasks import charge_due_memberships, expire_lapsed_memberships

        self.member(days=0)

        with patch('billing.bachs.charge_saved_card', side_effect=BachsError('no')):
            charge_due_memberships()

        membership = Membership.objects.get(user=self.user)
        # The message says "tap to pay and keep your membership". Ending it
        # in the same minute would make that a lie.
        self.assertTrue(membership.is_active)
        self.assertGreater(membership.renews_at, timezone.now())
        self.assertEqual(expire_lapsed_memberships(), 0)
        # And the message says how long they have.
        self.assertIn('access stays on until', self.notices().get().body)

    def test_a_failed_charge_is_the_only_word_on_it_that_morning(self):
        from billing.bachs import BachsError
        from billing.tasks import charge_due_memberships, send_renewal_reminders

        self.member(days=0)

        with patch('billing.bachs.charge_saved_card', side_effect=BachsError('no')):
            charge_due_memberships()
        sent = send_renewal_reminders()

        # One message about the money, not two in the same morning -- and
        # certainly not one promising to charge a card we have put down.
        self.assertEqual(sent, 0)
        self.assertEqual(self.notices().count(), 1)

    def test_a_card_put_down_is_not_promised_to_the_member(self):
        from billing.bachs import BachsError
        from billing.tasks import charge_due_memberships, send_renewal_reminders

        membership = self.member(days=0)
        with patch('billing.bachs.charge_saved_card', side_effect=BachsError('no')):
            charge_due_memberships()

        # Two days before the grace runs out, which is a reminder day.
        membership.refresh_from_db()
        membership.reminded_at = None
        membership.renews_at = timezone.now().replace(
            hour=18, minute=0, second=0, microsecond=0,
        ) + datetime.timedelta(days=2)
        membership.save(update_fields=['reminded_at', 'renews_at'])

        with patch('billing.tasks.timezone.now',
                   return_value=timezone.now().replace(hour=6, minute=0)):
            self.assertEqual(send_renewal_reminders(), 1)

        body = self.notices().latest('id').body
        # The card has been put down, so nothing may promise to charge it.
        self.assertNotIn('We will charge', body)
        self.assertIn('Pay \u20a6150,000', body)

    def test_a_card_is_not_charged_twice_in_a_day(self):
        from billing.tasks import charge_due_memberships

        self.member(days=0)

        with patch('billing.bachs.charge_saved_card',
                   return_value={'status': 'processing'}) as charge:
            charge_due_memberships()
            charge_due_memberships()

        self.assertEqual(charge.call_count, 1)

    def test_a_period_with_days_to_run_is_left_alone(self):
        from billing.tasks import charge_due_memberships

        self.member(days=3)

        with patch('billing.bachs.charge_saved_card') as charge:
            self.assertEqual(charge_due_memberships(), 0)
        charge.assert_not_called()

    def test_a_membership_with_no_card_is_left_to_the_reminders(self):
        from billing.tasks import charge_due_memberships

        self.member(days=0, payment_method_id='')

        with patch('billing.bachs.charge_saved_card') as charge:
            self.assertEqual(charge_due_memberships(), 0)
        charge.assert_not_called()

    @override_settings(MEMBERSHIP_AUTO_CHARGE=False)
    def test_nothing_is_charged_when_the_switch_is_off(self):
        from billing.tasks import charge_due_memberships

        self.member(days=0)

        with patch('billing.bachs.charge_saved_card') as charge:
            charge_due_memberships()

        charge.assert_not_called()
        self.assertFalse(Membership.objects.get(user=self.user).auto_renew)

    def test_a_charge_that_never_comes_back_is_given_up_on(self):
        from billing.tasks import give_up_on_stuck_charges

        # Tried three days ago; no webhook ever arrived.
        self.member(
            days=-3,
            charge_attempted_at=timezone.now() - datetime.timedelta(days=3),
        )

        self.assertEqual(give_up_on_stuck_charges(), 1)

        membership = Membership.objects.get(user=self.user)
        self.assertFalse(membership.auto_renew)
        self.assertEqual(self.notices().count(), 1)
        # Which hands it to the lapse job, as an unpaid period.
        from billing.tasks import expire_lapsed_memberships
        self.assertEqual(expire_lapsed_memberships(), 1)

    def test_cancelling_puts_the_card_down(self):
        from billing.tasks import charge_due_memberships, expire_lapsed_memberships

        membership = self.member(days=0)
        membership.cancel()

        with patch('billing.bachs.charge_saved_card') as charge:
            charge_due_memberships()

        # "Cancel" has to mean the card is not charged again...
        charge.assert_not_called()
        self.assertFalse(Membership.objects.get(user=self.user).auto_renew)
        # ...and the period still ends when it ends, rather than sitting
        # cancelled-but-active for good.
        self.assertEqual(expire_lapsed_memberships(), 1)
        self.assertFalse(Membership.objects.get(user=self.user).is_active)

    def test_a_charge_from_this_morning_is_still_waiting(self):
        from billing.tasks import give_up_on_stuck_charges

        self.member(days=0, charge_attempted_at=timezone.now())

        self.assertEqual(give_up_on_stuck_charges(), 0)

    def test_the_warning_names_the_card_and_the_amount(self):
        from billing.tasks import send_renewal_reminders

        self.member(days=2)

        with patch('billing.tasks.timezone.now',
                   return_value=timezone.now().replace(hour=6)):
            send_renewal_reminders()

        body = self.notices().get().body
        # Somebody about to be charged is told so, with the card named and
        # a way out, before the money moves.
        self.assertIn('150,000', body)
        self.assertIn('8821', body)
        self.assertIn('Cancel any time', body)
