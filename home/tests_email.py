from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from home.models import WaitlistSignup
from home.tests_waitlist import PLAIN_TEST_SETTINGS

SIGNUP = {'name': 'Ada Obi', 'email': 'ada@example.com', 'phone': '08030000000'}


@override_settings(**PLAIN_TEST_SETTINGS)
class WelcomeEmailTests(TestCase):
    def join(self, **extra):
        return self.client.post(reverse('home:waitlist'), {**SIGNUP, **extra})

    def test_sent_on_signup_with_the_right_headers(self):
        self.join()
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, "Welcome to Soothify! You're officially on the waitlist")
        self.assertEqual(msg.to, ['ada@example.com'])
        self.assertEqual(msg.from_email, 'Rita from Soothify <noreply@soothifyafrica.com.ng>')
        self.assertEqual(msg.reply_to, ['support@soothifyafrica.com.ng'])

    def test_body_has_first_name_place_in_line_and_referral_link(self):
        self.join()
        row = WaitlistSignup.objects.get()
        text = mail.outbox[0].body
        html = mail.outbox[0].alternatives[0][0]
        for body in (text, html):
            self.assertIn('Hi Ada,', body.replace('Hi Ada,', 'Hi Ada,'))   # first name only
            self.assertNotIn('Ada Obi,', body)
            self.assertIn('#1', body)                                       # their place in line
            self.assertIn(f'?ref={row.referral_code}', body)                # their own link
            self.assertIn('Rita Peter', body)
        self.assertEqual(mail.outbox[0].alternatives[0][1], 'text/html')

    def test_place_in_line_is_the_real_one(self):
        for i in range(4):
            self.client.post(reverse('home:waitlist'), {**SIGNUP, 'email': f'p{i}@example.com'})
        self.assertIn('#4', mail.outbox[-1].body)

    def test_not_sent_again_when_someone_updates_their_answers(self):
        self.join()
        self.join(phone='08031111111')                       # same email, new details
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(WaitlistSignup.objects.count(), 1)

    def test_a_mail_failure_never_loses_the_signup(self):
        with patch('home.emails.EmailMultiAlternatives.send', side_effect=OSError('smtp down')):
            with self.assertLogs('home.emails', level='ERROR'):
                resp = self.join()
        self.assertRedirects(resp, reverse('home:waitlist_done'))
        self.assertEqual(WaitlistSignup.objects.count(), 1)   # still joined
        self.assertEqual(len(mail.outbox), 0)

    def test_nothing_sent_when_the_form_is_invalid(self):
        self.client.post(reverse('home:waitlist'), {'name': '', 'email': 'nope'})
        self.assertEqual(len(mail.outbox), 0)
