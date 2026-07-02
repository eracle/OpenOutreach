"""Pre-``migrate`` reconciliation for app renames recorded in django_migrations.

When an app is renamed, existing installs still have its migrations recorded
under the old label. Django runs ``check_consistent_history()`` *before*
executing any migration, so it aborts on the label mismatch before a data
migration could fix it — the reconciliation therefore cannot live in a
migration file. It runs from the overridden ``migrate`` command instead
(``core/management/commands/migrate.py``), just before the real migrate.

Each entry is idempotent: a no-op on fresh installs (table absent / no rows)
and after the first run.
"""
from __future__ import annotations

# old app label -> new app label
_RENAMED_APPS = {
    "linkedin": "legacy",
}


def reconcile_app_labels(connection) -> list[str]:
    """Rewrite renamed app labels in django_migrations. Returns notes for logging."""
    if "django_migrations" not in connection.introspection.table_names():
        return []  # fresh DB, nothing recorded yet

    notes = []
    with connection.cursor() as cursor:
        for old_label, new_label in _RENAMED_APPS.items():
            cursor.execute(
                "UPDATE django_migrations SET app = %s WHERE app = %s",
                [new_label, old_label],
            )
            if cursor.rowcount:
                notes.append(f"{old_label} -> {new_label} ({cursor.rowcount} rows)")
    return notes
