# The one migration of the app-move that runs real SQL: rename the moved
# tables from their historical linkedin_* names to the core_* defaults.
# AlterModelTable(table=None) renames to the model's default table name,
# including auto-created M2M tables (campaign.users).
# The content-type remap keeps generic FKs and admin log entries valid and
# prevents the stale-content-type prompt on the next interactive migrate.
from django.db import migrations


def _remap_content_types(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.using(schema_editor.connection.alias).filter(
        app_label="legacy", model__in=["campaign", "siteconfig", "task"]
    ).update(app_label="core")


def _unmap_content_types(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.using(schema_editor.connection.alias).filter(
        app_label="core", model__in=["campaign", "siteconfig", "task"]
    ).update(app_label="legacy")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("legacy", "0010_move_engine_models_to_core"),
    ]

    operations = [
        migrations.AlterModelTable(name="siteconfig", table=None),
        migrations.AlterModelTable(name="task", table=None),
        migrations.AlterModelTable(name="campaign", table=None),
        migrations.RenameIndex(
            model_name="task",
            old_name="linkedin_ta_status_d04eec_idx",
            new_name="core_task_status_sched_idx",
        ),
        migrations.RunPython(_remap_content_types, _unmap_content_types),
    ]
