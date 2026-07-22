# openoutreach/core/migrations/0011_collapse_prescreen_empties.py
"""Collapse band-bundled pre-screen empties to singletons.

Pre-screen used to record a dead value together with the headcount band — a 3-clause
``{min, max, value}`` set. The backoff generalizer (``select._generalizations``) then
split it into n−1 children, resurrecting the pruned value into a candidate whose family
the pool no longer holds and crashing the ranker (``KeyError``). A pre-screen empty is a
size-1 set of the value alone; rewrite the stale rows so they generate no children.
"""
import hashlib
import json

from django.db import migrations

_HEADCOUNT = {"company_headcount_min", "company_headcount_max"}


def _clause_key(pairs):
    """sha256 of the canonicalized clause set — mirrors ``select.clause_key``."""
    canonical = json.dumps(sorted(pairs), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def collapse_prescreen_empties(apps, schema_editor):
    EmptyClauseSet = apps.get_model("core", "EmptyClauseSet")
    Clause = apps.get_model("core", "Clause")

    for empty in EmptyClauseSet.objects.all():
        pairs = sorted(empty.clauses.values_list("family", "value"))
        values = [p for p in pairs if p[0] not in _HEADCOUNT]
        # Only a band-bundled singleton (band + exactly one value) collapses; a genuine
        # multi-value empty maximal keeps its full clause set.
        if len(pairs) == len(values) or len(values) != 1:
            continue

        new_key = _clause_key(values)
        dup = EmptyClauseSet.objects.filter(clause_key=new_key).exclude(pk=empty.pk).first()
        if dup:
            empty.delete()  # the singleton is already recorded — drop the stale bundle
            continue

        family, value = values[0]
        clause = Clause.objects.get(family=family, value=value)
        empty.clauses.set([clause])
        empty.clause_key = new_key
        empty.save(update_fields=["clause_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_campaign_discovery_minted_at_qualified"),
    ]

    operations = [
        migrations.RunPython(collapse_prescreen_empties, migrations.RunPython.noop),
    ]
