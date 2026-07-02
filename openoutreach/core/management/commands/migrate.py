"""`migrate`, with a pre-flight reconciliation of renamed app labels.

The `linkedin` app was renamed to `legacy`; existing installs recorded its
migrations under the old label. Django validates migration-history consistency
*before* running any migration, so the fix can't be a migration file — it has
to run first. Overriding the command makes it fire on every `migrate` (direct
or via `rundaemon`'s `call_command`), so no manual SQL is ever needed.
"""
from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import DEFAULT_DB_ALIAS, connections

from openoutreach.core.migration_compat import reconcile_app_labels


class Command(MigrateCommand):
    def handle(self, *args, **options):
        connection = connections[options.get("database", DEFAULT_DB_ALIAS)]
        for note in reconcile_app_labels(connection):
            self.stdout.write(f"Reconciled renamed app label in django_migrations: {note}")
        super().handle(*args, **options)
