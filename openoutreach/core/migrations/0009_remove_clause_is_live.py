"""Drop ``Clause.is_live`` — the singleton probe's verdict, now the ``k=1`` case of
``EmptyClauseSet``.

The column and the blacklist stored the same fact by two routes. ``is_live=False``
excluded a clause from the pool; a recorded singleton ``{c}`` prunes every candidate
containing it. Those prune identically — ``{c} ⊆ candidate`` iff ``c ∈ candidate`` —
so keeping both meant keeping them in step, and the probe sweep that wrote the column
(a ``limit=1`` call per clause, creating no leads) is gone with it: every node is a
real fetch now, and ``discover`` records emptiness from the page it already pulled.

**No data is carried over, and none is lost.** A ``True`` was only ever "a fetch
would have found somebody", which the next fetch re-establishes for free. A ``False``
is a real conviction, but the surviving pool is re-swept on the next walk at level 1,
which costs one page per dead clause once. Backfilling instead would mean minting
``EmptyClauseSet`` rows from a column whose provenance we cannot re-check, and
emptiness rows are **global** — a wrong one prunes every campaign's lattice forever.
Re-earning them is cheap; un-earning them is not.

Reversible: the column comes back as NULL everywhere, which is what it meant before
anything probed it.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_clause_lattice"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="clause",
            name="is_live",
        ),
    ]
