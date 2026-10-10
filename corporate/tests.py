"""The Corporate enquiry form, reached from "Speak with Corporate Team"."""

from django.core import mail
from django.urls import reverse

from api.testing import ApiTestCase
from corporate.models import Enquiry

GOOD = {
    'company': 'Landmark Enterprise',
    'contact_name': 'Mr Felix',
    'email': 'felix@landmark.example',
    'phone': '+2348030000000',
    'employees': '15',
    'services': 'Group Therapy, Mindfulness Exercises',
    'notes': 'Thank you for your service',
}


class EnquiryTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('api:corporate:enquiries')

    def send(self, **overrides):
        payload = dict(GOOD)
        payload.update(overrides)
        return self.client.post(self.url, payload, content_type='application/json')

    def test_a_company_can_send_one_without_an_account(self):
        response = self.send()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Enquiry.objects.count(), 1)
        self.assertEqual(Enquiry.objects.get().status, 'new')

    def test_the_team_is_told_and_can_reply_to_the_company(self):
        self.send()

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Landmark Enterprise', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].reply_to, ['felix@landmark.example'])

    def test_the_notice_carries_what_was_asked(self):
        self.send()

        body = mail.outbox[0].body
        self.assertIn('Mr Felix', body)
        self.assertIn('Group Therapy', body)
        self.assertIn('Thank you for your service', body)

    def test_the_four_needed_fields_are_required(self):
        for field in ('company', 'contact_name', 'email', 'phone'):
            response = self.send(**{field: '   '})

            self.assertEqual(response.status_code, 400, msg=field)
            self.assertIn(field, response.json(), msg=field)

    def test_the_optional_fields_can_be_left_empty(self):
        response = self.send(employees='', services='', notes='')

        self.assertEqual(response.status_code, 201)

    def test_a_dead_mail_server_does_not_lose_the_enquiry(self):
        from unittest.mock import patch

        with patch('corporate.emails.EmailMessage.send', side_effect=OSError('down')):
            response = self.send()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Enquiry.objects.count(), 1)
