"""What the booking flow and the practitioner screens exchange."""

from django.utils import timezone
from rest_framework import serializers

from api.media import signed_url

from .models import (
    Application,
    ApplicationDocument,
    AvailabilitySlot,
    Discipline,
    ExpertProfile,
    Payout,
    PayoutMethod,
    Session,
    SessionLanguage,
    SessionNote,
)


class ExpertSerializer(serializers.ModelSerializer):
    """An expert as the client sees them -- the matched card and the profile."""

    name = serializers.CharField(source='display_name', read_only=True)
    avatar_url = serializers.SerializerMethodField()
    rating = serializers.FloatField(source='rating_average', read_only=True)

    class Meta:
        model = ExpertProfile
        fields = (
            'id', 'name', 'headline', 'bio', 'quote', 'disciplines',
            'languages', 'interests', 'years_experience', 'rating',
            'rating_count', 'avatar_url', 'is_accepting',
        )

    def get_avatar_url(self, obj):
        avatar = obj.profile.avatar
        return signed_url(avatar.name, self.context.get('request')) if avatar else ''


class AvailabilitySlotSerializer(serializers.ModelSerializer):
    """Minutes from midnight, as the app's AvailabilitySlot models them."""

    class Meta:
        model = AvailabilitySlot
        fields = ('id', 'day', 'start_minute', 'end_minute')

    def validate(self, attrs):
        start = attrs.get('start_minute', getattr(self.instance, 'start_minute', 0))
        end = attrs.get('end_minute', getattr(self.instance, 'end_minute', 0))
        if end <= start:
            raise serializers.ValidationError(
                {'end_minute': 'A slot has to end after it starts.'},
            )
        return attrs


class SessionSerializer(serializers.ModelSerializer):
    """A booking, read from either side."""

    expert_name = serializers.CharField(source='expert.display_name', read_only=True)
    client_name = serializers.SerializerMethodField()
    room_id = serializers.CharField(read_only=True)

    class Meta:
        model = Session
        fields = (
            'id', 'discipline', 'expert', 'expert_name', 'client_name',
            'starts_at', 'ends_at', 'mode', 'plan', 'price_naira', 'status',
            'rating', 'feedback', 'room_id', 'created_at',
        )
        read_only_fields = (
            'id', 'expert', 'status', 'price_naira', 'rating', 'feedback',
            'room_id', 'created_at',
        )

    def get_client_name(self, obj):
        profile = getattr(obj.client, 'profile', None)
        return getattr(profile, 'full_name', '') or obj.client.get_username()


class BookingSerializer(serializers.Serializer):
    """What the calendar sends when someone picks a day and a time.

    The slot is the start time the calendar printed ("5:00pm"), sent as
    minutes from midnight so the server is not parsing display text.
    """

    discipline = serializers.ChoiceField(choices=['therapy', 'yoga', 'pilates'])
    day = serializers.DateField()
    start_minute = serializers.IntegerField(min_value=0, max_value=1439)
    mode = serializers.ChoiceField(choices=['phone', 'video'], default='video')
    plan = serializers.ChoiceField(choices=['single', 'monthly'], default='single')
    expert = serializers.PrimaryKeyRelatedField(
        queryset=ExpertProfile.objects.filter(is_published=True),
        required=False, allow_null=True,
    )

    def validate_day(self, value):
        if value < timezone.localdate():
            raise serializers.ValidationError('That day has already passed.')
        return value


class RatingSerializer(serializers.Serializer):
    """The stars and the note the booking flow ends on."""

    rating = serializers.IntegerField(min_value=1, max_value=5)
    feedback = serializers.CharField(required=False, allow_blank=True, default='')


class ApplicationDocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = ApplicationDocument
        fields = ('id', 'kind', 'file_url', 'uploaded_at')

    def get_file_url(self, obj):
        # Only ever handed to staff; the applicant's own view does not include
        # a link back to their passport scan.
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is not None and user.is_staff:
            return signed_url(obj.file.name, request)
        return ''


class ApplicationSerializer(serializers.ModelSerializer):
    documents = ApplicationDocumentSerializer(many=True, read_only=True)

    # Lists, and sent as lists -- but a multipart form (which is how the
    # app sends this, because of the two files) repeats the key instead of
    # nesting JSON, so both shapes are accepted. See `to_internal_value`.
    disciplines = serializers.ListField(
        child=serializers.ChoiceField(choices=Discipline.choices),
        allow_empty=False,
    )
    languages = serializers.ListField(
        child=serializers.ChoiceField(choices=SessionLanguage.choices),
        allow_empty=False,
    )
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Application
        fields = (
            'id', 'full_name', 'disciplines', 'languages', 'experience_years',
            'bio', 'portfolio_url', 'licence_number', 'insurance_provider',
            'status', 'status_display', 'documents', 'created_at',
        )
        read_only_fields = ('id', 'status', 'status_display', 'documents', 'created_at')

    def to_internal_value(self, data):
        """Accept the lists however the client could send them.

        A JSON client sends `["therapy", "yoga"]`. A multipart one -- which
        the app has to be, since the certificate and the ID travel with the
        form -- can only repeat the key, so `getlist` is what has the
        values. Without this, a two-discipline application arrives as the
        string "yoga" and the first choice is silently dropped.
        """
        if hasattr(data, 'getlist'):
            data = data.copy()
            for key in ('disciplines', 'languages'):
                values = data.getlist(key)
                if len(values) == 1 and ',' in values[0]:
                    # A single comma-joined field, which is what an HTML
                    # form with one hidden input sends.
                    values = [v.strip() for v in values[0].split(',') if v.strip()]
                if values:
                    data.setlist(key, values)
        return super().to_internal_value(data)


class SessionNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionNote
        fields = ('body', 'created_at', 'updated_at')
        read_only_fields = ('created_at', 'updated_at')


class PayoutMethodSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()

    # Sent once, to Bachs, and never stored: the view resolves the account,
    # makes a payout destination of it, and keeps the last four digits.
    account_number = serializers.CharField(write_only=True, required=False)
    bank_code = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = PayoutMethod
        fields = (
            'id', 'kind', 'bank_name', 'account_name', 'last4', 'label',
            'is_default', 'account_number', 'bank_code',
        )
        read_only_fields = ('id', 'label', 'last4', 'account_name')

    def validate(self, attrs):
        if attrs.get('kind', 'bank') == 'bank':
            missing = [
                name for name in ('account_number', 'bank_code')
                if not attrs.get(name)
            ]
            if missing:
                raise serializers.ValidationError(
                    {name: 'This is needed to pay into a bank account.'
                     for name in missing},
                )
        return attrs

    def get_label(self, obj):
        return str(obj)


class PayoutSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Payout
        fields = (
            'id', 'label', 'amount_naira', 'status', 'status_display',
            'paid_on', 'created_at',
        )
        read_only_fields = fields


class PayoutRequestSerializer(serializers.Serializer):
    """Asking to be paid. The amount is all the practitioner chooses.

    Where it goes is not theirs to pass: it is the default payout method on
    file, which was verified with the bank when it was added. Taking a
    destination from the request would make a withdrawal re-routable by
    whoever sent it.
    """

    amount_naira = serializers.IntegerField(min_value=1)


class EarningsSerializer(serializers.Serializer):
    """The Earnings screen, counted rather than stored."""

    available = serializers.IntegerField()
    this_week = serializers.IntegerField()
    this_week_sessions = serializers.IntegerField()
    total_earned = serializers.IntegerField()
    total_sessions = serializers.IntegerField()
    next_payout = serializers.DateField()
    payout_method = serializers.CharField(allow_blank=True)
