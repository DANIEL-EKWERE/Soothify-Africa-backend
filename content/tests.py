"""What ContentRepository asks of /api/v1/content/.

Each test names the app method it stands behind, so a change here that breaks
a screen is obvious from the failure.
"""

from django.urls import reverse

from accounts.models import Profile
from api.testing import ApiTestCase
from content.models import (
    Category,
    MediaItem,
    MediaRating,
    PlaybackEvent,
    Shelf,
    ShelfItem,
)


def make_item(title='Daily focus', **overrides):
    fields = {
        'title': title,
        'subtitle': 'Burnout relief',
        'tag': 'Pilates & Core',
        'media_type': 'audio',
        'duration_seconds': 2700,
        'credited_name': 'Jacob Samuel',
    }
    fields.update(overrides)
    return MediaItem.objects.create(**fields)


class CategoryTests(ApiTestCase):
    """getCategories()"""

    def test_lists_active_categories_in_order(self):
        Category.objects.create(id='sleep', name='Sleep', position=2)
        Category.objects.create(id='calm', name='Calm', position=1)
        Category.objects.create(id='gone', name='Retired', position=0, is_active=False)

        response = self.client.get(reverse('api:content:categories'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.json()], ['calm', 'sleep'])

    def test_open_to_a_guest(self):
        """The app is browsable without an account."""
        self.assertEqual(
            self.client.get(reverse('api:content:categories')).status_code, 200,
        )


class MediaListTests(ApiTestCase):
    """getByCategory() and search()"""

    def setUp(self):
        super().setUp()
        self.focus = Category.objects.create(id='focus', name='Focus')
        self.calm = Category.objects.create(id='calm', name='Calm')
        self.item = make_item('Daily focus', category=self.focus)
        make_item('Mindfulness', category=self.calm, credited_name='Ada Obi')
        self.url = reverse('api:content:items')

    def test_serves_the_field_names_the_app_parses(self):
        response = self.client.get('{0}?category=focus'.format(self.url))

        row = response.json()['results'][0]
        for field in (
            'id', 'title', 'subtitle', 'tag', 'media_type', 'duration_seconds',
            'rating', 'cover_url', 'media_url', 'category', 'practitioner_name',
            'is_free',
        ):
            self.assertIn(field, row)
        self.assertEqual(row['media_type'], 'audio')
        self.assertEqual(row['practitioner_name'], 'Jacob Samuel')

    def test_filters_by_category(self):
        response = self.client.get('{0}?category=calm'.format(self.url))

        titles = [row['title'] for row in response.json()['results']]
        self.assertEqual(titles, ['Mindfulness'])

    def test_search_matches_title_or_practitioner(self):
        by_title = self.client.get('{0}?q=focus'.format(self.url)).json()['results']
        by_person = self.client.get('{0}?q=ada'.format(self.url)).json()['results']

        self.assertEqual([row['title'] for row in by_title], ['Daily focus'])
        self.assertEqual([row['title'] for row in by_person], ['Mindfulness'])

    def test_an_empty_search_finds_nothing_rather_than_everything(self):
        response = self.client.get('{0}?q='.format(self.url))

        self.assertEqual(response.json()['results'], [])

    def test_unpublished_items_are_not_served(self):
        make_item('Draft', is_published=False)

        response = self.client.get(self.url)

        self.assertNotIn('Draft', [row['title'] for row in response.json()['results']])


class PaywallTests(ApiTestCase):
    """is_free, and what a paid item hands out."""

    def setUp(self):
        super().setUp()
        self.paid = make_item('Mindfulness', is_free=False, external_url='https://cdn/x.mp3')
        self.free = make_item('Breath work', external_url='https://cdn/y.mp3')

    def test_a_free_item_comes_with_a_playable_url(self):
        response = self.client.get(
            reverse('api:content:item', args=[self.free.pk]),
        )

        self.assertEqual(response.json()['media_url'], 'https://cdn/y.mp3')
        self.assertFalse(response.json()['requires_subscription'])

    def test_a_paid_item_does_not(self):
        response = self.client.get(
            reverse('api:content:item', args=[self.paid.pk]),
        )

        self.assertEqual(response.json()['media_url'], '')
        self.assertTrue(response.json()['requires_subscription'])


class ShelfTests(ApiTestCase):
    """getShelf() and getShelfPage()"""

    def setUp(self):
        super().setUp()
        self.shelf = Shelf.objects.create(
            key='voice-overs', title='Voice overs', preview_limit=3,
        )
        # Added out of order on purpose: the row's order is a layout decision.
        for position, title in ((2, 'Third'), (0, 'First'), (1, 'Second'), (3, 'Fourth')):
            ShelfItem.objects.create(
                shelf=self.shelf, item=make_item(title), position=position,
            )

    def test_row_previews_in_the_curated_order(self):
        response = self.client.get(
            reverse('api:content:shelf', args=['voice-overs']),
        )

        body = response.json()
        self.assertEqual(body['title'], 'Voice overs')
        self.assertEqual(
            [row['title'] for row in body['items']], ['First', 'Second', 'Third'],
        )

    def test_see_all_returns_the_whole_shelf(self):
        response = self.client.get(
            '{0}?full=true'.format(reverse('api:content:shelf', args=['voice-overs'])),
        )

        self.assertEqual(len(response.json()['items']), 4)

    def test_an_unknown_shelf_is_a_404(self):
        response = self.client.get(reverse('api:content:shelf', args=['nope']))

        self.assertEqual(response.status_code, 404)

    def test_two_shelves_serve_their_own_slices(self):
        """The design gives Voice Overs and Sound Effects different cards."""
        other = Shelf.objects.create(key='sound-effects', title='Sound effects')
        ShelfItem.objects.create(shelf=other, item=make_item('Rain'), position=0)

        response = self.client.get(
            reverse('api:content:shelf', args=['sound-effects']),
        )

        self.assertEqual([row['title'] for row in response.json()['items']], ['Rain'])


