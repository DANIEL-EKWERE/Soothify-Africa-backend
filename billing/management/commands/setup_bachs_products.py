"""Create the recurring products at Bachs that the plans sell.

A plan cannot be subscribed to until it has one: the product carries the
price, the billing cycle and the trial period, and a checkout for it is what
starts a subscription.

    python manage.py setup_bachs_products          # create what is missing
    python manage.py setup_bachs_products --dry-run
"""

from django.core.management.base import BaseCommand

from billing import bachs
from billing.models import Plan


class Command(BaseCommand):
    help = 'Create a Bachs product for every priced plan that lacks one.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Say what would be created, without creating it.',
        )

    def handle(self, *args, **options):
        if not bachs.configured():
            self.stderr.write('BACHS_SECRET_KEY is not set.')
            return

        self.stdout.write('Using {0}'.format(bachs.base_url()))
        pending = Plan.objects.filter(
            is_active=True, bachs_product_id='',
        ).exclude(price_naira=None)

        if not pending:
            self.stdout.write('Every priced plan already has a product.')
            return

        for plan in pending:
            interval = 'year' if plan.cadence == 'annual' else 'month'
            line = '{0}: NGN {1} / {2}, {3}-day trial'.format(
                plan.name, plan.price_naira, interval, plan.trial_days,
            )
            if options['dry_run']:
                self.stdout.write('would create -- {0}'.format(line))
                continue

            try:
                product = bachs.create_product(
                    name=plan.name,
                    naira=plan.price_naira,
                    interval=interval if plan.cadence else None,
                    trial_days=plan.trial_days,
                )
            except bachs.BachsError as failure:
                self.stderr.write('{0}: {1}'.format(plan.key, failure))
                continue

            plan.bachs_product_id = product.get('product_id') or product.get('id', '')
            plan.save(update_fields=['bachs_product_id'])
            self.stdout.write('created {0} -- {1}'.format(plan.bachs_product_id, line))
