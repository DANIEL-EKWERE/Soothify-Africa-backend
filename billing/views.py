"""/api/v1/billing/ -- plans, the trial, checkout, invoices and the webhook.

The rule that shapes all of it: **only Paystack's webhook may mark anything
paid**. The app saying "I paid" is a claim; the webhook, whose signature we
check, is evidence. That is also why starting a trial does not itself grant
access -- the card has to be verified first.
"""

import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
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

from . import paystack
from .models import Cadence, Membership, Payment, Plan, PromoCode
from .serializers import (
    CheckoutSerializer,
    InvoiceSerializer,
    MembershipSerializer,
    PlanSerializer,
    PromoSerializer,
)

logger = logging.getLogger(__name__)

# What we charge to prove a card works before a free trial. Paystack cannot
# authorise zero, so the trial verifies the card with a small charge and
# refunds it. The alternative -- no card until day 8 -- means chasing people
# for payment after they have had the week, which is worse for everyone.
CARD_CHECK_NAIRA = 50


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
    """Billing History. Only real charges -- a card check is not an invoice."""

    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(
            user=self.request.user, kind='membership',
        ).exclude(status='pending')


class CheckoutView(APIView):
    """Start a membership: returns a Paystack URL for the app to open.

    With `trial`, the charge is the small card check and the seven days begin
    once it succeeds. Without it, the full plan price is taken now.
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

        membership, _created = Membership.objects.get_or_create(user=request.user)
        if membership.state in ('trial', 'active'):
            return Response(
                {'detail': 'This account already has a membership.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        wants_trial = form.validated_data['trial']
        amount = CARD_CHECK_NAIRA if wants_trial else plan.price_naira
        code = ''
        if not wants_trial:
            amount, code = _discounted(amount, form.validated_data.get('promo_code'))

        payment = Payment.objects.create(
            user=request.user,
            kind='card_check' if wants_trial else 'membership',
            amount_naira=amount,
            description=(
                'Card verification for the 7-day trial' if wants_trial
                else '{0} ({1})'.format(plan.name, plan.get_cadence_display() or 'one-off')
            ),
            membership=membership,
            promo_code=code,
            reference=paystack.reference('mem'),
        )

        # Remembered so the webhook knows what this payment was buying.
        membership.plan = plan
        membership.cadence = form.validated_data['cadence']
        membership.save(update_fields=['plan', 'cadence', 'updated_at'])

        try:
            checkout = paystack.initialize(
                email=request.user.email,
                naira=amount,
                reference_=payment.reference,
                callback_url=_callback_url(),
                metadata={'kind': payment.kind, 'trial': wants_trial},
            )
        except paystack.PaystackError as failure:
            payment.status = 'failed'
            payment.save(update_fields=['status'])
            return Response(
                {'detail': str(failure)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({
            'reference': payment.reference,
            'amount_naira': amount,
            'authorization_url': checkout['authorization_url'],
            'access_code': checkout['access_code'],
            'trial': wants_trial,
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
            reference=paystack.reference('ses'),
        )

        try:
            checkout = paystack.initialize(
                email=request.user.email,
                naira=amount,
                reference_=payment.reference,
                callback_url=_callback_url(),
                metadata={'kind': 'session', 'session_id': session.pk},
            )
        except paystack.PaystackError as failure:
            payment.status = 'failed'
            payment.save(update_fields=['status'])
            return Response(
                {'detail': str(failure)}, status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({
            'reference': payment.reference,
            'amount_naira': amount,
            'authorization_url': checkout['authorization_url'],
            'access_code': checkout['access_code'],
        }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_membership(request):
    """Stop renewing. Access runs to the end of the paid period."""
    membership = get_object_or_404(Membership, user=request.user)
    if membership.state not in ('trial', 'active'):
        return Response(
            {'detail': 'There is nothing to cancel.'},
            status=status.HTTP_400_BAD_REQUEST,
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
    rather than asking Paystack again on every poll.
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
    """Paystack telling us what happened.

    Unsigned or wrongly signed requests are dropped without a word about why.
    Answers 200 even for events we ignore, so Paystack stops retrying.
    """
    signature = request.META.get('HTTP_X_PAYSTACK_SIGNATURE', '')
    if not paystack.signature_is_valid(request.body, signature):
        logger.warning('rejected a webhook with a bad signature')
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    event = request.data.get('event', '')
    data = request.data.get('data') or {}

    if event == 'charge.success':
        _settle(data)
    else:
        logger.info('ignoring paystack event %s', event)
    return Response({'received': True})


def _settle(data):
    """Record a successful charge, and give whatever it bought."""
    reference = data.get('reference', '')
    payment = Payment.objects.filter(reference=reference).select_related(
        'membership', 'session',
    ).first()
    if payment is None:
        logger.warning('paystack charge for an unknown reference %s', reference)
        return
    if payment.status == 'paid':
        # Paystack retries; settling twice would extend a membership twice.
        return

    authorization = data.get('authorization') or {}
    payment.status = 'paid'
    payment.paid_at = timezone.now()
    payment.card_label = _card_label(authorization)
    payment.save(update_fields=['status', 'paid_at', 'card_label'])

    if payment.promo_code:
        PromoCode.objects.filter(code__iexact=payment.promo_code).update(
            used_count=models_f('used_count') + 1,
        )

    if payment.kind == 'session' and payment.session_id:
        payment.session.mark_paid()
        return

    membership = payment.membership
    if membership is None:
        return

    # Keep the card so the trial can be charged on day 8 without asking again.
    membership.customer_code = (data.get('customer') or {}).get('customer_code', '')
    membership.authorization_code = authorization.get('authorization_code', '')
    membership.card_brand = (authorization.get('brand') or '').title()
    membership.card_last4 = authorization.get('last4', '')
    membership.save(update_fields=[
        'customer_code', 'authorization_code', 'card_brand', 'card_last4', 'updated_at',
    ])

    if payment.kind == 'card_check':
        membership.start_trial(membership.plan, membership.cadence)
    else:
        membership.extend()


def models_f(field):
    from django.db.models import F

    return F(field)


def _discounted(naira, code):
    """The amount after a promo code, and the code that was applied."""
    code = (code or '').strip()
    if not code:
        return naira, ''
    promo = PromoCode.objects.filter(code__iexact=code).first()
    if promo is None or not promo.is_usable:
        return naira, ''
    return promo.apply_to(naira), promo.code


def _card_label(authorization):
    brand = (authorization.get('brand') or '').title()
    last4 = authorization.get('last4') or ''
    if not last4:
        return ''
    return '{0} {1}'.format(brand or 'Card', last4).strip()


def _callback_url():
    from django.conf import settings

    return getattr(settings, 'PAYSTACK_CALLBACK_URL', '')
