"""/api/v1/experts/ and /api/v1/sessions/.

Two audiences in one app. A client browses experts, gets matched, books a
slot and later joins a call. A practitioner sets their availability, sees
what is booked, writes notes and watches their earnings. The permission on
each view says which of the two it is for.
"""

import datetime

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api import jobs
from api.permissions import IsPractitioner
from recordings import models as recordings_models
from recordings import tasks as recording_tasks

from . import calls, matching
from .models import (
    Application,
    ApplicationDocument,
    AvailabilitySlot,
    ExpertProfile,
    Payout,
    PayoutMethod,
    Session,
    SessionNote,
    earnings_for,
)
from .serializers import (
    ApplicationSerializer,
    AvailabilitySlotSerializer,
    BookingSerializer,
    EarningsSerializer,
    ExpertSerializer,
    PayoutMethodSerializer,
    PayoutSerializer,
    RatingSerializer,
    SessionNoteSerializer,
    SessionSerializer,
)

# How long an unpaid booking holds its slot. Long enough to finish paying,
# short enough that an abandoned checkout does not block that hour all day.
HOLD_MINUTES = 20

# What a session costs, in naira, from the app's SessionOffering.
PRICES = {
    'therapy': {'single': 25000, 'monthly': 75000},
    'yoga': {'single': 10000, 'monthly': 36000},
    'pilates': {'single': 10000, 'monthly': 36000},
}
SESSION_MINUTES = 50


class ExpertListView(generics.ListAPIView):
    """Who can be booked. ?discipline=therapy"""

    serializer_class = ExpertSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        experts = ExpertProfile.objects.filter(
            is_published=True,
        ).select_related('profile')
        discipline = self.request.query_params.get('discipline')
        if discipline:
            # JSON contains is unsupported on SQLite, and the list is short.
            ids = [e.pk for e in experts if e.practises(discipline)]
            experts = experts.filter(pk__in=ids)
        return experts


class ExpertDetailView(generics.RetrieveAPIView):
    serializer_class = ExpertSerializer
    permission_classes = [AllowAny]
    queryset = ExpertProfile.objects.filter(is_published=True).select_related('profile')


