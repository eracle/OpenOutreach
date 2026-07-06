# Email-first pivot — drop the LinkedIn channel models, merged into one.
# Removes the FKs/index and deletes LinkedInProfile / SearchKeyword / ActionLog.
# The legacy app stays as a model-less migration-history anchor.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("legacy", "0010_move_engine_models_to_core"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="actionlog",
            name="linkedin_ac_linkedi_37318d_idx",
        ),
        migrations.RemoveField(
            model_name="linkedinprofile",
            name="self_lead",
        ),
        migrations.RemoveField(
            model_name="linkedinprofile",
            name="user",
        ),
        migrations.RemoveField(
            model_name="actionlog",
            name="campaign",
        ),
        migrations.RemoveField(
            model_name="actionlog",
            name="linkedin_profile",
        ),
        migrations.DeleteModel(
            name="SearchKeyword",
        ),
        migrations.DeleteModel(
            name="ActionLog",
        ),
        migrations.DeleteModel(
            name="LinkedInProfile",
        ),
    ]
