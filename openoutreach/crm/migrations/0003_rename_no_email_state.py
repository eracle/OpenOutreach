"""Retire the vendor-named enrichment-miss state.

``DealState`` stores its labels, so the rename is a data migration and not just a
``choices`` edit: every existing row still carries the literal
``"No Email (BetterContact)"``, and without this it would sit outside the enum —
invisible to ``status``, to the export filters and to the ML labeler, all of which
match on the value. Reversible, because a downgrade has to be able to read its own
rows back.
"""
from django.db import migrations

OLD = "No Email (BetterContact)"
NEW = "No Email Found"


def _rewrite(apps, schema_editor, old, new):
    Deal = apps.get_model("crm", "Deal")
    Deal.objects.filter(state=old).update(state=new)


def forwards(apps, schema_editor):
    _rewrite(apps, schema_editor, OLD, NEW)


def backwards(apps, schema_editor):
    _rewrite(apps, schema_editor, NEW, OLD)


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0002_remove_deal_profile_summary"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
