"""Recording, scanning, disclosure and deletion.

The scanning tests carry the most weight: a false flag means a reviewer opens
a therapy session that did not need opening, and a missed one means the thing
this was built for did not happen.
"""

import datetime
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, Role
from api.testing import ApiTestCase
from experts.models import ExpertProfile, Session
from recordings import scanning
from recordings.models import (
    AccessLog,
    Flag,
    Recording,
    RecordingNotice,
    Transcript,
)
from recordings.tasks import flag_recording, purge_old_recordings


class ScanningTests(ApiTestCase):
    """What gets flagged, and -- just as important -- what does not."""

    def kinds(self, text):
        return [finding['kind'] for finding in scanning.scan(text)]

    def test_a_written_nigerian_number_is_caught(self):
        found = scanning.scan('you can reach me on 08031234567 any time')

        self.assertEqual(found[0]['kind'], 'contact')
        self.assertIn('0803', found[0]['matched'])

    def test_the_international_form_is_caught_too(self):
        self.assertIn('contact', self.kinds('call +234 803 123 4567'))

    def test_a_number_said_aloud_is_caught(self):
        """A transcript renders a spoken number as words."""
        said = 'my number is zero eight zero three one two three four five six seven'

        self.assertIn('contact', self.kinds(said))

    def test_a_handle_is_caught(self):
        self.assertIn('contact', self.kinds('find me on instagram @calm.with.ada'))

    def test_an_email_is_caught(self):
        self.assertIn('contact', self.kinds('mail me at ada@example.com'))

    def test_arranging_to_book_elsewhere_is_caught(self):
        for line in (
            'you can book me directly next time',
            "it's cheaper if we book direct",
            'just pay me directly and we skip the fee',
            'transfer it to my account instead',
        ):
            self.assertIn('off_platform', self.kinds(line), msg=line)

    def test_a_disclosure_of_risk_is_caught(self):
        found = scanning.scan('some days I think about ending my life')

        self.assertEqual(found[0]['kind'], 'safety')

    def test_ordinary_talk_about_money_is_not_flagged(self):
        """"Pay" and "transfer" belong in sessions about money worries."""
        for line in (
            'I could not pay my rent this month and it kept me awake',
            'work wants to transfer me to Abuja and I am dreading it',
            'my account is empty again, it is the third month',
            'I keep thinking about how much I owe my brother',
        ):
            self.assertEqual(self.kinds(line), [], msg=line)

    def test_ordinary_distress_is_not_a_safety_flag(self):
        """The queue is read by people; false alarms bury real disclosures."""
        for line in (
            'I am so tired of all of this',
            'I felt like giving up on the project',
            'that meeting killed me, honestly',
            'I could murder a plate of rice right now',
        ):
            self.assertEqual(self.kinds(line), [], msg=line)

    def test_a_year_or_a_price_is_not_read_as_a_phone_number(self):
        for line in (
            'back in two thousand and nineteen things were easier',
            'it cost twenty five thousand naira for the session',
        ):
            self.assertEqual(self.kinds(line), [], msg=line)

    def test_a_flag_carries_the_words_around_it_not_the_session(self):
        text = 'a' * 500 + ' call me on 08031234567 ' + 'b' * 500

        excerpt = scanning.scan(text)[0]['excerpt']

        self.assertLess(len(excerpt), 310)
        self.assertNotIn('a' * 200, excerpt)

    def test_an_empty_transcript_raises_nothing(self):
        self.assertEqual(scanning.scan(''), [])
        self.assertEqual(scanning.scan(None), [])


class RecordingTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        coach = Profile.create_user(
            email='coach@example.com', password='Soothify2026',
            full_name='Baraqhat', role=Role.PRACTITIONER,
        )
        self.expert = ExpertProfile.objects.create(
            profile=coach, disciplines=['therapy'],
        )
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')
        self.session = Session.objects.create(
            client=self.profile.user, expert=self.expert, discipline='therapy',
            starts_at=timezone.now() + datetime.timedelta(minutes=5),
            ends_at=timezone.now() + datetime.timedelta(minutes=55),
            status='confirmed', price_naira=25000,
        )


