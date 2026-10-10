from django.contrib import admin
from django.utils import timezone

from .models import Enquiry


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ('company', 'contact_name', 'email', 'employees', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    list_editable = ('status',)
    search_fields = ('company', 'contact_name', 'email', 'phone')
    readonly_fields = ('user', 'created_at')
    actions = ['mark_contacted']

    @admin.action(description='Mark as contacted')
    def mark_contacted(self, request, queryset):
        updated = queryset.update(status='contacted', handled_at=timezone.now())
        self.message_user(request, '{0} marked as contacted.'.format(updated))
