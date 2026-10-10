"""/api/v1/notifications/ -- the feed, the switches, and push registration."""

from django.utils import timezone
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification, Preferences, PushDevice
from .serializers import (
    NotificationSerializer,
    PreferencesSerializer,
    PushDeviceSerializer,
)


class FeedView(generics.ListAPIView):
    """Newest first, optionally narrowed by a chip: ?filter=sessions."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        feed = Notification.objects.filter(user=self.request.user)
        chip = self.request.query_params.get('filter')
        # 'all' selects everything, so it filters nothing.
        if chip and chip != 'all':
            feed = feed.filter(filters__contains=',{0},'.format(chip))
        return feed


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_read(request, pk=None):
    """Mark one as read, or the whole feed when no id is given."""
    feed = Notification.objects.filter(user=request.user, unread=True)
    if pk is not None:
        feed = feed.filter(pk=pk)
    return Response({'marked': feed.update(unread=False)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def unread_count(request):
    """For the dot on the bell."""
    return Response({
        'unread': Notification.objects.filter(user=request.user, unread=True).count(),
    })


class PreferencesView(generics.RetrieveUpdateAPIView):
    """The four switches. All off until someone turns them on."""

    serializer_class = PreferencesSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        preferences, _ = Preferences.objects.get_or_create(user=self.request.user)
        return preferences


class PushDeviceView(APIView):
    """Register this install for push, or stop pushing to it.

    Re-registering an existing token moves it to the current account: phones
    get handed over and reinstalled, and a token left pointing at the previous
    owner would send them someone else's reminders.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PushDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device, created = PushDevice.objects.update_or_create(
            token=serializer.validated_data['token'],
            defaults={
                'user': request.user,
                'platform': serializer.validated_data['platform'],
                'is_active': True,
                'last_seen_at': timezone.now(),
            },
        )
        return Response(
            PushDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request):
        token = request.data.get('token', '')
        PushDevice.objects.filter(user=request.user, token=token).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
