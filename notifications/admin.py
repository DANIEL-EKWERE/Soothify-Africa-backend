from django.contrib import admin

from .models import Notification, Preferences, PushDevice


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'kind', 'summary', 'unread', 'created_at')
    list_filter = ('kind', 'unread', 'created_at')
    search_fields = ('user__email', 'title', 'text', 'body')

    @admin.display(description='says')
    def summary(self, obj):
        return obj.title or '{0}{1}'.format(obj.actor, obj.text)


@admin.register(Preferences)
class PreferencesAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'notify_balance', 'notify_meditation', 'notify_mood',
        'notify_offers', 'updated_at',
    )
    search_fields = ('user__email',)


@admin.register(PushDevice)
class PushDeviceAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'is_active', 'last_seen_at')
    list_filter = ('platform', 'is_active')
    search_fields = ('user__email', 'token')
