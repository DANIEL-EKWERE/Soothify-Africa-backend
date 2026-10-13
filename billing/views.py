"""/api/v1/billing/ -- plans, memberships, checkout, invoices and the webhook.

The rule that shapes all of it: **only a signed webhook may grant anything**.
The app saying "I paid" is a claim; a signed delivery from Bachs is evidence.
Their own documentation says the same -- fulfil from the webhook, not from
the redirect the customer lands on.

What Bachs runs, and we therefore do not: the free trial, the charge when it
ends, renewals, and retries on a failed card. We record what they tell us.
"""

import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from experts.models import Session

from . import bachs
from .models import Cadence, Membership, Payment, Plan, PromoCode
from .serializers import (
    CheckoutSerializer,
    InvoiceSerializer,
    MembershipSerializer,
    PlanSerializer,
    PromoSerializer,
)

logger = logging.getLogger(__name__)


class PlanListView(generics.ListAPIView):
    """The three cards. Open: the Plans screen sells to people without accounts."""

    serializer_class = PlanSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        plans = Plan.objects.filter(is_active=True)
        cadence = self.request.query_params.get('period')
        if cadence in (Cadence.MONTHLY, Cadence.ANNUAL):
            # A card with no cadence (Passport, Corporate) shows under both.
            plans = plans.filter(cadence__in=[cadence, ''])
        return plans


