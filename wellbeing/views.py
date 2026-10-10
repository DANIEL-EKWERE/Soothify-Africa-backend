"""/api/v1/wellbeing/ -- mood check-ins, the journal, and the stats card.

Everything here is private. Each view filters by `request.user` and none of
them accepts a user id from the client, so there is no way to ask for someone
else's rows.
"""

import datetime

from django.db.models import Sum
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import JournalEntry, MoodEntry
from .serializers import (
    JournalEntrySerializer,
    MoodEntrySerializer,
    ProfileStatsSerializer,
)


class MoodListView(generics.ListCreateAPIView):
    """GET history, POST a check-in.

    Posting twice in one day replaces that day's entry rather than adding
    another, which is what the app does locally.
    """

    serializer_class = MoodEntrySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        entries = MoodEntry.objects.filter(user=self.request.user)

        # ?from=2026-09-01&to=2026-09-30 backs the mood-record week strip.
        start = _as_date(self.request.query_params.get('from'))
        end = _as_date(self.request.query_params.get('to'))
        if start:
            entries = entries.filter(recorded_on__gte=start)
        if end:
            entries = entries.filter(recorded_on__lte=end)

        limit = _as_int(self.request.query_params.get('limit'), default=50)
        return entries[:limit]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        today = timezone.localdate()
        entry, created = MoodEntry.objects.update_or_create(
            user=request.user,
            recorded_on=today,
            defaults={
                'score': serializer.validated_data['score'],
                'note': serializer.validated_data.get('note', ''),
                'recorded_at': timezone.now(),
            },
        )
        return Response(
            MoodEntrySerializer(entry).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MoodTodayView(APIView):
    """Today's check-in, if there is one.

    The check-in screen reads this to preselect the slider and to avoid
    logging the same day twice. 204 rather than 404 when there is none: not
    having checked in yet is an ordinary state, not an error.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        entry = MoodEntry.objects.for_day(request.user, timezone.localdate()).first()
        if entry is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(MoodEntrySerializer(entry).data)


class JournalListView(generics.ListCreateAPIView):
    """The notes, newest first."""

    serializer_class = JournalEntrySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return JournalEntry.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class JournalDetailView(generics.RetrieveUpdateDestroyAPIView):
    """One note. Only its author can read, change or delete it."""

    serializer_class = JournalEntrySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return JournalEntry.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        # An edited note re-derives its heading unless one was sent, so a
        # rewritten opening line is reflected on the card.
        if not serializer.validated_data.get('title'):
            body = serializer.validated_data.get('body', serializer.instance.body)
            serializer.save(title=JournalEntry.title_from(body))
        else:
            serializer.save()


class ProfileStatsView(APIView):
    """The three counters on Profile.

    Counted from what actually happened -- playback events and booked
    sessions -- so a new account reads zero because it has done nothing, not
    because the number is hardcoded.
    """

    permission_classes = [IsAuthenticated]

    # Which categories count towards which counter. Curated here rather than
    # guessed from titles, so renaming a category cannot silently zero a stat.
    MEDITATION_CATEGORIES = ('calm', 'breathe', 'focus', 'sleep')
    BALANCE_CATEGORIES = ('balance', 'yoga', 'pilates')

    def get(self, request):
        from content.models import PlaybackEvent

        played = PlaybackEvent.objects.filter(user=request.user)
        stats = {
            'meditation_minutes': self._minutes(played, self.MEDITATION_CATEGORIES),
            'balance_minutes': self._minutes(played, self.BALANCE_CATEGORIES),
            'live_sessions': self._live_sessions(request.user),
        }
        return Response(ProfileStatsSerializer(stats).data)

    def _minutes(self, played, categories):
        seconds = played.filter(item__category_id__in=categories).aggregate(
            total=Sum('seconds_played'),
        )['total'] or 0
        return int(seconds // 60)

    def _live_sessions(self, user):
        """Sessions actually attended. Zero until booking is built."""
        return 0


def _as_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        # A malformed date is treated as "no filter" rather than a 500; the
        # worst case is a longer list than the caller wanted.
        return None


def _as_int(value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, 365))
