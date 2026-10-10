"""Everything that should happen on a schedule, in one command.

On AWS, Celery beat calls the same functions and this is a convenience. On a
host without a worker, one cron entry runs the lot:

    0 6 * * *  /path/to/venv/bin/python /path/to/manage.py run_due_jobs
"""

from django.core.management.base import BaseCommand

from billing.tasks import charge_due_subscriptions
from experts.tasks import release_expired_holds, send_session_reminders
from recordings.tasks import purge_old_recordings


class Command(BaseCommand):
    help = 'Run the scheduled billing and session jobs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--only',
            choices=['charges', 'reminders', 'holds', 'recordings'],
            help='Run just one of them.',
        )

    def handle(self, *args, **options):
        only = options.get('only')
        ran = []

        if only in (None, 'holds'):
            ran.append(('released holds', release_expired_holds()))
        if only in (None, 'charges'):
            ran.append(('charged memberships', charge_due_subscriptions()))
        if only in (None, 'reminders'):
            ran.append(('session reminders', send_session_reminders()))
        if only in (None, 'recordings'):
            ran.append(('purged recordings', purge_old_recordings()))

        for label, count in ran:
            self.stdout.write('{0}: {1}'.format(label, count))
