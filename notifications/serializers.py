"""The feed, the four switches, and registering a phone for push."""

from rest_framework import serializers

from .models import Notification, Preferences, PushDevice


class NotificationSerializer(serializers.ModelSerializer):
    # Stored comma-separated (see the model); served as the list the app reads.
    filters = serializers.ListField(
        source='filter_keys', child=serializers.CharField(), read_only=True,
    )

    class Meta:
        model = Notification
        fields = (
            'id', 'kind', 'actor', 'text', 'title', 'body', 'emphasis',
            'action_label', 'target', 'filters', 'unread', 'created_at',
        )
        read_only_fields = fields


class PreferencesSerializer(serializers.ModelSerializer):
    """Keys match NotificationPreference.key in the app, so the four toggles
    map straight onto these fields."""

    class Meta:
        model = Preferences
        fields = ('notify_balance', 'notify_meditation', 'notify_mood', 'notify_offers')


class PushDeviceSerializer(serializers.ModelSerializer):
    # Plain, so re-registering a phone is handled by the view rather than
    # rejected as a duplicate: reinstalls and handed-on phones are normal.
    token = serializers.CharField(max_length=255)

    class Meta:
        model = PushDevice
        fields = ('token', 'platform')
