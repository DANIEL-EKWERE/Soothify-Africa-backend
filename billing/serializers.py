"""What the Plans, trial and Manage-subscription screens exchange.

PlanTier.fromJson in the app reads `name`, `description`, `price_display` and
`is_recommended`, so those names are fixed.
"""

from django.conf import settings
from rest_framework import serializers

from .models import Membership, Payment, Plan, PromoCode


class PlanSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source='key', read_only=True)
    price_display = serializers.CharField(read_only=True)
    label = serializers.CharField(source='name', read_only=True)

    class Meta:
        model = Plan
        fields = (
            'id', 'name', 'label', 'description', 'price_display',
            'price_naira', 'price_suffix', 'cadence', 'action',
            'is_recommended',
        )


class MembershipSerializer(serializers.ModelSerializer):
    """"Your Subscription", in one response."""

    plan_key = serializers.CharField(source='plan.key', read_only=True, default='')
    plan_name = serializers.CharField(source='plan.name', read_only=True, default='')
    card = serializers.CharField(source='card_label', read_only=True)
    next_charge_naira = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    # Whether `renews_at` is a renewal or an ending. The screen has to say
    # which: "renews on the 9th" when the card will be charged by itself,
    # "ends on the 9th" when the member has to come back and pay.
    auto_renew = serializers.BooleanField(read_only=True)

    # Whether "Start 7-Day Free Trial" is still a true offer for this
    # account. One free week each; after that the button sells a period.
    trial_available = serializers.SerializerMethodField()

    # What the free week asks for up front. The screen has to say this
    # before anyone taps, and saying it from the server keeps the figure
    # and the setting from drifting apart.
    trial_requires_card = serializers.SerializerMethodField()
    card_check_naira = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = (
            'state', 'plan_key', 'plan_name', 'cadence', 'trial_ends_at',
            'renews_at', 'cancelled_at', 'card', 'next_charge_naira',
            'is_active', 'auto_renew', 'trial_available',
            'trial_requires_card', 'card_check_naira',
        )
        read_only_fields = fields

    def get_trial_available(self, membership):
        return not membership.trial_used

    def get_trial_requires_card(self, membership):
        return bool(getattr(settings, 'TRIAL_REQUIRES_CARD', True))

    def get_card_check_naira(self, membership):
        if not self.get_trial_requires_card(membership):
            return 0
        return int(getattr(settings, 'TRIAL_CARD_CHECK_NAIRA', 100))


class InvoiceSerializer(serializers.ModelSerializer):
    """One line on Billing History."""

    status_label = serializers.CharField(read_only=True)

    class Meta:
        model = Payment
        fields = (
            'id', 'amount_naira', 'description', 'status', 'status_label',
            'paid_at', 'created_at',
        )
        read_only_fields = fields


class CheckoutSerializer(serializers.Serializer):
    """Starting a membership, with or without the trial."""

    plan = serializers.SlugField()
    cadence = serializers.ChoiceField(choices=['monthly', 'annual'], default='annual')
    trial = serializers.BooleanField(default=True)
    promo_code = serializers.CharField(required=False, allow_blank=True, default='')


class PromoSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)
