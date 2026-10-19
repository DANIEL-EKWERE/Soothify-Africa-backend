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

from . import interests
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

    Personalised from the onboarding questionnaire, and open to anyone. That
    combination is the point: the questionnaire is answered before there is
    an account, so the answers arrive with the request rather than being
    looked up by user. See content/interests.py for the mapping.

    Three ways it decides, in order:

      POST with {"concerns": [...], "goals": [...]}  the device's own
          answers, which is what an anonymous visitor sends.
      GET while signed in                            their saved answers.
      anything else                                  the staff-picked list.

    A POST because "depression" in a query string ends up in access logs,
    proxy caches and the CDN cache key. It reads, and it writes nothing.

    ?mood= is still accepted and still echoed: mood-aware picking is not
    built, and the app already sends it.
    """

    serializer_class = MediaItemSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def post(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_queryset(self):
        picked = MediaItem.objects.live().select_related('category', 'practitioner')

        keys = interests.categories_for(self._answers())
        if keys:
            matched = picked.filter(category_id__in=keys)
            if matched.exists():
                # Ordered so the strongest signal leads, which a plain
                # `filter` would not do -- it answers in the table's order,
                # not the one the questionnaire implied.
                return sorted(
                    matched, key=lambda item: keys.index(item.category_id),
                )

        # Nothing to go on, or nothing in the library that answers it. The
        # staff-picked list is the fallback, never an empty screen.
        return picked.filter(is_recommended=True)

    def _answers(self):
        """What the questionnaire holds, from the body or from the account."""
        sent = self.request.data if self.request.method == 'POST' else None
        if isinstance(sent, dict) and (sent.get('concerns') or sent.get('goals')):
            return sent

        user = self.request.user
        if user.is_authenticated:
            kyc = getattr(user, 'kyc', None)
            if kyc is not None:
                return kyc.answers or {}
        return {}


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
