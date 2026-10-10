"""/api/v1/auth/ -- register, sign in, sign out, me, KYC."""

from django.contrib.auth import get_user_model
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import KycResponse, Profile
from .serializers import (
    KycSerializer,
    LoginSerializer,
    ProfileSerializer,
    RegisterSerializer,
    tokens_for,
)


def _session_payload(profile, request):
    """What the app gets back from register and login: tokens plus the user.

    Both in one response so the app can route straight after signing in -- it
    needs the role to pick a shell and `kyc_complete` to decide whether to
    show the questionnaire, and a second round trip for that is a spinner the
    user would sit through.
    """
    return {
        'tokens': tokens_for(profile.user),
        'profile': ProfileSerializer(profile, context={'request': request}).data,
    }


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        return Response(
            _session_payload(profile, request), status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        # An account made before profiles existed, or by createsuperuser.
        profile = getattr(user, 'profile', None) or Profile.objects.create(
            user=user, full_name=user.get_full_name(),
        )
        return Response(_session_payload(profile, request))


class LogoutView(APIView):
    """Give up a refresh token.

    Without token blacklisting installed this cannot actually revoke it, so it
    answers 205 either way and the app forgets its copy. Real revocation is a
    matter of adding SimpleJWT's blacklist app, which needs a migration.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get('refresh', '')
        if token:
            try:
                RefreshToken(token).blacklist()
            except (TokenError, AttributeError):
                # Already expired, malformed, or blacklisting is not installed.
                pass
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MeView(generics.RetrieveUpdateAPIView):
    """The signed-in person's own profile."""

    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        profile, _ = Profile.objects.get_or_create(
            user=self.request.user,
            defaults={'full_name': self.request.user.get_full_name()},
        )
        return profile


class KycView(APIView):
    """The onboarding questionnaire.

    GET returns what has been answered so far, PATCH merges in more answers,
    and POST /complete marks it finished -- which is what the app's splash
    screen reads to decide where to send someone.
    """

    permission_classes = [IsAuthenticated]

    def get_kyc(self):
        kyc, _ = KycResponse.objects.get_or_create(user=self.request.user)
        return kyc

    def get(self, request):
        return Response(KycSerializer(self.get_kyc()).data)

    def patch(self, request):
        kyc = self.get_kyc()
        serializer = KycSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # Merged, not replaced: the app saves after each question, and a later
        # step must not wipe an earlier answer.
        answers = dict(kyc.answers or {})
        answers.update(serializer.validated_data.get('answers', {}))
        kyc.answers = answers
        kyc.save(update_fields=['answers', 'updated_at'])
        return Response(KycSerializer(kyc).data)

    def put(self, request):
        """Replace every answer. Used when the app re-sends the whole map."""
        kyc = self.get_kyc()
        serializer = KycSerializer(kyc, data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class KycCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        kyc, _ = KycResponse.objects.get_or_create(user=request.user)
        kyc.mark_complete()
        return Response(KycSerializer(kyc).data)


class EmailAvailableView(APIView):
    """Lets the sign-up form say "already taken" before the password step.

    Rate-limited with the other auth endpoints: it answers a question about
    who has an account, so it should not be cheap to ask in bulk.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def get(self, request):
        email = request.query_params.get('email', '').strip().lower()
        taken = bool(email) and get_user_model().objects.filter(
            email__iexact=email,
        ).exists()
        return Response({'email': email, 'available': not taken})
