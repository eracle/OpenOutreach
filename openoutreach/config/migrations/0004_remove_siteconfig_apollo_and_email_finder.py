from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('openoutreach_config', '0003_remove_siteconfig_accepted_legal_notice'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='siteconfig',
            name='apollo_api_key',
        ),
        migrations.RemoveField(
            model_name='siteconfig',
            name='email_finder',
        ),
    ]
