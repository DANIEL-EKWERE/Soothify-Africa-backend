"""Reviewing applications, and keeping an eye on bookings."""

from django.contrib import admin, messages
from django.utils import timezone

from accounts.models import Role

from .models import (
    Application,
    ApplicationDocument,
    AvailabilitySlot,
    ExpertProfile,
    Payout,
    PayoutMethod,
    Recommendation,
    Session,
    SessionNote,
)


@admin.register(ExpertProfile)
class ExpertProfileAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'disciplines', 'is_accepting', 'is_published', 'rating_average')
    list_filter = ('is_accepting', 'is_published')
    search_fields = ('profile__full_name', 'profile__user__email')
    autocomplete_fields = ('profile',)
    readonly_fields = ('rating_average', 'rating_count', 'created_at')


class ApplicationDocumentInline(admin.TabularInline):
    model = ApplicationDocument
    extra = 0
    readonly_fields = ('kind', 'file', 'uploaded_at')
    can_delete = False


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'practises', 'status', 'created_at', 'reviewed_at')
    list_filter = ('status', 'created_at')
    search_fields = ('full_name', 'user__email', 'licence_number')
    readonly_fields = ('user', 'full_name', 'disciplines', 'languages',
                       'experience_years', 'bio', 'portfolio_url',
                       'licence_number', 'insurance_provider', 'created_at',
                       'reviewed_by', 'reviewed_at')
    inlines = [ApplicationDocumentInline]
    actions = ['approve']

    @admin.display(description='practises')
    def practises(self, obj):
        return obj.disciplines_display

    @admin.action(description='Approve: create the expert and switch their role')
    def approve(self, request, queryset):
        """The same approval the dashboard performs -- see Application.approve.

        One implementation, because two would drift and the drift would be
        about who is allowed to take sessions.
        """
        made = 0
        invited = 0
        for application in queryset.exclude(status='approved'):
            _profile, was_invited = application.approve(reviewer=request.user)
            made += 1
            invited += 1 if was_invited else 0

        message = '{0} approved.'.format(made)
        if invited:
            message += (
                ' {0} had no account; one was made with no password, so they '
                'need a "set your password" email.'.format(invited)
            )
        self.message_user(request, message)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('starts_at', 'discipline', 'expert', 'client', 'status', 'price_naira')
    list_filter = ('status', 'discipline', 'starts_at')
    search_fields = ('client__email', 'expert__profile__full_name')
    readonly_fields = ('created_at', 'cancelled_at', 'cancelled_by')


@admin.register(AvailabilitySlot)
class AvailabilitySlotAdmin(admin.ModelAdmin):
    list_display = ('expert', 'day', 'start_minute', 'end_minute')
    list_filter = ('day',)


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    """Payouts, and the one place money is actually sent.

    A practitioner's withdrawal arrives here as `pending`. Nothing sends it
    automatically: a request over HTTP is a request, and money leaving the
    business on the strength of one nobody read is how a bug becomes a bank
    transfer. Somebody selects it and sends it.
    """

    list_display = ('expert', 'amount_naira', 'destination', 'status',
                    'paid_on', 'created_at')
    list_filter = ('status', 'paid_on')
    search_fields = ('expert__profile__user__email', 'reference')
    actions = ('send_payout',)

    @admin.display(description='into')
    def destination(self, obj):
        return str(obj.method) if obj.method_id else '--'

    @admin.action(description='Send this payout to the practitioner')
    def send_payout(self, request, queryset):
        """Pay the pending ones, through Bachs, one at a time.

        Each carries our own reference, which is also the idempotency key,
        so a second click cannot pay twice. A payout with no destination is
        skipped and named rather than guessed at.
        """
        from billing import bachs

        sent = 0
        for payout in queryset.filter(status='pending').select_related('method'):
            destination = getattr(payout.method, 'destination_id', '')
            if not destination:
                self.message_user(
                    request,
                    'No payout destination for {0} -- ask them to add their '
                    'account again.'.format(payout.expert),
                    level=messages.WARNING,
                )
                continue

            reference = payout.reference or 'po_{0}'.format(payout.pk)
            try:
                answer = bachs.create_payout(
                    destination_id=destination,
                    naira=payout.amount_naira,
                    reference_=reference,
                )
            except bachs.BachsError as failure:
                self.message_user(
                    request,
                    'Bachs refused the payout to {0}: {1}'.format(
                        payout.expert, failure,
                    ),
                    level=messages.ERROR,
                )
                continue

            payout.reference = (
                answer.get('payout_id') or answer.get('id') or reference
            )
            payout.status = 'sent'
            payout.paid_on = timezone.localdate()
            payout.save(update_fields=['reference', 'status', 'paid_on'])
            sent += 1

        if sent:
            self.message_user(request, '{0} payout(s) sent.'.format(sent))


admin.site.register(PayoutMethod)


@admin.register(SessionNote)
class SessionNoteAdmin(admin.ModelAdmin):
    """Clinical notes. Listed so they can be found, never previewed here."""

    list_display = ('session', 'updated_at')
    readonly_fields = ('session', 'body', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    """What practitioners have pointed clients at.

    Read-only on the author and the recipient: who recommended what to whom
    is a record of a session, not a field to correct afterwards.
    """

    list_display = ('item', 'expert', 'client', 'is_new', 'created_at')
    list_filter = ('created_at', 'read_at')
    search_fields = (
        'item__title', 'client__email', 'expert__profile__full_name', 'note',
    )
    autocomplete_fields = ('item', 'client', 'session')
    readonly_fields = ('created_at', 'read_at')

    @admin.display(boolean=True, description='unread')
    def is_new(self, obj):
        return obj.is_new