class ExpertSlotsView(APIView):
    """When this expert is free. ?from=2026-10-01&to=2026-10-31

    Slots already booked are left out rather than marked, so the calendar
    cannot offer an hour that someone else has taken.
    """

    permission_classes = [AllowAny]

    def get(self, request, pk):
        expert = get_object_or_404(ExpertProfile, pk=pk, is_published=True)
        start = _as_date(request.query_params.get('from')) or timezone.localdate()
        end = _as_date(request.query_params.get('to')) or (
            start + datetime.timedelta(days=60)
        )

        slots = AvailabilitySlot.objects.filter(
            expert=expert, day__gte=start, day__lte=end,
        )
        taken = {
            (session.starts_at.date(), session.starts_at.hour * 60 + session.starts_at.minute)
            for session in Session.objects.filter(
                expert=expert, status__in=('pending', 'confirmed'),
                starts_at__date__gte=start, starts_at__date__lte=end,
            )
        }
        free = [
            slot for slot in slots
            if (slot.day, slot.start_minute) not in taken
        ]
        return Response(AvailabilitySlotSerializer(free, many=True).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def match(request):
    """Who you would be paired with. ?discipline=&day=&start_minute=

    The interstitial calls this while it plays, so the card it opens on is a
    real expert rather than a fixed sample.
    """
    discipline = request.query_params.get('discipline', 'therapy')
    day = _as_date(request.query_params.get('day'))
    start_minute = _as_int(request.query_params.get('start_minute'))
    language = request.query_params.get('language')

    expert, score = matching.best(discipline, day, start_minute, language)
    if expert is None:
        return Response(
            {'detail': 'Nobody is free then. Try another time.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    body = ExpertSerializer(expert, context={'request': request}).data
    # The design's "98% Match", now meaning something -- see matching.py.
    body['match_percent'] = score
    return Response(body)


class SessionListView(generics.ListCreateAPIView):
    """GET your sessions; POST books one.

    Booking does not take payment: it reserves the slot and comes back with a
    session to pay for. Only paying confirms it.
    """

    serializer_class = SessionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        sessions = Session.objects.filter(
            client=self.request.user,
        ).select_related('expert', 'expert__profile')
        if self.request.query_params.get('upcoming') == 'true':
            sessions = sessions.filter(
                status='confirmed', starts_at__gte=timezone.now(),
            )
        return sessions

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        form = BookingSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        booking = form.validated_data

        expert = booking.get('expert')
        if expert is None:
            expert, _score = matching.best(
                booking['discipline'], booking['day'], booking['start_minute'],
            )
        if expert is None:
            raise ValidationError(
                {'detail': 'Nobody is free then. Try another time.'},
            )

        starts_at = _combine(booking['day'], booking['start_minute'])
        if starts_at <= timezone.now():
            raise ValidationError({'detail': 'That time has already passed.'})

        if _already_taken(expert, starts_at):
            raise ValidationError(
                {'detail': 'That slot has just been taken. Try another time.'},
            )

        session = Session.objects.create(
            client=request.user,
            expert=expert,
            discipline=booking['discipline'],
            starts_at=starts_at,
            ends_at=starts_at + datetime.timedelta(minutes=SESSION_MINUTES),
            mode=booking['mode'],
            plan=booking['plan'],
            price_naira=PRICES[booking['discipline']][booking['plan']],
            status='pending',
            expires_at=timezone.now() + datetime.timedelta(minutes=HOLD_MINUTES),
        )
        return Response(
            SessionSerializer(session, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class SessionDetailView(generics.RetrieveAPIView):
    serializer_class = SessionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return _sessions_for(self.request.user)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_session(request, pk):
    """Either side may cancel. Who did is recorded, for the refund rules."""
    session = get_object_or_404(_sessions_for(request.user), pk=pk)
    if session.status in ('completed', 'cancelled'):
        raise ValidationError({'detail': 'This session is already over.'})
    session.cancel(by=request.user)
    return Response(SessionSerializer(session, context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def rate_session(request, pk):
    """The stars and the note at the end of the booking flow.

    Only the client rates, and only a session that actually happened.
    """
    session = get_object_or_404(Session, pk=pk, client=request.user)
    if session.status != 'completed':
        raise ValidationError(
            {'detail': 'You can rate a session once it has taken place.'},
        )

    form = RatingSerializer(data=request.data)
    form.is_valid(raise_exception=True)
    session.rating = form.validated_data['rating']
    session.feedback = form.validated_data.get('feedback', '')
    session.save(update_fields=['rating', 'feedback'])
    session.expert.refresh_rating()
    return Response(SessionSerializer(session, context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def call_token(request, pk):
    """Credentials for joining this session's call.

    Issued only to the two people on the booking, only for a confirmed
    session, and only around the time it runs -- a token minted days early
    would be a key to a room left lying about.
    """
    session = get_object_or_404(_sessions_for(request.user), pk=pk)

    if session.status != 'confirmed':
        raise ValidationError({'detail': 'This session is not confirmed.'})

    now = timezone.now()
    opens = session.starts_at - datetime.timedelta(minutes=15)
    closes = session.ends_at + datetime.timedelta(minutes=15)
    if now < opens:
        raise ValidationError(
            {'detail': 'The room opens fifteen minutes before the session.'},
        )
    if now > closes:
        raise ValidationError({'detail': 'This session has finished.'})

    try:
        token = calls.token_for(
            user_id=request.user.pk,
            room_id=session.room_id,
            seconds=int((closes - now).total_seconds()),
        )
    except calls.NotConfigured as error:
        return Response(
            {'detail': str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # Two things happen at the moment somebody is let into a room, and both
    # belong here rather than in the app: the person is recorded as having
    # been told they will be recorded, and the recording is started. The app
    # cannot skip either by not calling them.
    _disclose_and_record(session, request.user)

    return Response({
        'app_id': calls.app_id(),
        'token': token,
        'room_id': session.room_id,
        'user_id': str(request.user.pk),
        'expires_at': closes,
        'mode': session.mode,
        'recording_notice': str(recordings_models.NOTICE_TEXT),
    })


def _disclose_and_record(session, user):
    recordings_models.RecordingNotice.objects.get_or_create(
        session=session, user=user,
        defaults={'version': recordings_models.NOTICE_VERSION},
    )
    jobs.run(recording_tasks.start_recording, session.pk)


# ----------------------------------------------------------- the expert side

class MyAvailabilityView(generics.ListCreateAPIView):
    """The practitioner's own availability."""

    serializer_class = AvailabilitySlotSerializer
    permission_classes = [IsAuthenticated, IsPractitioner]
    pagination_class = None

    def get_queryset(self):
        return AvailabilitySlot.objects.filter(
            expert=_my_expert(self.request), day__gte=timezone.localdate(),
        )

    def perform_create(self, serializer):
        serializer.save(expert=_my_expert(self.request))


class MyAvailabilityDetailView(generics.DestroyAPIView):
    serializer_class = AvailabilitySlotSerializer
    permission_classes = [IsAuthenticated, IsPractitioner]

    def get_queryset(self):
        return AvailabilitySlot.objects.filter(expert=_my_expert(self.request))


class MySessionsView(generics.ListAPIView):
    """What is booked with me, soonest first."""

    serializer_class = SessionSerializer
    permission_classes = [IsAuthenticated, IsPractitioner]

    def get_queryset(self):
        sessions = Session.objects.filter(
            expert=_my_expert(self.request),
        ).select_related('client', 'expert', 'expert__profile')
        if self.request.query_params.get('upcoming') == 'true':
            sessions = sessions.filter(status='confirmed', starts_at__gte=timezone.now())
        return sessions


class MyEarningsView(APIView):
    permission_classes = [IsAuthenticated, IsPractitioner]

    def get(self, request):
        return Response(EarningsSerializer(earnings_for(_my_expert(request))).data)


class MyPayoutsView(generics.ListAPIView):
    serializer_class = PayoutSerializer
    permission_classes = [IsAuthenticated, IsPractitioner]

    def get_queryset(self):
        return Payout.objects.filter(expert=_my_expert(self.request))


class MyPayoutMethodsView(generics.ListCreateAPIView):
    serializer_class = PayoutMethodSerializer
    permission_classes = [IsAuthenticated, IsPractitioner]
    pagination_class = None

    def get_queryset(self):
        return PayoutMethod.objects.filter(expert=_my_expert(self.request))

    def perform_create(self, serializer):
        expert = _my_expert(self.request)
        if serializer.validated_data.get('is_default', True):
            PayoutMethod.objects.filter(expert=expert).update(is_default=False)
        serializer.save(expert=expert)


class SessionNoteView(APIView):
    """The practitioner's note on a session. Never served to the client."""

    permission_classes = [IsAuthenticated, IsPractitioner]

    def get_session(self, request, pk):
        return get_object_or_404(Session, pk=pk, expert=_my_expert(request))

    def get(self, request, pk):
        session = self.get_session(request, pk)
        note = getattr(session, 'note', None)
        if note is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(SessionNoteSerializer(note).data)

    def put(self, request, pk):
        session = self.get_session(request, pk)
        serializer = SessionNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note, _created = SessionNote.objects.update_or_create(
            session=session, defaults={'body': serializer.validated_data['body']},
        )
        return Response(SessionNoteSerializer(note).data)


class ApplicationView(generics.ListCreateAPIView):
    """Applying to practise here, and checking on it afterwards."""

    serializer_class = ApplicationSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = None

    def get_queryset(self):
        return Application.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        application = serializer.save(user=self.request.user)
        # Both documents are marked required in the form; an application
        # without them cannot be reviewed, so they are taken together with it.
        for kind in ('certification', 'identity'):
            uploaded = self.request.FILES.get(kind)
            if uploaded is not None:
                ApplicationDocument.objects.create(
                    application=application, kind=kind, file=uploaded,
                )


# --------------------------------------------------------------- small parts

def _my_expert(request):
    profile = getattr(request.user, 'profile', None)
    expert = getattr(profile, 'expert', None)
    if expert is None:
        raise ValidationError(
            {'detail': 'This account is not set up as a practitioner yet.'},
        )
    return expert


def _sessions_for(user):
    """Sessions this person is part of, on either side."""
    from django.db.models import Q

    return Session.objects.filter(
        Q(client=user) | Q(expert__profile__user=user),
    ).select_related('expert', 'expert__profile', 'client')


def _already_taken(expert, starts_at):
    return Session.objects.filter(
        expert=expert, starts_at=starts_at, status__in=('pending', 'confirmed'),
    ).exists()


def _combine(day, start_minute):
    naive = datetime.datetime.combine(
        day, datetime.time(hour=start_minute // 60, minute=start_minute % 60),
    )
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _as_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