class DisclosureTests(RecordingTestCase):
    def test_joining_records_that_the_person_was_told(self):
        with patch('recordings.tasks.zego.start', return_value='TASK1'):
            response = self.client.post(
                reverse('api:experts:session-call-token', args=[self.session.pk]),
                HTTP_AUTHORIZATION=self.token,
            )

        self.assertEqual(response.status_code, 200)
        notice = RecordingNotice.objects.get()
        self.assertEqual(notice.user, self.profile.user)
        self.assertEqual(notice.session, self.session)

    def test_the_wording_is_handed_back_so_the_app_can_show_it(self):
        with patch('recordings.tasks.zego.start', return_value='TASK1'):
            response = self.client.post(
                reverse('api:experts:session-call-token', args=[self.session.pk]),
                HTTP_AUTHORIZATION=self.token,
            )

        self.assertIn('recorded', response.json()['recording_notice'])

    def test_recording_starts_when_the_first_person_is_let_in(self):
        with patch('recordings.tasks.zego.start', return_value='TASK1') as start:
            self.client.post(
                reverse('api:experts:session-call-token', args=[self.session.pk]),
                HTTP_AUTHORIZATION=self.token,
            )

        start.assert_called_once()
        recording = Recording.objects.get()
        self.assertEqual(recording.status, 'recording')
        self.assertEqual(recording.task_id, 'TASK1')

    def test_the_second_person_joining_does_not_start_a_second_recording(self):
        coach_token = self.signed_in_as('coach@example.com', 'Soothify2026')
        url = reverse('api:experts:session-call-token', args=[self.session.pk])

        with patch('recordings.tasks.zego.start', return_value='TASK1') as start:
            self.client.post(url, HTTP_AUTHORIZATION=self.token)
            self.client.post(url, HTTP_AUTHORIZATION=coach_token)

        self.assertEqual(start.call_count, 1)
        self.assertEqual(Recording.objects.count(), 1)

    def test_a_call_still_connects_when_recording_cannot_start(self):
        """Losing the recording is bad; dropping a therapy session is worse."""
        from recordings.zego import ZegoError

        with patch('recordings.tasks.zego.start', side_effect=ZegoError('down')):
            response = self.client.post(
                reverse('api:experts:session-call-token', args=[self.session.pk]),
                HTTP_AUTHORIZATION=self.token,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Recording.objects.get().status, 'failed')


class FlaggingTests(RecordingTestCase):
    def setUp(self):
        super().setUp()
        self.recording = Recording.objects.create(
            session=self.session, room_id=self.session.room_id,
            status='stored', audio_key='recordings/1/audio.mp3',
        )

    def test_a_transcript_with_a_number_raises_a_flag(self):
        Transcript.objects.create(
            recording=self.recording,
            text='thanks for today. call me on 08031234567 next week',
        )

        made = flag_recording(self.recording.pk)

        self.assertEqual(made, 1)
        self.assertEqual(Flag.objects.get().kind, 'contact')
        self.assertEqual(Flag.objects.get().status, 'new')

    def test_an_ordinary_session_raises_nothing(self):
        Transcript.objects.create(
            recording=self.recording,
            text='we talked about sleep and the pressure at work. '
                 'she will try the breathing exercise before bed.',
        )

        self.assertEqual(flag_recording(self.recording.pk), 0)

    def test_scanning_twice_does_not_duplicate_a_flag(self):
        Transcript.objects.create(
            recording=self.recording, text='reach me on 08031234567',
        )

        flag_recording(self.recording.pk)
        flag_recording(self.recording.pk)

        self.assertEqual(Flag.objects.count(), 1)

    def test_nothing_is_acted_on_automatically(self):
        """A flag is a queue entry. It suspends nobody."""
        Transcript.objects.create(
            recording=self.recording, text='just pay me directly next time',
        )

        flag_recording(self.recording.pk)

        self.expert.refresh_from_db()
        self.assertTrue(self.expert.is_accepting)
        self.assertTrue(self.expert.is_published)


