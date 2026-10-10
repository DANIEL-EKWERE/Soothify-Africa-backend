"""What CommunityRepository asks for, and what the forum must never leak.

The privacy tests are the important ones here: the app is explicit that a post
carries "the poster's forum handle, not their account name".
"""

import unittest

from django.conf import settings
from django.urls import reverse

from accounts.models import Profile
from api.testing import ApiTestCase
from community.models import Comment, Discussion, DiscussionLike, Report

# The forum is on hold (FEATURES['community'] in settings.py) and its URLs are
# not mounted, so these cannot run. Kept rather than deleted: when the feature
# comes back, the flag is all that has to change.
pytestmark = unittest.skipUnless(
    settings.FEATURES.get('community'), 'the forum is on hold',
)


@unittest.skipUnless(settings.FEATURES.get('community'), 'the forum is on hold')


class CommunityTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.profile.community_handle = 'Kosin'
        self.profile.save(update_fields=['community_handle'])
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')
        self.discussions_url = reverse('api:community:discussions')

    def other_member(self, handle='Paul'):
        profile = Profile.create_user(
            email='paul@example.com', password='Soothify2026', full_name='Paul Eze',
        )
        profile.community_handle = handle
        profile.save(update_fields=['community_handle'])
        return self.signed_in_as('paul@example.com', 'Soothify2026')

    def post_thread(self, token=None, **overrides):
        payload = {'title': 'Depressed and tired', 'body': 'I found help here.'}
        payload.update(overrides)
        return self.client.post(
            self.discussions_url, payload, content_type='application/json',
            HTTP_AUTHORIZATION=token or self.token,
        )


