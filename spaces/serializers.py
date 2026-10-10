"""What "Spaces Around Me" and the Passport sheet exchange."""

from math import asin, cos, radians, sin, sqrt

from rest_framework import serializers

from api.media import signed_url

from .models import PassportWaitlistEntry, WellnessSpace


class WellnessSpaceSerializer(serializers.ModelSerializer):
    photo_url = serializers.SerializerMethodField()
    distance_km = serializers.SerializerMethodField()

    class Meta:
        model = WellnessSpace
        fields = (
            'id', 'name', 'area', 'city', 'category', 'tags', 'about',
            'photo_url', 'instagram', 'phone', 'latitude', 'longitude',
            'distance_km',
        )

    def get_photo_url(self, obj):
        return signed_url(obj.photo.name, self.context.get('request')) if obj.photo else ''

    def get_distance_km(self, obj):
        """How far from the caller, when they said where they are.

        Null rather than zero when the phone did not share a location: the
        card can then say nothing instead of claiming the studio is next door.
        """
        origin = self.context.get('origin')
        if not origin or obj.latitude is None or obj.longitude is None:
            return None
        return round(_haversine(origin[0], origin[1], obj.latitude, obj.longitude), 1)


class PassportWaitlistSerializer(serializers.ModelSerializer):
    # Declared plainly so the model's uniqueness does not become a validation
    # error: asking to be on the list twice is not a mistake to report back,
    # and the view answers "you are already on it". See PassportWaitlistView.
    email = serializers.EmailField()

    class Meta:
        model = PassportWaitlistEntry
        fields = ('email', 'city', 'source')


def _haversine(lat1, lon1, lat2, lon2):
    """Kilometres between two points, over a sphere.

    Good to a fraction of a percent at city distances, which is far better
    than a card reading "2.4 km away" needs, and it costs no dependency.
    """
    earth_km = 6371.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = (
        sin(d_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    )
    return 2 * earth_km * asin(sqrt(a))
