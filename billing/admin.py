from django.contrib import admin

from .models import Membership, Payment, Plan, PromoCode


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'key', 'price_naira', 'cadence', 'trial_days',
                    'sold', 'action', 'is_recommended', 'is_active', 'position')
    list_editable = ('is_active', 'position', 'is_recommended')
    search_fields = ('key', 'name')

    @admin.display(boolean=True, description='set up at Bachs')
    def sold(self, obj):
        """Whether this card can actually be bought.

        A priced plan with no Bachs product refuses checkout, so this is the
        first thing to look at when "start trial" stops working.
        """
        return bool(obj.bachs_product_id) or obj.price_naira is None


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'cadence', 'state', 'renews_at',
                    'renews_itself', 'free_week')
    list_filter = ('state', 'cadence', 'auto_renew')
    search_fields = ('user__email', 'card_last4')
    actions = ('allow_the_free_week_again',)

    # The card comes from Bachs and must not be edited by hand. The
    # signature is derived from it, so hand-editing that would only make
    # the duplicate check disagree with the card it came from.
    readonly_fields = ('customer_id', 'subscription_id', 'card_brand',
                       'card_last4', 'card_exp_month', 'card_exp_year',
                       'payment_method_id', 'card_signature',
                       'trial_started_at', 'created_at', 'updated_at')

    @admin.display(boolean=True, description='renews itself')
    def renews_itself(self, obj):
        return obj.auto_renew

    @admin.display(description='free week')
    def free_week(self, obj):
        """Taken, refused, or still going -- the question support is asked."""
        if obj.trial_started_at:
            return 'taken {0}'.format(obj.trial_started_at.date())
        if obj.trial_refused_at:
            return 'refused (card had one)'
        return 'available'

    @admin.action(description='Allow the free week again')
    def allow_the_free_week_again(self, request, queryset):
        """Undo a refusal.

        A card is recognised by its brand, last four digits and expiry, and
        two strangers can share all three -- so a refusal is occasionally
        wrong, and the person it was wrong about should not need a developer.

        Only clears a refusal. A week actually taken stays taken.
        """
        cleared = queryset.filter(
            trial_refused_at__isnull=False, trial_started_at__isnull=True,
        ).update(trial_refused_at=None)
        self.message_user(
            request,
            'The free week is on offer again for {0} of them.'.format(cleared),
        )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('reference', 'user', 'kind', 'amount_naira', 'status', 'created_at')
    list_filter = ('kind', 'status', 'created_at')
    search_fields = ('reference', 'user__email')
    # Money that moved is a record, not a form.
    readonly_fields = [field.name for field in Payment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'percent_off', 'amount_off_naira', 'used_count',
                    'max_uses', 'expires_at', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('code',)
    readonly_fields = ('used_count',)