class RetentionTests(RecordingTestCase):
    def setUp(self):
        super().setUp()
        self.recording = Recording.objects.create(
            session=self.session, room_id=self.session.room_id,
            status='stored', audio_key='recordings/1/audio.mp3',
            delete_after=timezone.now() - datetime.timedelta(minutes=1),
        )

    def test_storing_audio_sets_a_deletion_date_thirty_days_out(self):
        recording = Recording.objects.create(
            session=Session.objects.create(
                client=self.profile.user, expert=self.expert, discipline='yoga',
                starts_at=timezone.now(), ends_at=timezone.now(),
            ),
            room_id='session-x',
        )

        recording.mark_stored('recordings/x/audio.mp3', duration_seconds=3000)

        days = (recording.delete_after - timezone.now()).days
        self.assertEqual(days, 29)      # 30 days, minus the hours elapsed

    def test_audio_past_its_date_is_deleted(self):
        with patch('django.core.files.storage.default_storage.exists', return_value=True):
            with patch('django.core.files.storage.default_storage.delete') as delete:
                purged = purge_old_recordings()

        delete.assert_called_once_with('recordings/1/audio.mp3')
        self.assertEqual(purged, 1)
        recording = Recording.objects.get()
        self.assertEqual(recording.audio_key, '')
        self.assertEqual(recording.status, 'deleted')
        self.assertIsNotNone(recording.deleted_at)

    def test_the_row_survives_so_there_is_a_record_it_existed(self):
        with patch('django.core.files.storage.default_storage.exists', return_value=False):
            purge_old_recordings()

        self.assertEqual(Recording.objects.count(), 1)

    def test_an_unflagged_transcript_goes_with_the_audio(self):
        Transcript.objects.create(recording=self.recording, text='an ordinary session')

        with patch('django.core.files.storage.default_storage.exists', return_value=False):
            purge_old_recordings()

        self.assertEqual(Transcript.objects.count(), 0)

    def test_a_transcript_behind_an_open_flag_is_kept(self):
        Transcript.objects.create(recording=self.recording, text='call me on 08031234567')
        Flag.objects.create(
            recording=self.recording, kind='contact', excerpt='call me on 08031234567',
        )

        with patch('django.core.files.storage.default_storage.exists', return_value=False):
            purge_old_recordings()

        self.assertEqual(Transcript.objects.count(), 1)
        self.assertEqual(Flag.objects.count(), 1)

    def test_recent_audio_is_left_alone(self):
        Recording.objects.update(
            delete_after=timezone.now() + datetime.timedelta(days=29),
        )

        with patch('django.core.files.storage.default_storage.delete') as delete:
            purge_old_recordings()

        delete.assert_not_called()


class AccessTests(RecordingTestCase):
    def test_there_is_no_endpoint_that_serves_session_audio(self):
        """Not even to the people on the call: a stolen phone is a real risk."""
        from django.urls import NoReverseMatch

        Recording.objects.create(
            session=self.session, room_id=self.session.room_id,
            status='stored', audio_key='recordings/1/audio.mp3',
        )

        with self.assertRaises(NoReverseMatch):
            reverse('api:recordings:audio', args=[1])

    def test_listening_in_the_admin_writes_an_access_log_row(self):
        staff = Profile.create_user(email='staff@example.com', password='Soothify2026')
        staff.user.is_staff = True
        staff.user.is_superuser = True
        staff.user.save(update_fields=['is_staff', 'is_superuser'])
        recording = Recording.objects.create(
            session=self.session, room_id=self.session.room_id,
            status='stored', audio_key='recordings/1/audio.mp3',
        )
        self.client.force_login(staff.user)

        self.client.get(
            reverse('admin:recordings_recording_listen', args=[recording.pk]),
        )

        entry = AccessLog.objects.get()
        self.assertEqual(entry.staff, staff.user)
        self.assertEqual(entry.action, 'audio')


class CallbackTests(RecordingTestCase):
    def test_an_unsigned_callback_is_refused(self):
        Recording.objects.create(
            session=self.session, room_id=self.session.room_id, status='recording',
        )

        response = self.client.post(
            reverse('api:recordings:callback'),
            {'room_id': self.session.room_id, 'file_info': {'url': 'x.mp3'}},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Recording.objects.get().audio_key, '')
