"""/api/v1/spaces/ -- the studio list, and the Passport waitlist."""

from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import PassportWaitlistEntry, WellnessSpace
from .serializers import PassportWaitlistSerializer, WellnessSpaceSerializer


class SpaceListView(generics.ListAPIView):
    """?category=pilates&q=lekki&lat=&lng=

    Open to anyone: the design puts Spaces behind no sign-in, and a studio's
    address is public information its owner wants seen.
    """

    serializer_class = WellnessSpaceSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        spaces = WellnessSpace.objects.filter(is_active=True)
        params = self.request.query_params

        category = params.get('category')
        # 'all' is the chip that selects everything, so it is not a filter.
        if category and category != 'all':
            spaces = spaces.filter(category=category)

        term = (params.get('q') or '').strip()
        if term:
            from django.db.models import Q
            spaces = spaces.filter(
                Q(name__icontains=term)
                | Q(area__icontains=term)
                | Q(city__icontains=term)
                | Q(tags__icontains=term),
            )
        return spaces

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['origin'] = _origin(self.request)
        return context


class SpaceDetailView(generics.RetrieveAPIView):
    serializer_class = WellnessSpaceSerializer
    permission_classes = [AllowAny]
    queryset = WellnessSpace.objects.filter(is_active=True)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['origin'] = _origin(self.request)
        return context


class PassportWaitlistView(APIView):
    """"Reserve my place on the waitlist".

    Asking twice is not an error -- someone who taps it again after a reinstall
    should be told they are on the list, not shown a validation failure.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request):
        serializer = PassportWaitlistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email'].strip().lower()
        user = request.user if request.user.is_authenticated else None
        entry, created = PassportWaitlistEntry.objects.get_or_create(
            email=email,
            defaults={
                'user': user,
                'city': serializer.validated_data.get('city', ''),
                'source': serializer.validated_data.get('source', ''),
            },
        )
        # Someone who signed up as a guest and later made an account gets the
        # two joined up, so the launch email can address them by name.
        if not created and user is not None and entry.user_id is None:
            entry.user = user
            entry.save(update_fields=['user'])

        return Response(
            {'email': entry.email, 'already_on_list': not created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _origin(request):
    """The caller's position, when the phone sent one."""
    try:
        return (
            float(request.query_params['lat']),
            float(request.query_params['lng']),
        )
    except (KeyError, TypeError, ValueError):
        return None
