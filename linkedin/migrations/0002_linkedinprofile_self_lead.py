import django.db.models.deletion
from django.db import migrations, models

ME_URL = "https://www.linkedin.com/in/me/"


def delete_me_markers(apps, schema_editor):
    Lead = apps.get_model("crm", "Lead")
    Lead.objects.filter(linkedin_url=ME_URL).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0001_initial"),
        ("crm", "0003_public_identifier_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="linkedinprofile",
            name="self_lead",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="crm.lead",
                related_name="+",
            ),
        ),
        migrations.RunPython(delete_me_markers, migrations.RunPython.noop),
    ]
