"""/api/v1/auth/ -- register, sign in, sign out, me, KYC."""

import logging

from django.contrib.auth import get_user_model
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from api import jobs

from .emails import send_reset_code
from .models import KycResponse, PasswordResetCode, Profile
from .social import profile_for, verify_apple, verify_google
from .serializers import (
    KycSerializer,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileSerializer,
    RegisterSerializer,
    tokens_for,
)

logger = logging.getLogger(__name__)


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


class SocialLoginView(APIView):
    """Sign in with Google or Apple.

    One view, two providers: the only thing that differs is which token is
    verified and what it yields. Both answer with the same payload as
    LoginView, so the app routes identically afterwards and does not care
    which button was pressed.

    Throttled on the same 'auth' scope as password login: this issues
    sessions, so it is worth the same protection.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    #: Set by as_view(provider=...) in urls.py.
    provider = 'google'

    def post(self, request):
        if self.provider == 'google':
            token = request.data.get('id_token', '')
            if not token:
                raise ValidationError({'id_token': ['This field is required.']})
            email, full_name = verify_google(token)
        else:
            token = request.data.get('identity_token', '')
            if not token:
                raise ValidationError({'identity_token': ['This field is required.']})
            email, _sub = verify_apple(token)
            # Apple sends the name only on the first authorisation, so the
            # app forwards what it was given and it is used only if this
            # turns out to be a new account.
            full_name = (request.data.get('full_name') or '').strip()

        profile = profile_for(email, full_name=full_name)
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

class PasswordResetRequestView(APIView):
    """Start a reset: emails a six-digit code.

    Always answers the same way, whether or not the address has an account.
    Saying "no account with that email" turns this endpoint into a way to
    find out who is a member -- which, for a mental-health app, is a
    disclosure worth more than the small convenience of a clearer message.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    ANSWER = {
        'detail': 'If that address has an account, a code is on its way.',
    }

    def post(self, request):
        form = PasswordResetRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)

        email = form.validated_data['email'].strip().lower()
        user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
        if user is not None:
            if user.has_usable_password():
                code_row, code = PasswordResetCode.issue(user)
                jobs.run(send_reset_code, user.pk, code)
            else:
                # Signed up with Google or Apple: there is no password to
                # reset, and sending a code would start a journey that ends
                # in confusion. Say nothing different to the caller.
                logger.info('reset asked for a social-only account: %s', user.pk)

        return Response(self.ANSWER)


class PasswordResetConfirmView(APIView):
    """Finish a reset: the code, and the new password."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    REFUSED = {'detail': 'That code is wrong or has expired. Ask for a new one.'}

    def post(self, request):
        form = PasswordResetConfirmSerializer(data=request.data)
        form.is_valid(raise_exception=True)

        email = form.validated_data['email'].strip().lower()
        user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            return Response(self.REFUSED, status=status.HTTP_400_BAD_REQUEST)

        code_row = PasswordResetCode.objects.filter(user=user).first()
        if code_row is None or not code_row.is_usable:
            return Response(self.REFUSED, status=status.HTTP_400_BAD_REQUEST)

        if not code_row.matches(form.validated_data['code']):
            return Response(self.REFUSED, status=status.HTTP_400_BAD_REQUEST)

        code_row.spend()
        user.set_password(form.validated_data['password'])
        user.save(update_fields=['password'])

        # Signed in straight away: they have just proved they hold the email
        # and chosen a password, so asking them to type it again is friction
        # for its own sake.
        profile = getattr(user, 'profile', None) or Profile.objects.create(
            user=user, full_name=user.get_full_name(),
        )
        return Response(_session_payload(profile, request))