class FeaturedAndRecommendedTests(ApiTestCase):
    """getFeatured() and getRecommendations() are different lists."""

    def setUp(self):
        super().setUp()
        make_item('On home', is_featured=True)
        make_item('On the page', is_recommended=True)
        make_item('Neither')

    def test_home_row_holds_only_featured_items(self):
        response = self.client.get(reverse('api:content:featured'))

        self.assertEqual([row['title'] for row in response.json()], ['On home'])

    def test_recommendations_page_is_its_own_list(self):
        response = self.client.get(reverse('api:content:recommendations'))

        self.assertEqual([row['title'] for row in response.json()], ['On the page'])


class RecentTests(ApiTestCase):
    """getRecent()"""

    def setUp(self):
        super().setUp()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')
        self.url = reverse('api:content:recent')

    def test_needs_an_account(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_is_empty_before_anything_is_played(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.json(), [])

    def test_lists_what_was_played_newest_first(self):
        first, second = make_item('First'), make_item('Second')
        for item in (first, second):
            self.client.post(
                reverse('api:content:item-plays', args=[item.pk]),
                {'seconds_played': 600, 'completed': True},
                content_type='application/json',
                HTTP_AUTHORIZATION=self.token,
            )

        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual([row['title'] for row in response.json()], ['Second', 'First'])

    def test_playing_the_same_thing_twice_lists_it_once(self):
        item = make_item('Daily focus')
        for _ in range(2):
            self.client.post(
                reverse('api:content:item-plays', args=[item.pk]),
                {'seconds_played': 60},
                content_type='application/json',
                HTTP_AUTHORIZATION=self.token,
            )

        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(len(response.json()), 1)
        self.assertEqual(PlaybackEvent.objects.count(), 2)

    def test_one_persons_history_is_not_anothers(self):
        item = make_item('Daily focus')
        self.client.post(
            reverse('api:content:item-plays', args=[item.pk]),
            {'seconds_played': 60},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.json(), [])

    def test_reported_time_cannot_exceed_the_length_of_the_thing(self):
        item = make_item('Daily focus', duration_seconds=300)

        self.client.post(
            reverse('api:content:item-plays', args=[item.pk]),
            {'seconds_played': 99999},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(PlaybackEvent.objects.get().seconds_played, 300)


class RatingTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        Profile.create_user(email='ada@example.com', password='Soothify2026')
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')
        self.item = make_item('Unshakeable')
        self.url = reverse('api:content:item-rating', args=[self.item.pk])

    def rate(self, score, token=None):
        return self.client.put(
            self.url, {'score': score}, content_type='application/json',
            HTTP_AUTHORIZATION=token or self.token,
        )

    def test_scoring_updates_the_average_on_the_card(self):
        response = self.rate(4)

        self.assertEqual(response.json()['rating'], 4.0)
        self.assertEqual(response.json()['rating_count'], 1)

    def test_changing_your_mind_replaces_your_score(self):
        self.rate(2)

        response = self.rate(5)

        self.assertEqual(response.json()['rating'], 5.0)
        self.assertEqual(MediaRating.objects.count(), 1)

    def test_two_people_average(self):
        self.rate(5)
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.rate(3, token=self.signed_in_as('bola@example.com', 'Soothify2026'))

        self.assertEqual(response.json()['rating'], 4.0)
        self.assertEqual(response.json()['rating_count'], 2)

    def test_a_score_outside_one_to_five_is_refused(self):
        self.assertEqual(self.rate(9).status_code, 400)

    def test_a_guest_cannot_rate(self):
        response = self.client.put(
            self.url, {'score': 5}, content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)


class SignedMediaTests(ApiTestCase):
    """Files live in folders, so a signed link has to survive a slash.

    This caught a real bug: the URL pattern used <str:token>, which stops at
    a slash, so every cover and every audio file failed to sign on the
    deployment that serves its own media.
    """

    def test_a_file_in_a_folder_can_be_signed_and_served(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from api.media import signed_url

        name = default_storage.save('covers/deep/rest.txt', ContentFile(b'hello'))
        try:
            url = signed_url(name)

            response = self.client.get(url)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'hello')
        finally:
            default_storage.delete(name)

    def test_a_tampered_link_is_refused(self):
        from api.media import signed_url

        url = signed_url('covers/deep/rest.txt')

        response = self.client.get(url.replace('rest.txt', 'secret.txt'))

        self.assertEqual(response.status_code, 403)
