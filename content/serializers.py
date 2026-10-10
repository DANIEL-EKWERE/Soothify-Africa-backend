"""JSON in the shape MediaItem.fromJson and Category.fromJson already parse.

Those two factories fix the field names: `media_type`, `duration_seconds`,
`cover_url`, `media_url`, `category`, `practitioner_name`, `is_free`. Nothing
here may drift from them without a matching change in the app.
"""

from rest_framework import serializers

from api.media import signed_url

from .models import Category, MediaItem, MediaRating


class CategorySerializer(serializers.ModelSerializer):
    cover_url = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ('id', 'name', 'description', 'cover_url')

    def get_cover_url(self, obj):
        return signed_url(obj.cover.name, self.context.get('request')) if obj.cover else ''


class MediaItemSerializer(serializers.ModelSerializer):
    """One card, and one player.

    `media_url` is empty unless the caller may actually play the thing: paid
    content needs a subscription, and handing out a playable link and hiding
    the button in the app would be a paywall in name only.
    """

    cover_url = serializers.SerializerMethodField()
    media_url = serializers.SerializerMethodField()
    practitioner_name = serializers.CharField(read_only=True)
    rating = serializers.FloatField(source='rating_average', read_only=True)
    requires_subscription = serializers.SerializerMethodField()

    class Meta:
        model = MediaItem
        fields = (
            'id', 'title', 'subtitle', 'tag', 'description', 'media_type',
            'duration_seconds', 'rating', 'rating_count', 'cover_url',
            'media_url', 'category', 'practitioner_name', 'is_free',
            'requires_subscription', 'published_at',
        )

    def get_cover_url(self, obj):
        return signed_url(obj.cover.name, self.context.get('request')) if obj.cover else ''

    def get_media_url(self, obj):
        if not self._may_play(obj):
            return ''
        if obj.media_file:
            return signed_url(obj.media_file.name, self.context.get('request'))
        return obj.external_url

    def get_requires_subscription(self, obj):
        return not self._may_play(obj)

    def _may_play(self, obj):
        if obj.is_free:
            return True
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        # A membership on trial counts: the whole point of the seven days is
        # full access. See billing.Membership.is_active.
        membership = getattr(user, 'membership', None)
        return bool(membership and membership.is_active)


class RatingSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaRating
        fields = ('score',)

    def validate_score(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError('A score runs from 1 to 5.')
        return value


class PlaybackSerializer(serializers.Serializer):
    """What the player reports when someone listens.

    Sent when playback stops, not when it starts, so `seconds_played` is real.
    """

    seconds_played = serializers.IntegerField(min_value=0, default=0)
    completed = serializers.BooleanField(default=False)

    def validate_seconds_played(self, value):
        # A minute of audio cannot yield an hour of listening. Guards the
        # Profile stats against a client that miscounts or lies.
        item = self.context.get('item')
        if item is not None and item.duration_seconds:
            return min(value, item.duration_seconds)
        return value
