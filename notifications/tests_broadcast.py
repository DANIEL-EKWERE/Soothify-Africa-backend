"""Sending one message to many people.

The part that matters most here is who a message reaches: an audience that
is one person too wide is the kind of mistake that cannot be taken back, so
the count the dashboard shows and the people the send actually writes to are
asserted to be the same set.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from billing.models import Membership, Plan
from notifications import broadcast as sending
from notifications.models import Broadcast, Notification, Preferences
from home.models import WaitlistSignup


def a_user(email, **profile):
    user = get_user_model().objects.create_user(
        username=email, email=email, password='x',
    )
    if profile:
        from accounts.models import Profile

        Profile.objects.update_or_create(user=user, defaults=profile)
    return user


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    CELERY_BROKER_URL='',
)
class AudienceTests(TestCase):
    """Who a broadcast is addressed to."""

    def setUp(self):
        self.ada = a_user('ada@example.com')
        self.bola = a_user('bola@example.com')
        self.coach = a_user('coach@example.com', role='practitioner')

    def draft(self, **fields):
        fields.setdefault('title', 'Hello')
        fields.setdefault('body', 'A line.\n\nAnother line.')
        fields.setdefault('channel', Broadcast.Channel.BOTH)
        return Broadcast.objects.create(**fields)

    def reached(self, broadcast):
        return set(sending.audience_for(broadcast).values_list('email', flat=True))

    def test_everyone_means_every_active_account(self):
        row = self.draft(audience=Broadcast.Audience.EVERYONE)

        self.assertEqual(self.reached(row), {
            'ada@example.com', 'bola@example.com', 'coach@example.com',
        })

    def test_a_deactivated_account_is_left_out(self):
        get_user_model().objects.filter(pk=self.bola.pk).update(is_active=False)
        row = self.draft(audience=Broadcast.Audience.EVERYONE)

        self.assertNotIn('bola@example.com', self.reached(row))

    def test_practitioners_means_practitioners(self):
        row = self.draft(audience=Broadcast.Audience.PRACTITIONERS)

        self.assertEqual(self.reached(row), {'coach@example.com'})

    def test_members_means_the_ones_paying(self):
        plan = Plan.objects.create(
            key='core-monthly', name='Core', description='Core',
            price_naira=5000, cadence='monthly',
        )
        Membership.objects.create(user=self.ada, plan=plan, state='active')
        Membership.objects.create(user=self.bola, plan=plan, state='expired')

        self.assertEqual(
            self.reached(self.draft(audience=Broadcast.Audience.MEMBERS)),
            {'ada@example.com'},
        )
        self.assertEqual(
            self.reached(self.draft(audience=Broadcast.Audience.LAPSED)),
            {'bola@example.com'},
        )

    def test_chosen_means_only_those_picked(self):
        row = self.draft(audience=Broadcast.Audience.CHOSEN)
        row.people.set([self.bola])

        self.assertEqual(self.reached(row), {'bola@example.com'})

    # ---------------------------------------------------------- consent

    def test_a_promotional_message_goes_only_to_those_who_said_yes(self):
        Preferences.objects.create(user=self.ada, notify_offers=True)
        Preferences.objects.create(user=self.bola, notify_offers=False)

        row = self.draft(
            audience=Broadcast.Audience.EVERYONE, is_marketing=True,
        )

        # The switch defaults to off, so somebody who never touched it never
        # agreed to be marketed to -- and coach has no preferences row at all.
        self.assertEqual(self.reached(row), {'ada@example.com'})

    def test_an_ordinary_message_ignores_that_switch(self):
        # "Your session starts in an hour" is not marketing.
        row = self.draft(audience=Broadcast.Audience.EVERYONE)

        self.assertEqual(len(self.reached(row)), 3)

    # --------------------------------------------------------- waitlist

    def test_the_waitlist_is_addresses_not_accounts(self):
        WaitlistSignup.objects.create(email='new@example.com')
        WaitlistSignup.objects.create(email='ada@example.com')
        row = self.draft(
            audience=Broadcast.Audience.WAITLIST,
            channel=Broadcast.Channel.EMAIL,
        )

        # ada already has an account, so she is reached by the account
        # audiences; sending to her here as well would be twice.
        self.assertEqual(sending.waitlist_addresses(row), ['new@example.com'])
        self.assertFalse(sending.audience_for(row).exists())


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    CELERY_BROKER_URL='',
)
class SendingTests(TestCase):
    def setUp(self):
        self.ada = a_user('ada@example.com')
        self.bola = a_user('bola@example.com')
        self.row = Broadcast.objects.create(
            title='The library is open',
            body='First paragraph.\n\nSecond paragraph.',
            audience=Broadcast.Audience.EVERYONE,
            channel=Broadcast.Channel.BOTH,
            state=Broadcast.State.SENDING,
        )

    def send(self):
        with mock.patch('notifications.push.jobs.run'):
            return sending.send_broadcast(self.row.pk)

    def test_everybody_gets_a_feed_row_and_an_email(self):
        self.send()

        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            sorted(m.to[0] for m in mail.outbox),
            ['ada@example.com', 'bola@example.com'],
        )

    def test_each_email_is_addressed_to_one_person(self):
        self.send()

        # A shared To: line would show every recipient the whole list.
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)

    def test_the_subject_is_the_title_and_the_body_is_the_body(self):
        self.send()

        message = mail.outbox[0]
        self.assertEqual(message.subject, 'The library is open')
        self.assertIn('First paragraph.', message.body)
        self.assertIn('Second paragraph.', message.body)

    def test_push_only_writes_no_email(self):
        Broadcast.objects.filter(pk=self.row.pk).update(
            channel=Broadcast.Channel.PUSH,
        )
        self.row.refresh_from_db()

        self.send()

        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(mail.outbox, [])

    def test_email_only_files_nothing_in_the_feed(self):
        Broadcast.objects.filter(pk=self.row.pk).update(
            channel=Broadcast.Channel.EMAIL,
        )
        self.row.refresh_from_db()

        self.send()

        self.assertFalse(Notification.objects.exists())
        self.assertEqual(len(mail.outbox), 2)

    def test_it_records_what_happened(self):
        self.send()
        self.row.refresh_from_db()

        self.assertEqual(self.row.state, Broadcast.State.SENT)
        self.assertEqual(self.row.delivered, 2)
        self.assertEqual(self.row.failed, 0)
        self.assertIsNotNone(self.row.sent_at)

    def test_one_bad_address_does_not_stop_the_rest(self):
        # A broadcast that stops at the first failure has done more harm
        # than one that delivers the other 3,999 and says so.
        real = sending.EmailMultiAlternatives
        sent = []

        def flaky(*args, **kwargs):
            message = real(*args, **kwargs)
            if message.to == ['ada@example.com']:
                message.send = mock.Mock(side_effect=RuntimeError('refused'))
            else:
                sent.append(message.to[0])
            return message

        Broadcast.objects.filter(pk=self.row.pk).update(
            channel=Broadcast.Channel.EMAIL,
        )
        self.row.refresh_from_db()
        with mock.patch.object(sending, 'EmailMultiAlternatives', flaky):
            self.send()

        self.row.refresh_from_db()
        self.assertEqual(sent, ['bola@example.com'])
        self.assertEqual(self.row.delivered, 1)
        self.assertEqual(self.row.failed, 1)

    def test_a_row_that_is_not_going_out_is_not_sent(self):
        Broadcast.objects.filter(pk=self.row.pk).update(
            state=Broadcast.State.SENT,
        )
        self.row.refresh_from_db()

        self.assertEqual(self.send(), 0)
        self.assertEqual(mail.outbox, [])
