"""The Celery app, used on AWS.

Imported defensively by soothifyAfrica/__init__.py and by api/jobs.py: on a
machine with no broker configured, or without Celery installed at all, nothing
here is loaded and background work runs inline instead.

Run the pieces on AWS with:

    celery -A soothifyAfrica worker -l info
    celery -A soothifyAfrica beat -l info      # the scheduled jobs below
"""

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'soothifyAfrica.settings')

app = Celery('soothify')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Scheduled work. Each of these is also reachable as a management command
# (`python manage.py run_due_jobs`), so a deployment without beat -- cPanel --
# can run the same things from a single daily cron entry.
app.conf.beat_schedule = {
    # Bachs charges trials and renewals itself. This only catches a webhook
    # that never arrived, by asking them what the truth is.
    'reconcile-subscriptions': {
        'task': 'billing.tasks.reconcile_subscriptions',
        'schedule': crontab(hour=6, minute=0),
    },
    # Reminders go out the morning of a session, to whoever asked for them.
    'session-reminders': {
        'task': 'experts.tasks.send_session_reminders',
        'schedule': crontab(hour=7, minute=0),
    },
    # Session audio is deleted after 30 days. This is what makes that true,
    # so it runs every day whatever else is happening.
    'purge-recordings': {
        'task': 'recordings.tasks.purge_old_recordings',
        'schedule': crontab(hour=3, minute=30),
    },
}
app.conf.timezone = 'Africa/Lagos'
