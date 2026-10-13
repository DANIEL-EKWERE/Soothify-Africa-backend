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
    list_display = ('user', 'plan', 'cadence', 'state', 'trial_ends_at', 'renews_at')
    list_filter = ('state', 'cadence')
    search_fields = ('user__email',)
    # The card details come from Paystack and must not be edited by hand.
    readonly_fields = ('customer_id', 'subscription_id', 'card_brand',
                       'card_last4', 'created_at', 'updated_at')


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
