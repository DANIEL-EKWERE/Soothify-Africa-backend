"""What a practitioner points a client at.

The Journal's second tab. Two things are worth being strict about and both
are tested here: an expert can only write into the Journal of somebody they
have actually seen, and only a piece of content the client can open.
"""

import datetime

from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile
from api.testing import ApiTestCase
from content.models import Category, MediaItem
from experts.models import Recommendation, Session
from experts.tests import make_expert
from notifications.models import Notification

MINE = '/api/v1/experts/me/recommendations/'
THEIRS = '/api/v1/recommendations/'


class RecommendationTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.expert = make_expert()
        self.expert_token = self.signed_in_as('coach@example.com', 'Soothify2026')

        self.client_profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.ada = self.client_profile.user
        self.ada_token = self.signed_in_as('ada@example.com', 'Soothify2026')

        Category.objects.create(id='calm', name='Calm')
        self.item = MediaItem.objects.create(
            title='Guided Breathing for calm', category_id='calm',
            media_type='video', is_published=True,
        )
        self.session = Session.objects.create(
            expert=self.expert, client=self.ada, discipline='therapy',
            starts_at=timezone.now() - datetime.timedelta(days=1),
            ends_at=timezone.now() - datetime.timedelta(days=1, minutes=-50),
            status='completed',
        )

    def leave(self, token=None, **overrides):
        payload = {'client': self.ada.pk, 'item': self.item.pk,
                   'note': 'Try this before bed.'}
        payload.update(overrides)
        return self.client.post(
            MINE, payload, content_type='application/json',
            HTTP_AUTHORIZATION=token or self.expert_token,
        )

    # ------------------------------------------------------- leaving one

    def test_a_practitioner_can_recommend_to_a_client_they_have_seen(self):
        response = self.leave(session=self.session.pk)

        self.assertEqual(response.status_code, 201)
        row = Recommendation.objects.get()
        self.assertEqual(row.expert, self.expert)
        self.assertEqual(row.client, self.ada)
        self.assertEqual(row.item, self.item)
        self.assertEqual(row.note, 'Try this before bed.')

    def test_it_can_be_left_between_sessions(self):
        # The app allows one without a call behind it.
        response = self.leave()

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(Recommendation.objects.get().session)

    def test_the_client_is_told(self):
        self.leave()

        notification = Notification.objects.get(user=self.ada)
        self.assertIn('recommended something', notification.title)
        self.assertEqual(notification.body, 'Guided Breathing for calm')
        self.assertEqual(notification.target, 'journal')

    # ------------------------------------------------------------ refusals

    def test_not_to_somebody_they_have_never_seen(self):
        stranger = Profile.create_user(
            email='bola@example.com', password='Soothify2026', full_name='Bola',
        ).user

        response = self.leave(client=stranger.pk)

        # Without this an expert could write into any account by guessing
        # a number.
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Recommendation.objects.exists())

    def test_not_a_piece_of_content_that_is_not_published(self):
        MediaItem.objects.filter(pk=self.item.pk).update(is_published=False)

        response = self.leave()

        # Pointing somebody at something they cannot open is worse than not
        # recommending it.
        self.assertEqual(response.status_code, 400)

    def test_not_somebody_else_s_session(self):
        other = make_expert('yogi@example.com', 'Yoga Only', disciplines=('yoga',))
        theirs = Session.objects.create(
            expert=other, client=self.ada, discipline='yoga',
            starts_at=timezone.now(),
            ends_at=timezone.now() + datetime.timedelta(minutes=50),
            status='completed',
        )

        response = self.leave(session=theirs.pk)

        self.assertEqual(response.status_code, 400)

    def test_a_client_cannot_leave_one(self):
        response = self.leave(token=self.ada_token)

        self.assertEqual(response.status_code, 403)

    # -------------------------------------------------------- reading them

    def test_the_client_sees_what_was_left_for_them(self):
        self.leave(session=self.session.pk)

        rows = self.client.get(
            THEIRS, HTTP_AUTHORIZATION=self.ada_token,
        ).json()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['expert_name'], 'Baraqhat Ibrahim')
        self.assertEqual(row['note'], 'Try this before bed.')
        self.assertEqual(row['content']['title'], 'Guided Breathing for calm')
        self.assertEqual(row['content']['media_type'], 'video')
        self.assertEqual(row['session_label'], 'Therapy')
        self.assertTrue(row['is_new'])

    def test_nobody_sees_anybody_else_s(self):
        self.leave()
        bola = Profile.create_user(
            email='bola@example.com', password='Soothify2026', full_name='Bola',
        )
        token = self.signed_in_as('bola@example.com', 'Soothify2026')

        rows = self.client.get(THEIRS, HTTP_AUTHORIZATION=token).json()

        self.assertEqual(rows, [])

    def test_one_whose_content_was_withdrawn_is_not_offered(self):
        self.leave()
        MediaItem.objects.filter(pk=self.item.pk).update(is_published=False)

        rows = self.client.get(THEIRS, HTTP_AUTHORIZATION=self.ada_token).json()

        # A card that opens nothing is worse than no card.
        self.assertEqual(rows, [])

    def test_a_stranger_sees_none_at_all(self):
        self.assertEqual(self.client.get(THEIRS).status_code, 401)

    # ------------------------------------------------------------- reading

    def test_opening_one_clears_its_new_pill(self):
        self.leave()
        row = Recommendation.objects.get()

        response = self.client.post(
            '/api/v1/recommendations/{0}/read/'.format(row.pk),
            HTTP_AUTHORIZATION=self.ada_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['is_new'])
        row.refresh_from_db()
        self.assertIsNotNone(row.read_at)

    def test_reading_it_twice_keeps_the_first_time(self):
        self.leave()
        row = Recommendation.objects.get()
        url = '/api/v1/recommendations/{0}/read/'.format(row.pk)

        self.client.post(url, HTTP_AUTHORIZATION=self.ada_token)
        row.refresh_from_db()
        first = row.read_at

        self.client.post(url, HTTP_AUTHORIZATION=self.ada_token)
        row.refresh_from_db()

        self.assertEqual(row.read_at, first)

    def test_nobody_can_read_somebody_else_s(self):
        self.leave()
        row = Recommendation.objects.get()

        response = self.client.post(
            '/api/v1/recommendations/{0}/read/'.format(row.pk),
            HTTP_AUTHORIZATION=self.expert_token,
        )

        self.assertEqual(response.status_code, 404)

    # --------------------------------------------------- the expert's list

    def test_a_practitioner_sees_what_they_have_left(self):
        self.leave()

        rows = self.client.get(
            MINE, HTTP_AUTHORIZATION=self.expert_token,
        ).json()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['content']['title'], 'Guided Breathing for calm')


class BySessionTests(RecommendationTests):
    """The call screen names the session, never the client.

    Reading the client off the booking is what lets the practitioner's app
    recommend something mid-call without ever handling an account id.
    """

    def test_naming_the_session_is_enough(self):
        response = self.client.post(
            MINE,
            {'item': self.item.pk, 'session': self.session.pk},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.expert_token,
        )

        self.assertEqual(response.status_code, 201)
        row = Recommendation.objects.get()
        self.assertEqual(row.client, self.ada)
        self.assertEqual(row.session, self.session)

    def test_somebody_else_s_session_is_refused(self):
        other = make_expert('yogi@example.com', 'Yoga Only', disciplines=('yoga',))
        theirs = Session.objects.create(
            expert=other, client=self.ada, discipline='yoga',
            starts_at=timezone.now(),
            ends_at=timezone.now() + datetime.timedelta(minutes=50),
            status='completed',
        )

        response = self.client.post(
            MINE,
            {'item': self.item.pk, 'session': theirs.pk},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.expert_token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Recommendation.objects.exists())

    def test_neither_a_session_nor_a_client_is_refused(self):
        response = self.client.post(
            MINE, {'item': self.item.pk},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.expert_token,
        )

        self.assertEqual(response.status_code, 400)
