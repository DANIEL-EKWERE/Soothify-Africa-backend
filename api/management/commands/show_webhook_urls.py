"""Print what to paste into Bachs and ZEGOCLOUD.

These differ between deployments -- the site's own domain on cPanel, the api
subdomain on AWS -- and a wrong one fails silently: payments simply never
confirm. Asking the running app removes the guesswork.
"""

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Show the webhook URLs this deployment expects.'

    def handle(self, *args, **options):
        self.stdout.write('Deployment : {0}'.format(settings.DEPLOY_TARGET))
        self.stdout.write('API base   : {0}/api/v1/'.format(settings.API_URL))
        self.stdout.write('')
        self.stdout.write('Bachs webhook      : {0}'.format(settings.BACHS_WEBHOOK_URL))
        self.stdout.write('  events: checkout.completed, collection.succeeded, invoice.paid,')
        self.stdout.write('          invoice.payment_failed, customer.subscription.created,')
        self.stdout.write('          customer.subscription.updated, customer.subscription.deleted')
        self.stdout.write('')
        self.stdout.write('ZEGOCLOUD callback : {0}'.format(settings.ZEGO_CALLBACK_URL))
        self.stdout.write('')

        missing = [
            name for name in (
                'BACHS_SECRET_KEY', 'BACHS_WEBHOOK_SECRET',
                'ZEGO_APP_ID', 'ZEGO_SERVER_SECRET',
            )
            if not getattr(settings, name, '')
        ]
        if missing:
            self.stdout.write(self.style.WARNING(
                'Not set yet: {0}'.format(', '.join(missing)),
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Every credential is set.'))
