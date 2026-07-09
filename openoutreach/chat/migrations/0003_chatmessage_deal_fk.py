# Swap ChatMessage's generic FK (→ Lead) for a direct FK (→ Deal).
#
# A LinkedIn DM thread is one shared conversation per person, but outreach is
# per-(lead, campaign) = per Deal. Moving the message onto the Deal makes each
# campaign's conversation self-contained and drops the generic-FK machinery.
#
# Two data steps:
#   (a) Delete legacy NULL-urn rows. These came from a retired send path that
#       recorded the outgoing message at send time (before LinkedIn assigned a
#       urn); sync later re-fetched the same messages *with* urns, leaving stale
#       duplicates. Today's sync never writes a NULL urn, so this is one-time cruft.
#   (b) Backfill the deal. A message's lead may have several deals:
#         1 deal              → assign to it
#         ≥2 deals, ≥1 "live" → assign to each live deal (the only case we clone)
#         ≥2 deals, 0 live    → assign to one archival home (most-recently-updated
#                               messageable deal, else most-recently-updated deal)
#       "live" = CONNECTED with no outcome — the exact follow-up eligibility, i.e.
#       the only deals that will read the history again. Terminal deals get no copy.
import logging
from collections import defaultdict

from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion

logger = logging.getLogger(__name__)

LIVE_STATE = "Connected"
MESSAGEABLE_STATES = {"Connected", "Completed"}


def _is_live(deal) -> bool:
    return deal.state == LIVE_STATE and not (deal.outcome or "")


def delete_legacy_null_urns(apps, schema_editor):
    """Drop the retired send path's NULL-urn rows (stale duplicates of synced msgs)."""
    ChatMessage = apps.get_model("chat", "ChatMessage")
    deleted, _ = ChatMessage.objects.filter(
        Q(linkedin_urn__isnull=True) | Q(linkedin_urn="")
    ).delete()
    logger.info("ChatMessage→Deal: deleted %d legacy NULL-urn rows", deleted)


def _clone_into(ChatMessage, msg, deal_id) -> None:
    """Materialize a copy of `msg` under another live deal (shared thread).

    The generic-FK columns (content_type/object_id) still exist and are NOT NULL
    at this point — they're dropped later in the migration — so copy them too.
    """
    ChatMessage.objects.create(
        deal_id=deal_id,
        content_type_id=msg.content_type_id,
        object_id=msg.object_id,
        content=msg.content,
        owner_id=msg.owner_id,
        creation_date=msg.creation_date,
        linkedin_urn=msg.linkedin_urn,
        is_outgoing=msg.is_outgoing,
    )


def populate_deal(apps, schema_editor):
    ChatMessage = apps.get_model("chat", "ChatMessage")
    Deal = apps.get_model("crm", "Deal")

    deals_by_lead: dict[int, list] = defaultdict(list)
    for deal in Deal.objects.all().only("id", "lead_id", "state", "outcome", "update_date"):
        deals_by_lead[deal.lead_id].append(deal)

    counts = {"single": 0, "one_live": 0, "multi_live": 0, "archival": 0, "orphan": 0, "cloned_rows": 0}

    for msg in ChatMessage.objects.all():
        deals = deals_by_lead.get(msg.object_id, [])
        if not deals:
            counts["orphan"] += 1
            msg.delete()
            continue

        if len(deals) == 1:
            msg.deal_id = deals[0].id
            msg.save(update_fields=["deal"])
            counts["single"] += 1
            continue

        live = [d for d in deals if _is_live(d)]
        if live:
            msg.deal_id = live[0].id
            msg.save(update_fields=["deal"])
            for extra in live[1:]:
                _clone_into(ChatMessage, msg, extra.id)
                counts["cloned_rows"] += 1
            counts["multi_live" if len(live) > 1 else "one_live"] += 1
        else:
            messageable = [d for d in deals if d.state in MESSAGEABLE_STATES]
            home = max(messageable or deals, key=lambda d: d.update_date)
            msg.deal_id = home.id
            msg.save(update_fields=["deal"])
            counts["archival"] += 1

    logger.debug("ChatMessage→Deal populate: %s", counts)


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0002_add_linkedin_sync_fields"),
        ("crm", "0013_alter_deal_state"),
    ]

    operations = [
        # 1. New nullable FK so the column exists before populate.
        migrations.AddField(
            model_name="chatmessage",
            name="deal",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="messages",
                to="crm.deal",
                verbose_name="Deal",
            ),
        ),
        # 2. Drop legacy NULL-urn cruft *before* the NOT NULL rebuild below.
        migrations.RunPython(delete_legacy_null_urns, migrations.RunPython.noop),
        # 3. Drop the global urn unique (so a shared thread can be cloned across
        #    live deals) and make it NOT NULL (no nulls remain after step 2).
        migrations.AlterField(
            model_name="chatmessage",
            name="linkedin_urn",
            field=models.CharField(
                max_length=300,
                help_text="entityUrn from Voyager API, used for dedup (per deal)",
                verbose_name="LinkedIn message URN",
            ),
        ),
        # 4. Backfill the deal (+ clone shared threads across live deals).
        migrations.RunPython(populate_deal, migrations.RunPython.noop),
        # 5. Every message now has a home — enforce it.
        migrations.AlterField(
            model_name="chatmessage",
            name="deal",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="messages",
                to="crm.deal",
                verbose_name="Deal",
            ),
        ),
        # 6. Per-deal dedup replaces the old global one.
        migrations.AddConstraint(
            model_name="chatmessage",
            constraint=models.UniqueConstraint(
                fields=["deal", "linkedin_urn"], name="uniq_deal_linkedin_urn",
            ),
        ),
        # 7. Retire the generic FK.
        migrations.RemoveField(model_name="chatmessage", name="content_type"),
        migrations.RemoveField(model_name="chatmessage", name="object_id"),
    ]
