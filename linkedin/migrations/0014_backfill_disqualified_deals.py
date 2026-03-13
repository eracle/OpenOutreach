"""Convert LLM-rejected leads from Lead.disqualified=True to FAILED Deals.

Previously, LLM rejections set Lead.disqualified=True (global). Now they
are tracked as FAILED Deals with "Disqualified" closing reason (campaign-scoped).

Lead.disqualified=True is now reserved for self-profile exclusion only.
Self-profile leads are identified by having no ProfileEmbedding (they are
never embedded).

This migration:
  1. Ensures the "Disqualified" ClosingReason and "Failed" Stage exist.
  2. For each disqualified lead WITH an embedding (i.e. LLM-rejected, not
     self-profile): creates a FAILED Deal in the lead's department and
     clears the disqualified flag.
  3. Leaves self-profile leads (no embedding) with disqualified=True.
"""

import uuid
from datetime import date

from django.db import migrations


def _forwards(apps, schema_editor):
    Lead = apps.get_model("crm", "Lead")
    Deal = apps.get_model("crm", "Deal")
    Stage = apps.get_model("crm", "Stage")
    ClosingReason = apps.get_model("crm", "ClosingReason")
    ProfileEmbedding = apps.get_model("linkedin", "ProfileEmbedding")
    Campaign = apps.get_model("linkedin", "Campaign")

    # Find disqualified leads that have embeddings (LLM-rejected, not self-profile)
    embedded_lead_ids = set(
        ProfileEmbedding.objects.values_list("lead_id", flat=True)
    )
    llm_rejected = Lead.objects.filter(
        disqualified=True, id__in=embedded_lead_ids
    )

    if not llm_rejected.exists():
        return

    # Fallback department for leads with null department: LLM qualification
    # only happens in regular (non-freemium) campaigns, so use the first one.
    fallback_dept = None
    regular = Campaign.objects.filter(is_freemium=False).first()
    if regular:
        fallback_dept = regular.department
    else:
        # No regular campaign — try any campaign
        any_campaign = Campaign.objects.first()
        if any_campaign:
            fallback_dept = any_campaign.department

    # Group by department so we look up the right Stage/ClosingReason per dept
    dept_cache = {}  # dept_id -> (stage, closing_reason)

    for lead in llm_rejected:
        dept = lead.department or fallback_dept
        if not dept:
            continue

        if dept.pk not in dept_cache:
            # Ensure "Failed" stage and "Disqualified" closing reason exist
            stage, _ = Stage.objects.get_or_create(
                name="Failed", department=dept,
                defaults={"index_number": 6, "default": False, "success_stage": False},
            )
            closing, _ = ClosingReason.objects.get_or_create(
                name="Disqualified", department=dept,
                defaults={"index_number": 3, "success_reason": False},
            )
            dept_cache[dept.pk] = (stage, closing)

        stage, closing = dept_cache[dept.pk]

        # Skip if a Deal already exists for this lead in this department
        if Deal.objects.filter(lead=lead, department=dept).exists():
            lead.disqualified = False
            lead.save(update_fields=["disqualified"])
            continue

        Deal.objects.create(
            name=f"LinkedIn: {lead.website or ''}",
            lead=lead,
            stage=stage,
            owner=lead.owner,
            department=dept,
            closing_reason=closing,
            description=lead.description[:200] if lead.description else "",
            active=False,
            next_step="",
            next_step_date=date.today(),
            ticket=uuid.uuid4().hex[:16],
        )

        lead.disqualified = False
        lead.save(update_fields=["disqualified"])


def _backwards(apps, schema_editor):
    Lead = apps.get_model("crm", "Lead")
    Deal = apps.get_model("crm", "Deal")
    ClosingReason = apps.get_model("crm", "ClosingReason")

    # Find all FAILED Deals with "Disqualified" closing reason
    disqualified_deals = Deal.objects.filter(
        closing_reason__name="Disqualified", active=False,
    )

    for deal in disqualified_deals:
        if deal.lead:
            deal.lead.disqualified = True
            deal.lead.save(update_fields=["disqualified"])
        deal.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0013_rename_is_partner_to_is_freemium"),
        ("crm", "__latest__"),
    ]

    operations = [
        migrations.RunPython(_forwards, _backwards),
    ]
