"""An application can name more than one discipline, and payouts moved to Bachs.

Hand-edited from the generated version, which removed `field` and
`recipient_code` before adding their replacements -- which would have thrown
away every application already submitted. The columns are added first, the
values carried across, and only then are the old ones dropped.
"""

from django.db import migrations, models


def carry_values_across(apps, schema_editor):
    Application = apps.get_model('experts', 'Application')
    PayoutMethod = apps.get_model('experts', 'PayoutMethod')

    for application in Application.objects.exclude(field=''):
        # One discipline becomes a list of one. Nobody loses their place in
        # the queue because the form grew a checkbox.
        application.disciplines = [application.field]
        application.save(update_fields=['disciplines'])

    # Paystack's recipient codes are no use to Bachs, but a destination that
    # exists is better than a blank: payouts check the id against Bachs and
    # fail loudly rather than paying the wrong account.
    for method in PayoutMethod.objects.exclude(recipient_code=''):
        method.destination_id = method.recipient_code
        method.save(update_fields=['destination_id'])


def put_them_back(apps, schema_editor):
    Application = apps.get_model('experts', 'Application')
    PayoutMethod = apps.get_model('experts', 'PayoutMethod')

    for application in Application.objects.all():
        chosen = application.disciplines or []
        # Reversing loses the rest; the first is the one the single column
        # could ever have held.
        application.field = chosen[0] if chosen else ''
        application.save(update_fields=['field'])

    for method in PayoutMethod.objects.exclude(destination_id=''):
        method.recipient_code = method.destination_id
        method.save(update_fields=['recipient_code'])


class Migration(migrations.Migration):

    dependencies = [
        ('experts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='application',
            name='disciplines',
            field=models.JSONField(default=list, verbose_name='practises'),
        ),
        migrations.AddField(
            model_name='application',
            name='insurance_provider',
            field=models.CharField(blank=True, max_length=120, verbose_name='insurance'),
        ),
        migrations.AddField(
            model_name='application',
            name='licence_number',
            field=models.CharField(blank=True, max_length=80, verbose_name='licence number'),
        ),
        migrations.AddField(
            model_name='application',
            name='portfolio_url',
            field=models.URLField(blank=True, verbose_name='portfolio or website'),
        ),
        migrations.AddField(
            model_name='payoutmethod',
            name='destination_id',
            field=models.CharField(blank=True, max_length=80, verbose_name='Bachs destination'),
        ),
        migrations.RunPython(carry_values_across, put_them_back),
        migrations.RemoveField(
            model_name='application',
            name='field',
        ),
        migrations.RemoveField(
            model_name='payoutmethod',
            name='recipient_code',
        ),
        migrations.AlterField(
            model_name='payoutmethod',
            name='kind',
            field=models.CharField(
                choices=[
                    ('bank', 'Bank Transfer'),
                    ('card', 'Card'),
                    ('mobile', 'Mobile Money (USSD)'),
                ],
                default='bank', max_length=10, verbose_name='kind',
            ),
        ),
    ]
