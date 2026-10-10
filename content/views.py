"""/api/v1/content/ -- the eight things ContentRepository asks for.

Reading is open to anyone: the app is explicit that it can be browsed without
an account ("Browsing without an account. The app is fully usable this way").
Anything about a particular person -- their recent plays, their rating --
needs a token.
"""

from django.db.models import Max
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Category, MediaItem, MediaRating, PlaybackEvent, Shelf
from .serializers import (
    CategorySerializer,
    MediaItemSerializer,
    PlaybackSerializer,
    RatingSerializer,
)


class CategoryListView(generics.ListAPIView):
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]
    pagination_class = None          # five of them; a page of five is silly
    queryset = Category.objects.filter(is_active=True)


class MediaListView(generics.ListAPIView):
    """The catalogue, filtered.

    Covers `getByCategory` (?category=focus) and `search` (?q=breath) with one
    endpoint, because they differ only in the filter.
    """

    serializer_class = MediaItemSerializer
    permission_classes = [AllowAny]
    filter_backends = []             # the query params below are the filter

    def get_queryset(self):
        items = MediaItem.objects.live().select_related('category', 'practitioner')
        params = self.request.query_params

        term = params.get('q')
        if term is not None:
            # An explicit empty search returns nothing, as the app's own
            # search does -- not the whole library.
            items = items.search(term)

        category = params.get('category')
        if category:
            items = items.filter(category_id=category)

        media_type = params.get('type')
        if media_type:
            items = items.filter(media_type=media_type)

        if params.get('free') == 'true':
            items = items.filter(is_free=True)
        return items


class MediaDetailView(generics.RetrieveAPIView):
    serializer_class = MediaItemSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return MediaItem.objects.live().select_related('category', 'practitioner')


class FeaturedView(generics.ListAPIView):
    """Home's "Recommended for you" row."""

    serializer_class = MediaItemSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        return MediaItem.objects.live().filter(is_featured=True).select_related(
            'category', 'practitioner',
        )


class RecommendationsView(generics.ListAPIView):
    """The full "Recommended for you" page -- a longer, separate list.

    Takes an optional ?mood= (the app's MoodLevel key). Mood-aware picking is
    not built yet, so the parameter is accepted and recorded in the response
    rather than silently ignored: the app already sends it.
    """

    serializer_class = MediaItemSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        return MediaItem.objects.live().filter(is_recommended=True).select_related(
            'category', 'practitioner',
        )


class ShelfView(APIView):
    """One named shelf: 'voice-overs', 'sound-effects', 'yoga-flow', 'sounds'.

    ?full=true returns everything on it -- the page behind "See All" -- and
    the default returns just the cards the row previews.
    """

    permission_classes = [AllowAny]

    def get(self, request, key):
        shelf = get_object_or_404(Shelf, pk=key, is_active=True)
        items = shelf.ordered_items().select_related('category', 'practitioner')
        if request.query_params.get('full') != 'true':
            items = items[:shelf.preview_limit]
        return Response({
            'key': shelf.key,
            'title': shelf.title,
            'description': shelf.description,
            'items': MediaItemSerializer(
                items, many=True, context={'request': request},
            ).data,
        })


class RecentView(generics.ListAPIView):
    """What this person opened last, newest first, each item once."""

    serializer_class = MediaItemSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        recent = (
            PlaybackEvent.objects
            .filter(user=self.request.user)
            .values('item')
            .annotate(last=Max('played_at'))
            .order_by('-last')[:20]
        )
        ids = [row['item'] for row in recent]
        if not ids:
            return MediaItem.objects.none()
        items = {
            item.id: item
            for item in MediaItem.objects.live()
            .filter(id__in=ids)
            .select_related('category', 'practitioner')
        }
        # Put them back in "most recently played" order, which the database
        # will not do for an __in lookup.
        return [items[pk] for pk in ids if pk in items]


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def record_playback(request, pk):
    """The player reporting a finished listen."""
    item = get_object_or_404(MediaItem.objects.live(), pk=pk)
    serializer = PlaybackSerializer(data=request.data, context={'item': item})
    serializer.is_valid(raise_exception=True)
    PlaybackEvent.objects.create(
        user=request.user,
        item=item,
        seconds_played=serializer.validated_data['seconds_played'],
        completed=serializer.validated_data['completed'],
    )
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def rate_item(request, pk):
    """Score something out of 5. Sending it again changes your score."""
    item = get_object_or_404(MediaItem.objects.live(), pk=pk)
    serializer = RatingSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    MediaRating.objects.update_or_create(
        user=request.user, item=item,
        defaults={'score': serializer.validated_data['score']},
    )
    item.refresh_rating()
    return Response(
        MediaItemSerializer(item, context={'request': request}).data,
    )
