"""Pushing a feed row to the phone.

Firebase is never reached. What is tested is everything this side of the
wire: that a row becomes a message addressed to that person's phones and
nobody else's, that the map the app parses says where to go, that a dead
token is forgotten, and -- the one that matters most -- that no failure out
there can take a cron run or a request down with it.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from notifications import push
from notifications.models import Notification, PushDevice


class FakeResponse:
    def __init__(self, exception=None):
        self.exception = exception
        self.success = exception is None


class FakeBatch:
    def __init__(self, responses):
        self.responses = responses
        self.success_count = sum(1 for r in responses if r.success)


class UnregisteredError(Exception):
    pass


class SenderIdMismatchError(Exception):
    pass


def fake_messaging(batch=None, raises=None):
    """A stand-in for `firebase_admin.messaging`.

    Only the handful of names push.py touches. The message classes are
    recorded as the arguments they were built with, which is what lets the
    payload be asserted on without a Firebase project.
    """
    m = mock.MagicMock()
    m.UnregisteredError = UnregisteredError
    m.SenderIdMismatchError = SenderIdMismatchError
    if raises is not None:
        m.send_each_for_multicast.side_effect = raises
    else:
        m.send_each_for_multicast.return_value = batch or FakeBatch(
            [FakeResponse()],
        )
    return m


class PushTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='ada', email='ada@example.com', password='x',
        )
        self.seen = timezone.now()
        # push.py caches the module after the first look; each test supplies
        # its own, so the cache is cleared between them.
        self.addCleanup(self._forget)
        self._forget()

    def _forget(self):
        push._messaging_module = None
        push._looked = False

    def device(self, token='token-1', user=None, active=True):
        # Descending `last_seen_at`, one second apart: PushDevice is ordered
        # newest-first, and tokens have to go out in a known order for a
        # test to say which of them Firebase refused.
        self.seen -= timezone.timedelta(seconds=1)
        return PushDevice.objects.create(
            user=user or self.user, token=token, last_seen_at=self.seen,
            platform='android', is_active=active,
        )

    def row(self, **fields):
        fields.setdefault('kind', 'reminder')
        fields.setdefault('title', 'Reminder')
        fields.setdefault('body', 'You have a session today at 5:00pm')
        return Notification.objects.create(user=self.user, **fields)

    def send(self, notification, messaging):
        with mock.patch.object(push, '_messaging', return_value=messaging):
            return push.push_notification(notification.pk)

    # ------------------------------------------------------------ the send

    def test_a_row_is_pushed_to_that_person_s_phones(self):
        self.device('phone-a')
        self.device('phone-b')
        m = fake_messaging(FakeBatch([FakeResponse(), FakeResponse()]))

        sent = self.send(self.row(), m)

        self.assertEqual(sent, 2)
        tokens = m.MulticastMessage.call_args.kwargs['tokens']
        self.assertEqual(sorted(tokens), ['phone-a', 'phone-b'])

    def test_somebody_else_s_phone_is_not_told(self):
        other = get_user_model().objects.create_user(
            username='bola', email='bola@example.com', password='x',
        )
        self.device('mine')
        self.device('theirs', user=other)
        m = fake_messaging()

        self.send(self.row(), m)

        self.assertEqual(m.MulticastMessage.call_args.kwargs['tokens'], ['mine'])

    def test_a_phone_that_was_switched_off_is_skipped(self):
        self.device('live')
        self.device('muted', active=False)
        m = fake_messaging()

        self.send(self.row(), m)

        self.assertEqual(m.MulticastMessage.call_args.kwargs['tokens'], ['live'])

    def test_nothing_is_sent_to_nobody(self):
        m = fake_messaging()

        self.assertEqual(self.send(self.row(), m), 0)
        m.send_each_for_multicast.assert_not_called()

    def test_a_deleted_row_is_not_pushed(self):
        self.device()
        notification = self.row()
        pk = notification.pk
        notification.delete()
        m = fake_messaging()

        with mock.patch.object(push, '_messaging', return_value=m):
            self.assertEqual(push.push_notification(pk), 0)

    # --------------------------------------------------------- the payload

    def test_the_words_are_carried_twice(self):
        # Once in the notification block, which the system draws by itself,
        # and once in the data, which the background isolate reads when it
        # draws a push the system did not.
        self.device()
        m = fake_messaging()

        self.send(self.row(title='Reminder', body='At five'), m)

        m.Notification.assert_called_once_with(title='Reminder', body='At five')
        data = m.MulticastMessage.call_args.kwargs['data']
        self.assertEqual(data['title'], 'Reminder')
        self.assertEqual(data['body'], 'At five')

    def test_a_session_row_opens_the_session(self):
        self.device()
        m = fake_messaging()

        self.send(self.row(target='session/12'), m)

        data = m.MulticastMessage.call_args.kwargs['data']
        self.assertEqual(data['type'], 'session')
        self.assertEqual(data['session'], '12')

    def test_the_breathing_minute_opens_the_breathing_minute(self):
        self.device()
        m = fake_messaging()

        self.send(self.row(kind='breathe', target='breathe'), m)

        self.assertEqual(
            m.MulticastMessage.call_args.kwargs['data']['type'], 'breathe',
        )

    def test_anything_else_opens_the_feed(self):
        # 'subscription' is not one of the app's six topics. A push the
        # person was shown must always open something, and the feed is the
        # one screen that can show any of them.
        self.device()
        m = fake_messaging()

        self.send(self.row(target='subscription'), m)

        data = m.MulticastMessage.call_args.kwargs['data']
        self.assertEqual(data['type'], 'feed')
        # The original is still carried, so a later app can route on it.
        self.assertEqual(data['target'], 'subscription')

    def test_every_value_is_a_string(self):
        # FCM rejects a data map holding anything else, with a 400 that does
        # not say which key.
        self.device()
        m = fake_messaging()

        self.send(self.row(target='session/12'), m)

        data = m.MulticastMessage.call_args.kwargs['data']
        self.assertTrue(all(isinstance(v, str) for v in data.values()), data)

    # ------------------------------------------------------- dead tokens

    def test_an_uninstalled_app_s_token_is_dropped(self):
        self.device('gone')
        self.device('here')
        m = fake_messaging(FakeBatch([
            FakeResponse(UnregisteredError()), FakeResponse(),
        ]))

        self.send(self.row(), m)

        self.assertEqual(
            list(PushDevice.objects.values_list('token', flat=True)), ['here'],
        )

    def test_a_token_that_merely_failed_is_kept(self):
        # The network, a rate limit, Firebase itself. Deleting on those
        # would empty the table over a bad afternoon.
        self.device('flaky')
        m = fake_messaging(FakeBatch([FakeResponse(RuntimeError('503'))]))

        self.send(self.row(), m)

        self.assertTrue(PushDevice.objects.filter(token='flaky').exists())

    # ----------------------------------------------------- never load-bearing

    def test_no_key_means_no_push_and_no_error(self):
        self.device()

        with mock.patch.object(push, '_messaging', return_value=None):
            self.assertEqual(push.push_notification(self.row().pk), 0)

    def test_firebase_failing_does_not_raise(self):
        self.device()
        m = fake_messaging(raises=RuntimeError('Firebase is down'))

        self.assertEqual(self.send(self.row(), m), 0)

    def test_a_row_is_written_even_when_the_push_cannot_be(self):
        # The point of the whole arrangement: the feed is the record, the
        # push is a courtesy.
        self.device()

        with mock.patch.object(push, '_messaging', side_effect=RuntimeError):
            push.notify(self.user, kind='plain', title='Still filed')

        self.assertTrue(Notification.objects.filter(title='Still filed').exists())


class NotifyTests(TestCase):
    """The one entry point every caller uses."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='ada', email='ada@example.com', password='x',
        )

    def test_it_files_the_row_and_pushes_it(self):
        with mock.patch.object(push.jobs, 'run') as run:
            notification = push.notify(
                self.user, kind='reminder', title='Reminder', body='At five',
            )

        self.assertEqual(Notification.objects.get().pk, notification.pk)
        run.assert_called_once_with(push.push_notification, notification.pk)

    def test_a_chip_survives_the_trip(self):
        with mock.patch.object(push.jobs, 'run'):
            push.notify(
                self.user, kind='reminder', title='Reminder',
                target='session/3', filters=['sessions'],
            )

        self.assertEqual(Notification.objects.get().filter_keys, ['sessions'])

    def test_a_quiet_row_is_filed_without_lighting_a_screen(self):
        with mock.patch.object(push.jobs, 'run') as run:
            push.notify(self.user, kind='plain', title='FYI', push=False)

        self.assertTrue(Notification.objects.exists())
        run.assert_not_called()
