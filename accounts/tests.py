"""What the Flutter app relies on at /api/v1/auth/.

Written against the app's own flows: SignupController's rules, SigninController
sending someone to the questionnaire or to the shell depending on
`KycRepository.isComplete()`, and KycRepository's answer shape.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse

from accounts.models import KycResponse, PasswordResetCode, Profile, Role
from accounts.social import SocialAuthError
from accounts.views import PasswordResetRequestView
from api.testing import ApiTestCase

GOOD_SIGNUP = {
    'full_name': 'Ada Obi',
    'email': 'Ada@Example.com',
    'password': 'Soothify2026',
}


class RegisterTests(ApiTestCase):
    url = None

    def setUp(self):
        super().setUp()
        self.url = reverse('api:accounts:register')

    def test_creates_account_profile_and_tokens(self):
        response = self.client.post(self.url, GOOD_SIGNUP, content_type='application/json')

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn('access', body['tokens'])
        self.assertIn('refresh', body['tokens'])
        self.assertEqual(body['profile']['full_name'], 'Ada Obi')
        self.assertEqual(body['profile']['role'], Role.USER)
        # A fresh account has not done the questionnaire, which is what sends
        # the app to KYC rather than the shell.
        self.assertFalse(body['profile']['kyc_complete'])

    def test_email_is_stored_lower_case(self):
        self.client.post(self.url, GOOD_SIGNUP, content_type='application/json')

        user = get_user_model().objects.get()
        self.assertEqual(user.email, 'ada@example.com')
        self.assertEqual(user.profile.full_name, 'Ada Obi')

    def test_second_signup_with_the_same_email_is_refused(self):
        self.client.post(self.url, GOOD_SIGNUP, content_type='application/json')

        response = self.client.post(
            self.url,
            dict(GOOD_SIGNUP, full_name='Someone Else'),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_short_password_is_refused(self):
        response = self.client.post(
            self.url, dict(GOOD_SIGNUP, password='Short1'), content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.json())

    def test_practitioner_can_register_as_one(self):
        response = self.client.post(
            self.url,
            dict(GOOD_SIGNUP, role='practitioner'),
            content_type='application/json',
        )

        self.assertEqual(response.json()['profile']['role'], 'practitioner')
        self.assertTrue(Profile.objects.get().is_practitioner)


class LoginTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.url = reverse('api:accounts:login')

    def test_signs_in_with_email_whatever_the_case(self):
        response = self.client.post(
            self.url,
            {'email': 'ADA@example.com', 'password': 'Soothify2026'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.json()['tokens'])

    def test_wrong_password_is_refused_without_saying_which_part_was_wrong(self):
        response = self.client.post(
            self.url,
            {'email': 'ada@example.com', 'password': 'not-the-password'},
            content_type='application/json',
        )
        unknown = self.client.post(
            self.url,
            {'email': 'nobody@example.com', 'password': 'Soothify2026'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), unknown.json())

    def test_says_whether_the_questionnaire_is_done(self):
        KycResponse.objects.create(user=self.profile.user).mark_complete()

        response = self.client.post(
            self.url,
            {'email': 'ada@example.com', 'password': 'Soothify2026'},
            content_type='application/json',
        )

        self.assertTrue(response.json()['profile']['kyc_complete'])


class MeTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.url = reverse('api:accounts:me')

    def auth(self):
        return self.signed_in_as('ada@example.com', 'Soothify2026')

    def test_needs_a_token(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_returns_the_signed_in_profile(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['email'], 'ada@example.com')

    def test_can_change_language_and_handle(self):
        response = self.client.patch(
            self.url,
            {'language': 'pcm', 'community_handle': 'quiet_storm'},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth(),
        )

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.language, 'pcm')
        self.assertEqual(self.profile.community_handle, 'quiet_storm')

    def test_role_cannot_be_changed_by_the_client(self):
        self.client.patch(
            self.url,
            {'role': 'practitioner'},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth(),
        )

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.role, Role.USER)


class KycTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.url = reverse('api:accounts:kyc')
        self.token = self.signed_in_as('ada@example.com', 'Soothify2026')

    def patch(self, answers):
        return self.client.patch(
            self.url,
            {'answers': answers},
            content_type='application/json',
            HTTP_AUTHORIZATION=self.token,
        )

    def test_starts_empty(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION=self.token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['answers'], {})
        self.assertFalse(response.json()['is_complete'])

    def test_saves_single_and_multi_choice_answers(self):
        response = self.patch({'gender': ['female'], 'brings_you': ['stress', 'sleep']})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()['answers'],
            {'gender': ['female'], 'brings_you': ['stress', 'sleep']},
        )

    def test_later_answers_do_not_wipe_earlier_ones(self):
        self.patch({'gender': ['female']})

        response = self.patch({'age_band': ['1998']})

        self.assertEqual(
            response.json()['answers'], {'gender': ['female'], 'age_band': ['1998']},
        )

    def test_a_repeated_answer_is_stored_once(self):
        response = self.patch({'brings_you': ['stress', 'stress', 'sleep']})

        self.assertEqual(response.json()['answers']['brings_you'], ['stress', 'sleep'])

    def test_rejects_answers_that_are_not_option_values(self):
        response = self.patch({'gender': [{'label': 'female'}]})

        self.assertEqual(response.status_code, 400)

    def test_completing_it_is_what_the_app_reads_on_next_launch(self):
        self.patch({'gender': ['female']})

        done = self.client.post(
            reverse('api:accounts:kyc-complete'), HTTP_AUTHORIZATION=self.token,
        )

        self.assertTrue(done.json()['is_complete'])
        self.assertTrue(KycResponse.objects.get().is_complete)

    def test_one_persons_answers_are_not_anothers(self):
        self.patch({'gender': ['female']})
        other = Profile.create_user(
            email='bola@example.com', password='Soothify2026', full_name='Bola A',
        )
        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION=self.signed_in_as('bola@example.com', 'Soothify2026'),
        )

        self.assertEqual(response.json()['answers'], {})
        self.assertEqual(KycResponse.objects.filter(user=other.user).count(), 1)


class HealthTests(ApiTestCase):
    def test_health_is_open_and_names_the_deployment(self):
        response = self.client.get(reverse('api:health'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertIn(response.json()['deployment'], ('cpanel', 'aws'))


class SocialSignInTests(ApiTestCase):
    """Google and Apple, which the app's two auth screens both offer.

    The providers' verification is patched out -- reaching Google and Apple
    from a test would make these flaky and slow, and what is worth pinning is
    what this codebase does with a verified identity, not that google-auth
    can check a signature.
    """

    def setUp(self):
        super().setUp()
        self.google_url = reverse('api:accounts:google')
        self.apple_url = reverse('api:accounts:apple')

    def test_google_creates_an_account_and_returns_a_session(self):
        with patch(
            'accounts.views.verify_google', return_value=('ada@example.com', 'Ada Obi'),
        ):
            response = self.client.post(
                self.google_url, {'id_token': 'pretend'}, format='json',
            )

        self.assertEqual(response.status_code, 200)
        # The same shape as a password login, so the app routes identically.
        self.assertIn('access', response.data['tokens'])
        self.assertEqual(response.data['profile']['full_name'], 'Ada Obi')

        user = get_user_model().objects.get(email='ada@example.com')
        # Provider-only account: there is no password to guess.
        self.assertFalse(user.has_usable_password())

    def test_google_signs_in_to_an_account_that_registered_with_a_password(self):
        """One address, one account.

        Registering with a password and later pressing "Continue with Google"
        is the same person; a second account would strand their history.
        """
        Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )

        with patch(
            'accounts.views.verify_google', return_value=('ADA@example.com', 'Ada'),
        ):
            response = self.client.post(
                self.google_url, {'id_token': 'pretend'}, format='json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_user_model().objects.filter(email='ada@example.com').count(), 1)
        # And the password still works afterwards.
        self.assertTrue(
            get_user_model().objects.get(email='ada@example.com').has_usable_password(),
        )

    def test_apple_takes_the_name_only_when_the_account_is_new(self):
        """Apple sends the name once, on the first authorisation, never again.

        So a later sign-in posting a blank name must not wipe what is stored.
        """
        with patch('accounts.views.verify_apple', return_value=('ada@icloud.com', 'sub-1')):
            first = self.client.post(
                self.apple_url,
                {'identity_token': 'pretend', 'full_name': 'Ada Obi'},
                format='json',
            )
            second = self.client.post(
                self.apple_url, {'identity_token': 'pretend'}, format='json',
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['profile']['full_name'], 'Ada Obi')

    def test_a_missing_token_is_a_400_not_a_crash(self):
        self.assertEqual(
            self.client.post(self.google_url, {}, format='json').status_code, 400,
        )
        self.assertEqual(
            self.client.post(self.apple_url, {}, format='json').status_code, 400,
        )

    def test_a_rejected_token_never_reaches_the_database(self):
        with patch(
            'accounts.views.verify_google',
            side_effect=SocialAuthError({'detail': 'nope'}),
        ):
            response = self.client.post(
                self.google_url, {'id_token': 'forged'}, format='json',
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 0)


class PasswordResetTests(ApiTestCase):
    """"Forgot password", which the app's sign-in screen offers.

    Two endpoints: ask for a code, then send the code and a new password.
    What matters here is that it cannot be used to find out who has an
    account, and that a code cannot be guessed.
    """

    def setUp(self):
        super().setUp()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.ask_url = reverse('api:accounts:password-reset')
        self.confirm_url = reverse('api:accounts:password-reset-confirm')

    def ask(self, email='ada@example.com'):
        return self.client.post(
            self.ask_url, {'email': email}, content_type='application/json',
        )

    def confirm(self, code, password='NewSoothify2026', email='ada@example.com'):
        return self.client.post(
            self.confirm_url,
            {'email': email, 'code': code, 'password': password},
            content_type='application/json',
        )

    def sent_code(self):
        """The code as it was emailed, which is the only place it exists."""
        import re

        body = mail.outbox[-1].body
        return re.search(r'\b(\d{6})\b', body).group(1)

    def test_asking_sends_a_code(self):
        response = self.ask()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('reset code', mail.outbox[0].subject)
        self.assertEqual(len(self.sent_code()), 6)

    def test_an_unknown_address_gets_the_same_answer_and_no_email(self):
        """This endpoint must not say who has an account."""
        known = self.ask()
        unknown = self.ask('nobody@example.com')

        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(len(mail.outbox), 1)      # only the real one

    def test_the_code_is_not_stored_in_the_clear(self):
        self.ask()

        row = PasswordResetCode.objects.get()
        self.assertNotIn(self.sent_code(), row.code_hash)
        self.assertTrue(row.code_hash.startswith('pbkdf2_'))

    def test_the_code_changes_the_password_and_signs_them_in(self):
        self.ask()

        response = self.confirm(self.sent_code())

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.json()['tokens'])
        self.profile.user.refresh_from_db()
        self.assertTrue(self.profile.user.check_password('NewSoothify2026'))

    def test_the_old_password_stops_working(self):
        self.ask()
        self.confirm(self.sent_code())

        response = self.client.post(
            reverse('api:accounts:login'),
            {'email': 'ada@example.com', 'password': 'Soothify2026'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_a_code_cannot_be_used_twice(self):
        self.ask()
        code = self.sent_code()
        self.confirm(code)

        again = self.confirm(code, password='Another2026Pass')

        self.assertEqual(again.status_code, 400)
        self.profile.user.refresh_from_db()
        self.assertTrue(self.profile.user.check_password('NewSoothify2026'))

    def test_a_wrong_code_is_refused(self):
        self.ask()

        response = self.confirm('000000')

        self.assertEqual(response.status_code, 400)
        self.profile.user.refresh_from_db()
        self.assertTrue(self.profile.user.check_password('Soothify2026'))

    def test_guessing_runs_out_after_five_tries(self):
        self.ask()
        real = self.sent_code()
        for _ in range(5):
            self.confirm('000000')

        response = self.confirm(real)

        self.assertEqual(response.status_code, 400)

    def test_an_expired_code_is_refused(self):
        from django.utils import timezone

        self.ask()
        code = self.sent_code()
        PasswordResetCode.objects.update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        self.assertEqual(self.confirm(code).status_code, 400)

    def test_asking_again_retires_the_earlier_code(self):
        self.ask()
        first = self.sent_code()

        self.ask()

        self.assertEqual(self.confirm(first).status_code, 400)
        self.assertEqual(self.confirm(self.sent_code()).status_code, 200)

    def test_a_google_only_account_is_sent_nothing(self):
        """There is no password to reset, and a code would lead nowhere."""
        social = Profile.create_user(email='bola@example.com', password=None)
        social.user.set_unusable_password()
        social.user.save(update_fields=['password'])

        response = self.ask('bola@example.com')

        self.assertEqual(response.status_code, 200)    # same answer as always
        self.assertEqual(len(mail.outbox), 0)

    def test_a_short_password_is_refused(self):
        self.ask()

        response = self.confirm(self.sent_code(), password='Short1')

        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.json())


class PasswordResetTests(ApiTestCase):
    """"I have forgotten my password", which the app's sign-in screen offers.

    A code by email rather than a link: this is a phone app, and a link has
    to find its way back into it through a deep link that does not exist yet.
    """

    def setUp(self):
        super().setUp()
        self.profile = Profile.create_user(
            email='ada@example.com', password='Soothify2026', full_name='Ada Obi',
        )
        self.url = reverse('api:accounts:password-reset')
        self.confirm_url = reverse('api:accounts:password-reset-confirm')

    def ask(self, email='ada@example.com'):
        return self.client.post(
            self.url, {'email': email}, content_type='application/json',
        )

    def code_from_email(self):
        from django.core import mail

        body = mail.outbox[-1].body
        return ''.join(ch for ch in body if ch.isdigit())[:6]

    def confirm(self, code, password='BrandNew2026', email='ada@example.com'):
        return self.client.post(
            self.confirm_url,
            {'email': email, 'code': code, 'password': password},
            content_type='application/json',
        )

    def test_asking_sends_a_code(self):
        from django.core import mail

        response = self.ask()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('reset code', mail.outbox[0].subject.lower())
        self.assertEqual(len(self.code_from_email()), 6)

    def test_the_code_is_not_stored_in_the_clear(self):
        self.ask()

        code = self.code_from_email()
        stored = PasswordResetCode.objects.get()
        self.assertNotIn(code, stored.code_hash)
        self.assertTrue(stored.matches(code))

    def test_an_unknown_address_answers_exactly_the_same(self):
        """Otherwise this endpoint tells strangers who has an account."""
        known = self.ask()
        unknown = self.ask('nobody@example.com')

        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.json(), unknown.json())

    def test_the_code_changes_the_password_and_signs_them_in(self):
        self.ask()

        response = self.confirm(self.code_from_email())

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.json()['tokens'])
        self.assertTrue(
            self.client.login(username='ada@example.com', password='BrandNew2026'),
        )

    def test_the_old_password_stops_working(self):
        self.ask()
        self.confirm(self.code_from_email())

        response = self.client.post(
            reverse('api:accounts:login'),
            {'email': 'ada@example.com', 'password': 'Soothify2026'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_a_code_works_once(self):
        self.ask()
        code = self.code_from_email()
        self.confirm(code)

        again = self.confirm(code, password='AnotherOne2026')

        self.assertEqual(again.status_code, 400)

    def test_a_wrong_code_is_refused_and_counted(self):
        self.ask()

        response = self.confirm('000000')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(PasswordResetCode.objects.get().attempts, 1)

    def test_guessing_is_cut_off_after_five_tries(self):
        self.ask()
        real = self.code_from_email()
        for _ in range(PasswordResetCode.MAX_ATTEMPTS):
            self.confirm('000000')

        response = self.confirm(real)

        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            self.client.login(username='ada@example.com', password='Soothify2026'),
        )

    def test_an_expired_code_is_refused(self):
        from django.utils import timezone

        self.ask()
        code = self.code_from_email()
        PasswordResetCode.objects.update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        self.assertEqual(self.confirm(code).status_code, 400)

    def test_asking_again_retires_the_first_code(self):
        self.ask()
        first = self.code_from_email()
        self.ask()
        second = self.code_from_email()

        self.assertEqual(self.confirm(first).status_code, 400)
        self.assertEqual(self.confirm(second).status_code, 200)

    def test_a_weak_new_password_is_refused(self):
        self.ask()

        response = self.confirm(self.code_from_email(), password='short')

        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.json())

    def test_a_google_only_account_is_sent_no_code(self):
        """There is no password to reset, and a code would lead nowhere."""
        from django.core import mail

        social = Profile.create_user(email='bola@example.com', password=None)
        social.user.set_unusable_password()
        social.user.save(update_fields=['password'])

        response = self.ask('bola@example.com')

        self.assertEqual(response.status_code, 200)       # says nothing different
        self.assertEqual(len(mail.outbox), 0)

    def test_a_dead_mail_server_does_not_leak_that_the_account_exists(self):
        from unittest.mock import patch

        with patch('accounts.emails.EmailMultiAlternatives.send', side_effect=OSError('down')):
            response = self.ask()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), PasswordResetRequestView.ANSWER)
