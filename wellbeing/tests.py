"""What MoodRepository, JournalRepository and ProfileRepository ask for.

The rules being checked are the app's own: one check-in per day that
re-checking replaces, a heading derived from the note's first line, and stats
counted from real playback rather than invented.
"""

from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile
from api.testing import ApiTestCase
from content.models import Category, MediaItem, PlaybackEvent
from wellbeing.models import JournalEntry, MoodEntry


class WellbeingTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.user = self.profile.user
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')

    def post(self, url, payload, token=None):
        return self.client.post(
            url, payload, content_type='application/json',
            HTTP_AUTHORIZATION=token or self.token,
        )


class MoodTests(WellbeingTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('api:wellbeing:moods')

    def test_needs_an_account(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_records_the_slider_position_and_names_the_level(self):
        response = self.post(self.url, {'score': 0.95, 'note': 'Good day'})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['score'], 0.95)
        self.assertEqual(response.json()['level'], 'awesome')
        self.assertEqual(response.json()['note'], 'Good day')

    def test_the_ten_bands_match_the_apps_arithmetic(self):
        """MoodLevel.forScore: ten even bands across the track."""
        expected = (
            (0.0, 'awful'), (0.05, 'awful'), (0.15, 'drained'), (0.25, 'anxious'),
            (0.35, 'low'), (0.45, 'neutral'), (0.55, 'reflective'),
            (0.65, 'content'), (0.75, 'energized'), (0.85, 'joyful'),
            (0.95, 'awesome'), (1.0, 'awesome'),
        )

        for score, level in expected:
            self.assertEqual(
                MoodEntry(score=score).level_key, level,
                msg='score {0} should be {1}'.format(score, level),
            )

    def test_checking_in_twice_in_a_day_replaces_rather_than_stacks(self):
        self.post(self.url, {'score': 0.2})

        response = self.post(self.url, {'score': 0.8})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(MoodEntry.objects.count(), 1)
        self.assertEqual(MoodEntry.objects.get().score, 0.8)

    def test_a_score_off_the_slider_is_refused(self):
        self.assertEqual(self.post(self.url, {'score': 4}).status_code, 400)
        self.assertEqual(self.post(self.url, {'score': -1}).status_code, 400)

    def test_history_is_newest_first(self):
        today = timezone.localdate()
        for days, score in ((2, 0.1), (1, 0.5), (0, 0.9)):
            MoodEntry.objects.create(
                user=self.user, score=score,
                recorded_on=today - timezone.timedelta(days=days),
                recorded_at=timezone.now() - timezone.timedelta(days=days),
            )

        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual([row['score'] for row in response.json()], [0.9, 0.5, 0.1])

    def test_a_date_range_backs_the_week_strip(self):
        today = timezone.localdate()
        for days in range(10):
            MoodEntry.objects.create(
                user=self.user, score=0.5,
                recorded_on=today - timezone.timedelta(days=days),
                recorded_at=timezone.now() - timezone.timedelta(days=days),
            )
        week_ago = today - timezone.timedelta(days=6)

        response = self.client.get(
            '{0}?from={1}&to={2}'.format(self.url, week_ago, today),
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(len(response.json()), 7)

    def test_todays_entry_is_served_for_preselecting_the_slider(self):
        self.post(self.url, {'score': 0.65})

        response = self.client.get(
            reverse('api:wellbeing:mood-today'), HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['level'], 'content')

    def test_no_check_in_today_is_not_an_error(self):
        response = self.client.get(
            reverse('api:wellbeing:mood-today'), HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.status_code, 204)

    def test_one_persons_moods_are_not_anothers(self):
        self.post(self.url, {'score': 0.9})
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.json(), [])


class JournalTests(WellbeingTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('api:wellbeing:journal')

    def test_writes_a_note(self):
        response = self.post(self.url, {'body': 'Today was heavy but I got through it.'})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(JournalEntry.objects.count(), 1)

    def test_the_heading_is_the_notes_first_line(self):
        response = self.post(self.url, {'body': 'A quiet morning\nthen the rest of it.'})

        self.assertEqual(response.json()['title'], 'A quiet morning')

    def test_a_long_opening_line_is_cut_on_a_word(self):
        body = (
            'I have been thinking about the way the mornings have felt lately '
            'and it is hard to say'
        )

        response = self.post(self.url, {'body': body})

        title = response.json()['title']
        self.assertTrue(title.endswith('…'))
        self.assertLessEqual(len(title), JournalEntry.TITLE_LIMIT + 1)
        self.assertFalse(title[:-1].endswith(' '))

    def test_an_empty_note_is_refused(self):
        self.assertEqual(self.post(self.url, {'body': '   '}).status_code, 400)

    def test_notes_come_back_newest_first(self):
        self.post(self.url, {'body': 'First'})
        self.post(self.url, {'body': 'Second'})

        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        titles = [row['title'] for row in response.json()['results']]
        self.assertEqual(titles, ['Second', 'First'])

    def test_a_note_can_be_rewritten(self):
        created = self.post(self.url, {'body': 'Draft line'}).json()
        url = reverse('api:wellbeing:journal-entry', args=[created['id']])

        response = self.client.patch(
            url, {'body': 'A better line\nand more'}, content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.json()['title'], 'A better line')

    def test_a_note_can_be_deleted(self):
        created = self.post(self.url, {'body': 'Forget this'}).json()

        response = self.client.delete(
            reverse('api:wellbeing:journal-entry', args=[created['id']]),
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_nobody_else_can_read_your_journal(self):
        created = self.post(self.url, {'body': 'Private'}).json()
        Profile.create_user(email='bola@example.com', password='Soothify2026')
        other = self.signed_in_as('bola@example.com', 'Soothify2026')

        response = self.client.get(
            reverse('api:wellbeing:journal-entry', args=[created['id']]),
            HTTP_AUTHORIZATION=other,
        )

        self.assertEqual(response.status_code, 404)

    def test_nobody_else_can_delete_your_journal(self):
        created = self.post(self.url, {'body': 'Private'}).json()
        Profile.create_user(email='bola@example.com', password='Soothify2026')
        other = self.signed_in_as('bola@example.com', 'Soothify2026')

        self.client.delete(
            reverse('api:wellbeing:journal-entry', args=[created['id']]),
            HTTP_AUTHORIZATION=other,
        )

        self.assertEqual(JournalEntry.objects.count(), 1)


class ProfileStatsTests(WellbeingTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('api:wellbeing:stats')

    def test_a_new_account_reads_zero(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(
            response.json(),
            {'meditation_minutes': 0, 'balance_minutes': 0, 'live_sessions': 0},
        )

    def test_counts_minutes_from_what_was_actually_played(self):
        calm = Category.objects.create(id='calm', name='Calm')
        yoga = Category.objects.create(id='yoga', name='Yoga')
        meditation = MediaItem.objects.create(
            title='Breath work', category=calm, duration_seconds=1800,
        )
        stretch = MediaItem.objects.create(
            title='Morning flow', category=yoga, duration_seconds=1800,
        )
        PlaybackEvent.objects.create(user=self.user, item=meditation, seconds_played=1200)
        PlaybackEvent.objects.create(user=self.user, item=meditation, seconds_played=600)
        PlaybackEvent.objects.create(user=self.user, item=stretch, seconds_played=900)

        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.json()['meditation_minutes'], 30)
        self.assertEqual(response.json()['balance_minutes'], 15)

    def test_your_stats_are_yours_alone(self):
        calm = Category.objects.create(id='calm', name='Calm')
        item = MediaItem.objects.create(title='Breath work', category=calm)
        PlaybackEvent.objects.create(user=self.user, item=item, seconds_played=3600)
        Profile.create_user(email='bola@example.com', password='Soothify2026')

        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.json()['meditation_minutes'], 0)