@unittest.skipUnless(settings.FEATURES.get('community'), 'the forum is on hold')
class DiscussionTests(CommunityTestCase):
    """getDiscussions() and post()"""

    def test_posting_returns_the_thread_so_it_can_be_shown_at_once(self):
        response = self.post_thread()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['title'], 'Depressed and tired')
        self.assertEqual(body['author_username'], 'Kosin')

    def test_a_post_carries_the_handle_and_never_the_real_name_or_email(self):
        self.post_thread()

        row = self.client.get(self.discussions_url).json()['results'][0]

        self.assertEqual(row['author_username'], 'Kosin')
        serialised = str(row)
        self.assertNotIn('Ada Obi', serialised)
        self.assertNotIn('ada@example.com', serialised)

    def test_someone_without_a_handle_cannot_post(self):
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.post_thread(
            token=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('community_handle', response.json())
        self.assertEqual(Discussion.objects.count(), 0)

    def test_a_guest_can_read_the_forum_but_not_post(self):
        self.post_thread()

        read = self.client.get(self.discussions_url)
        write = self.client.post(
            self.discussions_url, {'title': 'Hi', 'body': 'Hello'},
            content_type='application/json',
        )

        self.assertEqual(read.status_code, 200)
        self.assertEqual(write.status_code, 401)

    def test_threads_come_back_newest_first(self):
        self.post_thread(title='First')
        self.post_thread(title='Second')

        titles = [
            row['title'] for row in self.client.get(self.discussions_url).json()['results']
        ]

        self.assertEqual(titles, ['Second', 'First'])

    def test_an_empty_post_is_refused(self):
        self.assertEqual(self.post_thread(body='   ').status_code, 400)
        self.assertEqual(self.post_thread(title='  ').status_code, 400)

    def test_only_the_author_can_edit_or_delete(self):
        thread = self.post_thread().json()
        url = reverse('api:community:discussion', args=[thread['id']])
        someone_else = self.other_member()

        edit = self.client.patch(
            url, {'body': 'Rewritten'}, content_type='application/json',
            HTTP_AUTHORIZATION=someone_else,
        )
        delete = self.client.delete(url, HTTP_AUTHORIZATION=someone_else)

        self.assertEqual(edit.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        self.assertEqual(Discussion.objects.count(), 1)

    def test_a_removed_thread_is_gone_from_the_forum(self):
        thread = self.post_thread().json()
        Discussion.objects.filter(pk=thread['id']).update(is_removed=True)

        listed = self.client.get(self.discussions_url).json()['results']
        fetched = self.client.get(
            reverse('api:community:discussion', args=[thread['id']]),
        )

        self.assertEqual(listed, [])
        self.assertEqual(fetched.status_code, 404)


@unittest.skipUnless(settings.FEATURES.get('community'), 'the forum is on hold')
class CommentTests(CommunityTestCase):
    """getComments() and comment()"""

    def setUp(self):
        super().setUp()
        self.thread = self.post_thread().json()
        self.url = reverse('api:community:comments', args=[self.thread['id']])

    def reply(self, body='Thank you for sharing', token=None):
        return self.client.post(
            self.url, {'body': body}, content_type='application/json',
            HTTP_AUTHORIZATION=token or self.token,
        )

    def test_replying_returns_the_comment(self):
        response = self.reply()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['author_username'], 'Kosin')

    def test_replies_read_oldest_first(self):
        self.reply('First')
        self.reply('Second')

        bodies = [row['body'] for row in self.client.get(self.url).json()]

        self.assertEqual(bodies, ['First', 'Second'])

    def test_replying_updates_the_count_on_the_card(self):
        self.reply(token=self.other_member())

        row = self.client.get(self.discussions_url).json()['results'][0]

        self.assertEqual(row['comment_count'], 1)

    def test_deleting_a_reply_puts_the_count_back(self):
        comment = self.reply().json()

        self.client.delete(
            reverse('api:community:comment', args=[comment['id']]),
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(
            self.client.get(self.discussions_url).json()['results'][0]['comment_count'], 0,
        )

    def test_a_guest_cannot_reply(self):
        response = self.client.post(
            self.url, {'body': 'Hello'}, content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)


@unittest.skipUnless(settings.FEATURES.get('community'), 'the forum is on hold')
class LikeShareReportTests(CommunityTestCase):
    def setUp(self):
        super().setUp()
        self.thread = self.post_thread().json()

    def test_liking_counts_once_however_many_times_it_is_sent(self):
        url = reverse('api:community:like', args=[self.thread['id']])

        self.client.post(url, HTTP_AUTHORIZATION=self.token)
        response = self.client.post(url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.json()['like_count'], 1)
        self.assertTrue(response.json()['liked'])
        self.assertEqual(DiscussionLike.objects.count(), 1)

    def test_a_like_can_be_taken_back(self):
        url = reverse('api:community:like', args=[self.thread['id']])
        self.client.post(url, HTTP_AUTHORIZATION=self.token)

        response = self.client.delete(url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.json()['like_count'], 0)
        self.assertFalse(response.json()['liked'])

    def test_the_list_says_which_threads_you_have_liked(self):
        self.client.post(
            reverse('api:community:like', args=[self.thread['id']]),
            HTTP_AUTHORIZATION=self.token,
        )

        row = self.client.get(
            self.discussions_url, HTTP_AUTHORIZATION=self.token,
        ).json()['results'][0]

        self.assertTrue(row['liked'])

    def test_sharing_only_moves_a_counter(self):
        response = self.client.post(
            reverse('api:community:share', args=[self.thread['id']]),
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.json()['share_count'], 1)

    def test_a_reported_post_stays_visible_until_a_person_acts(self):
        response = self.client.post(
            reverse('api:community:report', args=[self.thread['id']]),
            {'reason': 'harm', 'detail': 'This worries me'},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.other_member(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Report.objects.count(), 1)
        self.assertIsNone(Report.objects.get().handled_at)
        self.assertFalse(Discussion.objects.get().is_removed)


@unittest.skipUnless(settings.FEATURES.get('community'), 'the forum is on hold')
class HandleTests(CommunityTestCase):
    def test_a_taken_handle_is_reported_as_taken(self):
        response = self.client.get(
            '{0}?handle=Kosin'.format(reverse('api:community:handle-available')),
        )

        self.assertFalse(response.json()['available'])

    def test_a_free_handle_is_reported_as_free(self):
        response = self.client.get(
            '{0}?handle=quiet_storm'.format(reverse('api:community:handle-available')),
        )

        self.assertTrue(response.json()['available'])

    def test_two_people_cannot_share_a_handle(self):
        other = Profile.create_user(email='paul@example.com', password='Soothify2026')

        response = self.client.patch(
            reverse('api:accounts:me'), {'community_handle': 'Kosin'},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.signed_in_as('paul@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.status_code, 400)
        other.refresh_from_db()
        self.assertIsNone(other.community_handle)
