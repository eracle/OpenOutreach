"""Wipe PENDING Task rows left over from the pre-lazy scheduler.

Old rows carry ``public_id`` in their payload and ``scheduled_at`` values
from the previous run — if the daemon was offline for a while, they all
become "ready" at once and burst-fire under the new lazy semantics. Drop
them so the new planner builds a fresh 24h Poisson-spaced window on the
next reconcile cycle. RUNNING/COMPLETED/FAILED rows are kept as history.
"""
from django.db import migrations


def drop_pending_tasks(apps, schema_editor):
    Task = apps.get_model("legacy", "Task")
    Task.objects.filter(status="pending").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("legacy", "0008_drop_connect_weekly_limit"),
    ]

    operations = [
        migrations.RunPython(drop_pending_tasks, migrations.RunPython.noop),
    ]
