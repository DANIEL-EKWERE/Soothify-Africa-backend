"""What the Flutter app relies on at /api/v1/auth/.

Written against the app's own flows: SignupController's rules, SigninController
sending someone to the questionnaire or to the shell depending on
`KycRepository.isComplete()`, and KycRepository's answer shape.
"""

from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import KycResponse, Profile, Role
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
