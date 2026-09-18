import csv

from django.contrib import admin
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _

from .models import WaitlistSignup


@admin.register(WaitlistSignup)
class WaitlistSignupAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'source', 'wants_updates', 'referral_count', 'language', 'created_at')
    list_filter = ('source', 'wants_updates', 'language', 'created_at')
    search_fields = ('name', 'email', 'phone', 'referral_code')
    date_hierarchy = 'created_at'
    readonly_fields = ('referral_code', 'referred_by', 'created_at', 'place_in_line')
    actions = ('export_csv',)

    @admin.display(description=_('referrals'))
    def referral_count(self, obj):
        return obj.referrals.count()

    @admin.display(description=_('place in line'))
    def place_in_line(self, obj):
        return obj.position() if obj.pk else '-'

    @admin.action(description=_('Export selected signups to CSV'))
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="waitlist.csv"'
        writer = csv.writer(response)
        writer.writerow(['Name', 'Email', 'Phone', 'What brings them', 'Other reason',
                         'Experiences', 'Other experience', 'Heard about us', 'Wants updates',
                         'Referrals', 'Referred by', 'Language', 'Joined'])
        for row in queryset:
            writer.writerow([
                row.name, row.email, row.phone,
                '; '.join(str(l) for l in row.goal_labels()), row.goals_other,
                '; '.join(str(l) for l in row.interest_labels()), row.interests_other,
                row.get_source_display(), 'yes' if row.wants_updates else 'no',
                row.referrals.count(), row.referred_by.email if row.referred_by else '',
                row.language, row.created_at.strftime('%Y-%m-%d %H:%M'),
            ])
        return response
