from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.urls import reverse

from .models import Broadcast, Notification, Preferences, PushDevice


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


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    """The record of what the team has sent out.

    Read-only once sent, and it never sends from here: the dashboard's own
    page shows the audience and the count before anything goes, and a
    "save" button that quietly mails four thousand people is the wrong
    shape of control for that.
    """

    list_display = (
        'title', 'channel', 'audience', 'state', 'delivered', 'failed',
        'created_by', 'sent_at',
    )
    list_filter = ('state', 'channel', 'audience', 'is_marketing')
    search_fields = ('title', 'body', 'created_by__email')
    filter_horizontal = ('people',)
    readonly_fields = (
        'state', 'recipients', 'delivered', 'failed', 'detail',
        'created_by', 'created_at', 'sent_at',
    )

    def has_change_permission(self, request, obj=None):
        # A message people have already read is a record, not a draft.
        if obj is not None and obj.state != Broadcast.State.DRAFT:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        self.message_user(
            request,
            'Saved as a draft. Open it in the dashboard to check who it '
            'reaches and send it.',
            messages.INFO,
        )


#: Where a selection made in the admin waits for the dashboard to pick it
#: up. The session, not the URL: a hundred ticked accounts is a hundred
#: addresses, and that is past what a browser will put in a query string.
PICKED = 'broadcast_addresses'


@admin.action(description='Write a message to the selected people')
def write_to_selected(modeladmin, request, queryset):
    """Start a broadcast addressed to whoever is ticked in the list.

    The admin is where staff already are when they have just filtered the
    account list down to the people they mean; this carries that selection
    to the dashboard rather than making them retype the addresses. Nothing
    is created here -- abandoning the page leaves no empty draft behind.
    """
    addresses = [e for e in queryset.values_list('email', flat=True) if e]
    if not addresses:
        modeladmin.message_user(
            request, 'None of those accounts has an email address.',
            messages.WARNING,
        )
        return None

    request.session[PICKED] = addresses
    return redirect(reverse('dashboard:message-new'))


# Hung off whichever admin is registered for accounts, so it appears in the
# list staff already use rather than needing a second one of our own.
_user_admin = admin.site._registry.get(get_user_model())
if _user_admin is not None:
    _user_admin.actions = list(_user_admin.actions or []) + [write_to_selected]
