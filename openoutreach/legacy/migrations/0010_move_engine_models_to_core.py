# Move the engine models (SiteConfig, Campaign, Task) to the `core` app —
# step 2 of 2: repoint this app's FKs at core.Campaign and drop the moved
# models from linkedin's state. State-only (database_operations=[]): no
# tables are dropped and no data is touched — core.0001 already adopted
# the same models/tables, and core.0002 renames the tables.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("legacy", "0009_drop_legacy_pending_tasks"),
        ("core", "0001_initial"),
        ("crm", "0011_repoint_campaign_fk_to_core"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="searchkeyword",
                    name="campaign",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="search_keywords",
                        to="core.campaign",
                    ),
                ),
                migrations.AlterField(
                    model_name="actionlog",
                    name="campaign",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="action_logs",
                        to="core.campaign",
                    ),
                ),
                migrations.DeleteModel(name="Task"),
                migrations.DeleteModel(name="SiteConfig"),
                migrations.DeleteModel(name="Campaign"),
            ],
        ),
    ]
