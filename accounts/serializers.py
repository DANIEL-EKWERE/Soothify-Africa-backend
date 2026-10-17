"""What the app sends and receives at /api/v1/auth/.

Field names are snake_case because that is what the Flutter models parse, and
the shapes follow the app's own validation: sign-up collects one full name,
an email and a password of at least 8 characters with an upper and a lower
case letter (SignupController.PasswordRule).
"""

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import KycResponse, Language, Profile, Role


def tokens_for(user):
    """The pair the app stores after signing in."""
    refresh = RefreshToken.for_user(user)
    return {'refresh': str(refresh), 'access': str(refresh.access_token)}


class ProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    kyc_complete = serializers.SerializerMethodField()

    # Whether this account has a password at all. False for one opened with
    # Google or Apple, which changes what "confirm it is you" can ask for --
    # see DeleteAccountView.
    has_password = serializers.SerializerMethodField()

    # Two fields for one photo: `avatar` takes the upload, `avatar_url`
    # gives back a link that can actually be shown. The stored path is
    # private, so what is read back is a signed URL and never the path.
    avatar = serializers.ImageField(write_only=True, required=False)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = Profile
        fields = (
            'id', 'email', 'full_name', 'role', 'language', 'community_handle',
            'mood_figure', 'avatar', 'avatar_url', 'kyc_complete',
            'has_password', 'created_at',
        )
        read_only_fields = ('id', 'role', 'created_at')

    def get_has_password(self, obj):
        return obj.user.has_usable_password()

    def get_kyc_complete(self, obj):
        kyc = getattr(obj.user, 'kyc', None)
        return bool(kyc and kyc.is_complete)

    def get_avatar_url(self, obj):
        if not obj.avatar:
            return ''
        from api.media import signed_url  # local: avoids an import cycle
        return signed_url(obj.avatar.name, self.context.get('request'))


class RegisterSerializer(serializers.Serializer):
    """Sign-up. Mirrors the app's own form, field for field."""

    full_name = serializers.CharField(max_length=120)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    role = serializers.ChoiceField(choices=Role.choices, default=Role.USER)
    language = serializers.ChoiceField(choices=Language.choices, default=Language.ENGLISH)

    def validate_email(self, value):
        email = value.strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            # Said plainly: the app shows this under the field, and a vague
            # error here just makes someone try the same address again.
            raise serializers.ValidationError('An account already uses this email.')
        return email

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(list(error.messages))
        return value

    @transaction.atomic
    def create(self, validated_data):
        return Profile.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            full_name=validated_data['full_name'],
            role=validated_data['role'],
            language=validated_data['language'],
        )


class LoginSerializer(serializers.Serializer):
    """Sign-in by email, which is what the app's form asks for."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs['email'].strip().lower()
        user = get_user_model().objects.filter(email__iexact=email).first()
        # authenticate() wants the username; ours is derived from the email.
        user = authenticate(
            request=self.context.get('request'),
            username=user.get_username() if user else email,
            password=attrs['password'],
        )
        if user is None:
            # One message for a wrong password and for an unknown address, so
            # this endpoint cannot be used to find out who has an account.
            raise serializers.ValidationError(
                {'detail': 'You entered an incorrect email or password.'},
            )
        if not user.is_active:
            raise serializers.ValidationError({'detail': 'This account is disabled.'})
        attrs['user'] = user
        return attrs


class KycSerializer(serializers.ModelSerializer):
    """The questionnaire, in the shape the app's KycRepository stores.

    `answers` is {question_id: [value, ...]}. Single-choice questions send a
    one-item list, so the client never has to branch on the type it gets back.
    """

    class Meta:
        model = KycResponse
        fields = ('answers', 'is_complete', 'completed_at', 'updated_at')
        read_only_fields = ('is_complete', 'completed_at', 'updated_at')

    def validate_answers(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('Expected an object keyed by question id.')

        cleaned = {}
        for question, chosen in value.items():
            if isinstance(chosen, str):
                chosen = [chosen]
            if not isinstance(chosen, (list, tuple)):
                raise serializers.ValidationError(
                    {question: 'Expected a list of chosen option values.'},
                )
            if not all(isinstance(option, str) for option in chosen):
                raise serializers.ValidationError(
                    {question: 'Option values must be strings.'},
                )
            # De-duplicate but keep the order the user tapped them in: the app
            # sends a Set, whose iteration order is its insertion order.
            seen = []
            for option in chosen:
                if option not in seen:
                    seen.append(option)
            cleaned[str(question)] = seen
        return cleaned

class PasswordResetRequestSerializer(serializers.Serializer):
    """"I have forgotten my password." Only an address is needed."""

    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """The code from the email, and what the password should become."""

    email = serializers.EmailField()
    code = serializers.CharField(max_length=6, min_length=6)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(list(error.messages))
        return value

    def validate_code(self, value):
        code = value.strip()
        if not code.isdigit():
            raise serializers.ValidationError('The code is six digits.')
        return code
