# Email-first pivot — Lead/Deal reshape, merged into one migration.
# Adds the email-routing columns + Lead.country_code/profile_text, renames the
# Lead identity fields (linkedin_url→profile_url, api_email→email) rename-preserving
# (NOT drop+add — profile_url inherits the populated column), drops the connect-leg
# columns, and re-states any deal stranded at a connect-era state back to Qualified.
import django.db.models.deletion
from django.db import migrations, models

_CONNECT_STATES = ["Ready to Connect", "Pending", "Connected"]


def remap_connect_states(apps, schema_editor):
    """Send stranded LinkedIn deals back into the email funnel (→ Qualified)."""
    Deal = apps.get_model("crm", "Deal")
    Deal.objects.filter(state__in=_CONNECT_STATES).update(state="Qualified")


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0013_alter_deal_state'),
        ('emails', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='deal',
            name='email_subject',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='deal',
            name='email_sent_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='deal',
            name='email_message_id',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='deal',
            name='mailbox',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='deals', to='emails.mailbox'),
        ),
        migrations.AddField(
            model_name='lead',
            name='country_code',
            field=models.CharField(blank=True, default='', max_length=2),
        ),
        migrations.AddField(
            model_name='deal',
            name='next_follow_up_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name='deal',
            name='state',
            field=models.CharField(choices=[('Qualified', 'Qualified'), ('Ready to Find Email', 'Ready To Find Email'), ('Ready to Email', 'Ready To Email'), ('Emailed', 'Emailed'), ('Ready to Connect', 'Ready To Connect'), ('Pending', 'Pending'), ('Connected', 'Connected'), ('Completed', 'Completed'), ('Failed', 'Failed')], default='Qualified', max_length=20),
        ),
        migrations.RenameField(
            model_name='lead',
            old_name='linkedin_url',
            new_name='profile_url',
        ),
        migrations.RenameField(
            model_name='lead',
            old_name='api_email',
            new_name='email',
        ),
        migrations.AddField(
            model_name='lead',
            name='profile_text',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.RemoveField(
            model_name='lead',
            name='public_identifier',
        ),
        migrations.RemoveField(
            model_name='lead',
            name='urn',
        ),
        migrations.RemoveField(
            model_name='lead',
            name='contact_info',
        ),
        migrations.RunPython(remap_connect_states, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='deal',
            name='backoff_hours',
        ),
        migrations.RemoveField(
            model_name='deal',
            name='connect_attempts',
        ),
        migrations.RemoveField(
            model_name='deal',
            name='next_check_pending_at',
        ),
        migrations.AlterField(
            model_name='deal',
            name='state',
            field=models.CharField(choices=[('Qualified', 'Qualified'), ('Ready to Find Email', 'Ready To Find Email'), ('Ready to Email', 'Ready To Email'), ('Emailed', 'Emailed'), ('Completed', 'Completed'), ('Failed', 'Failed')], default='Qualified', max_length=20),
        ),
    ]
