from django.test import TestCase
from django.urls import reverse

from home.forms import WaitlistForm
from home.models import REFERRAL_BOOST, WaitlistSignup

GOOD = {'name': 'Ada Obi', 'email': 'Ada@Example.com', 'phone': '08030000000',
        'goals': ['stress', 'sleep'], 'interests': ['yoga'], 'source': 'social',
        'wants_updates': 'on'}


def signup(**overrides):
    form = WaitlistForm({**GOOD, **overrides})
    assert form.is_valid(), form.errors
    return form.save()


class WaitlistFormTests(TestCase):
    def test_saves_every_answer(self):
        row = signup()
        self.assertEqual(row.email, 'ada@example.com')           # stored lower-case
        self.assertEqual(row.goals, ['stress', 'sleep'])
        self.assertEqual(row.interests, ['yoga'])
        self.assertEqual(row.source, 'social')
        self.assertTrue(row.wants_updates)
        self.assertEqual(len(row.referral_code), 8)

    def test_only_name_and_email_are_required(self):
        self.assertTrue(WaitlistForm({'name': 'Ada', 'email': 'a@b.com'}).is_valid())
        bad = WaitlistForm({'name': '', 'email': 'not-an-email'})
        self.assertFalse(bad.is_valid())
        self.assertEqual(set(bad.errors), {'name', 'email'})

    def test_typing_an_other_answer_ticks_other(self):
        row = signup(goals=['stress'], goals_other='Grief')
        self.assertIn('other', row.goals)
        self.assertEqual(row.goals_other, 'Grief')

    def test_same_email_updates_but_keeps_place_and_link(self):
        first = signup()
        again = signup(name='Adaeze Obi', email='ADA@example.com')
        self.assertEqual(WaitlistSignup.objects.count(), 1)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(again.name, 'Adaeze Obi')
        self.assertEqual(again.referral_code, first.referral_code)


class PlaceInLineTests(TestCase):
    def test_referrals_move_you_up(self):
        people = [signup(email=f'p{i}@x.com') for i in range(30)]
        me = people[-1]
        self.assertEqual(me.position(), 30)
        friend = WaitlistForm({**GOOD, 'email': 'friend@x.com'})
        self.assertTrue(friend.is_valid())
        friend.save(referral_code=me.referral_code)
        self.assertEqual(me.position(), 30 - REFERRAL_BOOST)

    def test_position_never_below_one(self):
        me = signup(email='me@x.com')
        for i in range(3):
            f = WaitlistForm({**GOOD, 'email': f'f{i}@x.com'}); f.is_valid(); f.save(referral_code=me.referral_code)
        self.assertEqual(me.position(), 1)

    def test_cannot_refer_yourself(self):
        me = signup(email='me@x.com')
        again = WaitlistForm({**GOOD, 'email': 'me@x.com'}); again.is_valid()
        again.save(referral_code=me.referral_code)
        self.assertIsNone(WaitlistSignup.objects.get(email='me@x.com').referred_by)


class WaitlistPageTests(TestCase):
    def test_form_then_success_page(self):
        self.assertEqual(self.client.get(reverse('home:waitlist')).status_code, 200)
        resp = self.client.post(reverse('home:waitlist'), GOOD)
        self.assertRedirects(resp, reverse('home:waitlist_done'))
        done = self.client.get(reverse('home:waitlist_done'))
        self.assertContains(done, '#1')
        self.assertContains(done, '?ref=' + WaitlistSignup.objects.get().referral_code)

    def test_referral_link_is_credited(self):
        me = signup(email='me@x.com')
        self.client.get(reverse('home:waitlist') + '?ref=' + me.referral_code)
        self.client.post(reverse('home:waitlist'), {**GOOD, 'email': 'friend@x.com'})
        self.assertEqual(WaitlistSignup.objects.get(email='friend@x.com').referred_by, me)

    def test_success_page_needs_a_signup(self):
        self.assertRedirects(self.client.get(reverse('home:waitlist_done')), reverse('home:waitlist'))

    def test_invalid_post_shows_errors(self):
        resp = self.client.post(reverse('home:waitlist'), {'name': '', 'email': 'nope'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(WaitlistSignup.objects.count(), 0)
        self.assertContains(resp, 'wl-field__error')

    def test_buttons_point_at_the_waitlist_page(self):
        home = self.client.get('/')
        self.assertContains(home, f'href="{reverse("home:waitlist")}"')
        self.assertNotContains(home, 'forms.gle')
