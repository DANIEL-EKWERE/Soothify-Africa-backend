"""/api/v1/community/ -- threads, replies, likes and reports.

Reading is open, as the forum is part of what the app shows a visitor who has
not signed up. Posting needs both an account and a handle: the design's own
flow asks for one before letting anyone write, and posting under a real name
by accident is not a mistake this should allow.
"""

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from api.permissions import IsOwnerOrReadOnly

from .models import Comment, Discussion, DiscussionLike
from .serializers import CommentSerializer, DiscussionSerializer, ReportSerializer


def handle_for(user):
    """The handle this person posts under.

    Refuses rather than inventing one: a handle picked by the server would be
    a name someone did not choose, attached to what they write about their
    mental health.
    """
    profile = getattr(user, 'profile', None)
    handle = getattr(profile, 'community_handle', '') or ''
    if not handle.strip():
        raise ValidationError(
            {'community_handle': 'Choose a community handle before posting.'},
        )
    return handle.strip()


class DiscussionListView(generics.ListCreateAPIView):
    """The forum, newest first. POST starts a thread."""

    serializer_class = DiscussionSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated()]
        return [AllowAny()]

    def get_queryset(self):
        return Discussion.objects.filter(is_removed=False).select_related('author')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        user = self.request.user
        if user.is_authenticated:
            # One query for the whole page instead of one per card.
            context['liked_ids'] = set(
                DiscussionLike.objects.filter(user=user).values_list(
                    'discussion_id', flat=True,
                ),
            )
        return context

    def perform_create(self, serializer):
        serializer.save(
            author=self.request.user, author_handle=handle_for(self.request.user),
        )


class DiscussionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """One thread. Only its author may edit or delete it."""

    serializer_class = DiscussionSerializer
    permission_classes = [IsOwnerOrReadOnly]

    def get_queryset(self):
        return Discussion.objects.filter(is_removed=False).select_related('author')

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [AllowAny()]
        return [IsAuthenticated(), IsOwnerOrReadOnly()]


class CommentListView(generics.ListCreateAPIView):
    """Replies on one thread, oldest first -- the order a conversation reads."""

    serializer_class = CommentSerializer
    pagination_class = None

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated()]
        return [AllowAny()]

    def discussion(self):
        return get_object_or_404(
            Discussion, pk=self.kwargs['pk'], is_removed=False,
        )

    def get_queryset(self):
        return Comment.objects.filter(
            discussion_id=self.kwargs['pk'], is_removed=False,
        )

    @transaction.atomic
    def perform_create(self, serializer):
        discussion = self.discussion()
        serializer.save(
            discussion=discussion,
            author=self.request.user,
            author_handle=handle_for(self.request.user),
        )
        discussion.refresh_counts()


class CommentDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CommentSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrReadOnly]

    def get_queryset(self):
        return Comment.objects.filter(is_removed=False)

    def perform_destroy(self, instance):
        discussion = instance.discussion
        instance.delete()
        discussion.refresh_counts()


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def like(request, pk):
    """Like a thread, or take it back."""
    discussion = get_object_or_404(Discussion, pk=pk, is_removed=False)

    if request.method == 'POST':
        DiscussionLike.objects.get_or_create(discussion=discussion, user=request.user)
    else:
        DiscussionLike.objects.filter(discussion=discussion, user=request.user).delete()

    discussion.refresh_counts()
    return Response(
        DiscussionSerializer(discussion, context={'request': request}).data,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def share(request, pk):
    """Record that someone shared a thread.

    Only a counter; nothing about who shared it or where it went.
    """
    discussion = get_object_or_404(Discussion, pk=pk, is_removed=False)
    discussion.share_count += 1
    discussion.save(update_fields=['share_count', 'updated_at'])
    return Response({'share_count': discussion.share_count})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def report(request, pk):
    """Flag a thread for a person to look at. Nothing is hidden automatically."""
    discussion = get_object_or_404(Discussion, pk=pk, is_removed=False)
    serializer = ReportSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(discussion=discussion, reporter=request.user)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([AllowAny])
def handle_available(request):
    """Is this handle free? The compose screen asks before letting someone post."""
    from accounts.models import Profile

    handle = request.query_params.get('handle', '').strip()
    taken = bool(handle) and Profile.objects.filter(
        community_handle__iexact=handle,
    ).exists()
    return Response({'handle': handle, 'available': bool(handle) and not taken})
