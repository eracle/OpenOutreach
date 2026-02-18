# linkedin/migrations/0003_unify_campaigns.py
"""Add is_promo + action_fraction to Campaign, remove LinkedInProfile.campaign FK.

Data migration: ensures each LinkedInProfile's user belongs to the department
group of their campaign before dropping the FK.
"""
from django.db import migrations, models


def _backfill_groups(apps, schema_editor):
    """For each LinkedInProfile, add user to the department group of their campaign."""
    LinkedInProfile = apps.get_model("linkedin", "LinkedInProfile")
    for lp in LinkedInProfile.objects.select_related("campaign__department", "user").all():
        dept = lp.campaign.department
        if dept not in lp.user.groups.all():
            lp.user.groups.add(dept)


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0002_search_keyword"),
    ]

    operations = [
        # 1. Add new Campaign fields
        migrations.AddField(
            model_name="campaign",
            name="is_promo",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="campaign",
            name="action_fraction",
            field=models.FloatField(default=0.0),
        ),
        # 2. Data migration: backfill user groups from the FK
        migrations.RunPython(_backfill_groups, migrations.RunPython.noop),
        # 3. Remove the FK
        migrations.RemoveField(
            model_name="linkedinprofile",
            name="campaign",
        ),
    ]
