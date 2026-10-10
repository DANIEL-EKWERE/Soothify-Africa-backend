"""Reviewing applications, and keeping an eye on bookings."""

from django.contrib import admin
from django.utils import timezone

from accounts.models import Role

from .models import (
    Application,
    ApplicationDocument,
    AvailabilitySlot,
    ExpertProfile,
    Payout,
    PayoutMethod,
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
    list_display = ('full_name', 'field', 'status', 'created_at', 'reviewed_at')
    list_filter = ('status', 'field', 'created_at')
    search_fields = ('full_name', 'user__email')
    readonly_fields = ('user', 'full_name', 'field', 'languages', 'experience_years',
                       'bio', 'created_at', 'reviewed_by', 'reviewed_at')
    inlines = [ApplicationDocumentInline]
    actions = ['approve']

    @admin.action(description='Approve: create the expert and switch their role')
    def approve(self, request, queryset):
        made = 0
        for application in queryset.exclude(status='approved'):
            profile = getattr(application.user, 'profile', None)
            if profile is None:
                continue
            profile.role = Role.PRACTITIONER
            profile.save(update_fields=['role'])
            ExpertProfile.objects.get_or_create(
                profile=profile,
                defaults={
                    'disciplines': [application.field],
                    'languages': application.languages,
                    'years_experience': application.experience_years,
                    'bio': application.bio,
                },
            )
            application.status = 'approved'
            application.reviewed_by = request.user
            application.reviewed_at = timezone.now()
            application.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
            made += 1
        self.message_user(request, '{0} approved.'.format(made))


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
    list_display = ('expert', 'amount_naira', 'status', 'paid_on', 'created_at')
    list_filter = ('status', 'paid_on')


admin.site.register(PayoutMethod)


@admin.register(SessionNote)
class SessionNoteAdmin(admin.ModelAdmin):
    """Clinical notes. Listed so they can be found, never previewed here."""

    list_display = ('session', 'updated_at')
    readonly_fields = ('session', 'body', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False
