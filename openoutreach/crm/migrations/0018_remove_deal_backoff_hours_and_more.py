# Drop the LinkedIn connect leg from the funnel: re-activate any deal stranded at
# a connect-era state on the email funnel (→ Qualified), then remove those
# DealState values and the connect-only scheduling columns. Forward-only and
# data-preserving — no deal row is deleted, only re-stated.
from django.db import migrations, models

_CONNECT_STATES = ["Ready to Connect", "Pending", "Connected"]


def remap_connect_states(apps, schema_editor):
    """Send stranded LinkedIn deals back into the email funnel.

    A deal left at Ready to Connect / Pending / Connected is a qualified lead the
    connect leg never closed; the channel is gone, so re-state it to Qualified and
    the discover→qualify→find_email→email chain reaches it by email. Outcome and
    summaries are untouched, so no history is lost.
    """
    Deal = apps.get_model("crm", "Deal")
    Deal.objects.filter(state__in=_CONNECT_STATES).update(state="Qualified")


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0017_lead_reshape_profile_url'),
    ]

    operations = [
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