class MembershipView(APIView):
    """Where this account stands."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        membership, _created = Membership.objects.get_or_create(user=request.user)
        return Response(MembershipSerializer(membership).data)


class InvoiceListView(generics.ListAPIView):
    """Billing History."""

    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(
            user=self.request.user, kind='membership',
        ).exclude(status='pending')


class CheckoutView(APIView):
    """Start a membership. Returns a Bachs checkout URL for the app to open.

    Nothing is charged here even when the plan has a trial: Bachs saves the
    card at checkout and bills the first cycle when the seven days are up.
    """

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        form = CheckoutSerializer(data=request.data)
        form.is_valid(raise_exception=True)

        plan = get_object_or_404(
            Plan, key=form.validated_data['plan'], is_active=True,
        )
        if plan.price_naira is None:
            return Response(
                {'detail': 'This plan is not sold here.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not plan.bachs_product_id:
            # Without a product at Bachs there is nothing to subscribe to.
            # Said plainly rather than failing somewhere inside their API.
            return Response(
                {'detail': 'This plan is not set up for payment yet.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        membership, _created = Membership.objects.get_or_create(user=request.user)
        if membership.state in ('trial', 'active'):
            return Response(
                {'detail': 'This account already has a membership.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment = Payment.objects.create(
            user=request.user,
            kind='membership',
            amount_naira=plan.price_naira,
            description='{0} ({1})'.format(
                plan.name, plan.get_cadence_display() or 'one-off',
            ),
            membership=membership,
            reference=bachs.reference('mem'),
        )

        # Remembered so the webhook knows what this checkout was buying.
        membership.plan = plan
        membership.cadence = form.validated_data['cadence']
        membership.save(update_fields=['plan', 'cadence', 'updated_at'])

        try:
            checkout = bachs.checkout_for_product(
                product_id=plan.bachs_product_id,
                email=request.user.email,
                name=getattr(getattr(request.user, 'profile', None), 'full_name', ''),
                reference_=payment.reference,
                success_url=_return_url(),
                metadata={'kind': 'membership', 'user_id': request.user.pk},
            )
        except bachs.BachsError as failure:
            payment.status = 'failed'
            payment.save(update_fields=['status'])
            return Response(
                {'detail': str(failure)}, status=status.HTTP_502_BAD_GATEWAY,
            )

        payment.checkout_id = checkout.get('checkout_id', '')
        payment.save(update_fields=['checkout_id'])

        return Response({
            'reference': payment.reference,
            'amount_naira': plan.price_naira,
            'checkout_url': checkout.get('checkout_url', ''),
            'checkout_id': payment.checkout_id,
            'trial_days': plan.trial_days,
            # Nothing is taken today when the plan has a trial, which is what
            # the app's "Due today ₦0.00" line is telling the user.
            'due_today_naira': 0 if plan.trial_days else plan.price_naira,
        }, status=status.HTTP_201_CREATED)


class SessionCheckoutView(APIView):
    """Paying for a booked session -- the screen after the calendar."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        session = get_object_or_404(Session, pk=pk, client=request.user)
        if session.status != 'pending':
            return Response(
                {'detail': 'This session is not awaiting payment.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount, code = _discounted(
            session.price_naira, request.data.get('promo_code'),
        )
        payment = Payment.objects.create(
            user=request.user,
            kind='session',
            amount_naira=amount,
            description='{0} session with {1}'.format(
                session.get_discipline_display(), session.expert.display_name,
            ),
            session=session,
            promo_code=code,
            reference=bachs.reference('ses'),
        )

        try:
            # Ad-hoc pricing: one session is not a product, and creating one
            # per booking would fill their catalogue with rubbish.
            checkout = bachs.checkout_for_amount(
                naira=amount,
                email=request.user.email,
                reference_=payment.reference,
                success_url=_return_url(),
                metadata={'kind': 'session', 'session_id': session.pk},
            )
        except bachs.BachsError as failure:
            payment.status = 'failed'
            payment.save(update_fields=['status'])
            return Response(
                {'detail': str(failure)}, status=status.HTTP_502_BAD_GATEWAY,
            )

        payment.checkout_id = checkout.get('checkout_id', '')
        payment.save(update_fields=['checkout_id'])

        return Response({
            'reference': payment.reference,
            'amount_naira': amount,
            'checkout_url': checkout.get('checkout_url', ''),
            'checkout_id': payment.checkout_id,
        }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_membership(request):
    """Stop renewing. Access runs to the end of the paid period.

    Told to Bachs as well as recorded here: a cancellation they do not know
    about is a charge the member will still receive.
    """
    membership = get_object_or_404(Membership, user=request.user)
    if membership.state not in ('trial', 'active'):
        return Response(
            {'detail': 'There is nothing to cancel.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if membership.subscription_id:
        try:
            bachs.cancel_subscription(membership.subscription_id)
        except bachs.BachsError as failure:
            logger.warning(
                'could not cancel subscription %s: %s',
                membership.subscription_id, failure,
            )
            return Response(
                {'detail': 'Could not cancel just now. Try again in a moment.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    membership.cancel()
    return Response(MembershipSerializer(membership).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def check_promo(request):
    """What a code is worth, before anyone commits to paying."""
    form = PromoSerializer(data=request.data)
    form.is_valid(raise_exception=True)

    promo = PromoCode.objects.filter(
        code__iexact=form.validated_data['code'].strip(),
    ).first()
    if promo is None or not promo.is_usable:
        return Response(
            {'valid': False, 'detail': 'That code cannot be used.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response({
        'valid': True,
        'code': promo.code,
        'percent_off': promo.percent_off,
        'amount_off_naira': promo.amount_off_naira,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_status(request, reference):
    """Has this payment landed yet?

    The app polls this after checkout. It reports what the webhook recorded
    rather than asking Bachs again on every poll.
    """
    payment = get_object_or_404(Payment, reference=reference, user=request.user)
    return Response({
        'reference': payment.reference,
        'status': payment.status,
        'amount_naira': payment.amount_naira,
    })


@csrf_exempt
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def webhook(request):
    """Bachs telling us what happened.

    Verified against the raw body before anything is parsed -- re-serialising
    the JSON changes the bytes and the signature no longer matches. Answers
    200 for events we ignore, so they stop retrying.
    """
    header = (
        request.META.get('HTTP_X_BACHS_SIGNATURE_V2')
        or request.META.get('HTTP_X_BACHS_SIGNATURE', '')
    )
    if not bachs.signature_is_valid(request.body, header):
        logger.warning('rejected a webhook with a bad signature')
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    event = request.data.get('event') or request.data.get('type') or ''
    data = request.data.get('data') or {}

    handler = {
        'checkout.completed': _checkout_completed,
        'collection.succeeded': _collection_succeeded,
        'customer.subscription.created': _subscription_changed,
        'customer.subscription.updated': _subscription_changed,
        'customer.subscription.deleted': _subscription_deleted,
        'invoice.paid': _invoice_paid,
        'invoice.payment_failed': _invoice_failed,
    }.get(event)

    if handler is None:
        logger.info('ignoring bachs event %s', event)
    else:
        handler(data)
    return Response({'received': True})


# ----------------------------------------------------------- event handlers

def _checkout_completed(data):
    """A checkout finished. For a trial, this is where access begins."""
    payment = _payment_for(data)
    if payment is None:
        return

    subscription = data.get('subscription') or {}
    membership = payment.membership
    if membership is None or payment.kind != 'membership':
        return

    _remember_customer(membership, data)

    subscription_id = subscription.get('subscription_id') or subscription.get('id', '')
    trial_end = _as_datetime(subscription.get('trial_end'))
    state = subscription.get('status', '')

    if state == 'trialing' or trial_end:
        membership.start_trial(
            membership.plan, membership.cadence,
            trial_ends_at=trial_end, subscription_id=subscription_id,
        )
        # Nothing was charged, so the trial leaves no invoice. Billing
        # History starts at the first real charge.
        payment.status = 'pending'
        payment.save(update_fields=['status'])
    else:
        if subscription_id:
            membership.subscription_id = subscription_id
            membership.save(update_fields=['subscription_id', 'updated_at'])
        _mark_paid(payment, data)
        membership.extend(_as_datetime(subscription.get('current_period_end')))


def _collection_succeeded(data):
    """Money arrived for a one-off -- a booked session."""
    payment = _payment_for(data)
    if payment is None or payment.status == 'paid':
        return

    _mark_paid(payment, data)
    if payment.kind == 'session' and payment.session_id:
        payment.session.mark_paid()


def _invoice_paid(data):
    """A billing cycle was charged: the trial ending, or a renewal."""
    membership = _membership_for(data)
    if membership is None:
        return

    amount = bachs.from_money(data.get('amount_paid') or data.get('amount') or 0)
    reference = data.get('invoice_id') or data.get('id') or bachs.reference('inv')
    payment, created = Payment.objects.get_or_create(
        reference='bachs_{0}'.format(reference),
        defaults={
            'user': membership.user,
            'kind': 'membership',
            'amount_naira': amount or membership.next_charge_naira,
            'description': '{0} ({1})'.format(
                membership.plan.name if membership.plan else 'Membership',
                membership.get_cadence_display(),
            ),
            'membership': membership,
        },
    )
    if not created and payment.status == 'paid':
        return          # they retry; a second extension would be a free month

    _mark_paid(payment, data)
    membership.extend(_as_datetime(data.get('period_end')))


def _invoice_failed(data):
    """A charge did not go through.

    Marked past due rather than cancelled: Bachs runs its own recovery, and
    the usual cause is a card that needs topping up.
    """
    membership = _membership_for(data)
    if membership is None:
        return
    membership.state = 'past_due'
    membership.save(update_fields=['state', 'updated_at'])


def _subscription_changed(data):
    membership = _membership_for(data)
    if membership is None:
        return

    subscription_id = data.get('subscription_id') or data.get('id', '')
    if subscription_id and not membership.subscription_id:
        membership.subscription_id = subscription_id

    state = data.get('status', '')
    if state == 'trialing':
        membership.state = 'trial'
        membership.trial_ends_at = _as_datetime(data.get('trial_end'))
        membership.renews_at = membership.trial_ends_at
    elif state == 'active':
        membership.state = 'active'
        membership.renews_at = _as_datetime(data.get('current_period_end')) or membership.renews_at
        membership.trial_ends_at = None
    elif state in ('canceled', 'cancelled'):
        membership.state = 'cancelled'
        membership.cancelled_at = timezone.now()
    membership.save()


def _subscription_deleted(data):
    membership = _membership_for(data)
    if membership is None:
        return
    membership.cancel()


# --------------------------------------------------------------- small parts

def _payment_for(data):
    """Our Payment row for an event, found by the reference we sent."""
    reference = (
        data.get('reference')
        or (data.get('metadata') or {}).get('reference')
        or ''
    )
    if not reference:
        logger.warning('bachs event carried no reference')
        return None
    payment = Payment.objects.filter(reference=reference).select_related(
        'membership', 'session',
    ).first()
    if payment is None:
        logger.warning('bachs event for an unknown reference %s', reference)
    return payment


def _membership_for(data):
    """The membership an event belongs to.

    By subscription first, then by the customer, then by the reference on the
    payment that started it -- the earliest events arrive before we have
    stored a subscription id.
    """
    subscription_id = (
        data.get('subscription_id')
        or data.get('id')
        or (data.get('subscription') or {}).get('subscription_id', '')
    )
    if subscription_id:
        found = Membership.objects.filter(subscription_id=subscription_id).first()
        if found is not None:
            return found

    customer = data.get('customer') or {}
    customer_id = customer.get('customer_id') or customer.get('id') or data.get('customer_id', '')
    if customer_id:
        found = Membership.objects.filter(customer_id=customer_id).first()
        if found is not None:
            return found

    payment = _payment_for(data)
    return payment.membership if payment is not None else None


def _remember_customer(membership, data):
    customer = data.get('customer') or {}
    customer_id = customer.get('customer_id') or customer.get('id') or ''
    if customer_id and membership.customer_id != customer_id:
        membership.customer_id = customer_id
        membership.save(update_fields=['customer_id', 'updated_at'])


def _mark_paid(payment, data):
    payment.status = 'paid'
    payment.paid_at = timezone.now()
    payment.card_label = _card_label(data)
    payment.save(update_fields=['status', 'paid_at', 'card_label'])

    if payment.promo_code:
        from django.db.models import F

        PromoCode.objects.filter(code__iexact=payment.promo_code).update(
            used_count=F('used_count') + 1,
        )

    membership = payment.membership
    method = data.get('payment_method') or {}
    if membership is not None and method.get('last4'):
        membership.card_brand = (method.get('brand') or '').title()
        membership.card_last4 = method.get('last4', '')
        membership.save(update_fields=['card_brand', 'card_last4', 'updated_at'])


def _card_label(data):
    method = data.get('payment_method') or {}
    last4 = method.get('last4') or ''
    if not last4:
        return ''
    return '{0} {1}'.format((method.get('brand') or 'Card').title(), last4).strip()


def _discounted(naira, code):
    """The amount after a promo code, and the code that was applied."""
    code = (code or '').strip()
    if not code:
        return naira, ''
    promo = PromoCode.objects.filter(code__iexact=code).first()
    if promo is None or not promo.is_usable:
        return naira, ''
    return promo.apply_to(naira), promo.code


def _as_datetime(value):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is not None and timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.utc)
    return parsed


def _return_url():
    from django.conf import settings

    return getattr(settings, 'BACHS_RETURN_URL', '') or settings.SITE_URL
