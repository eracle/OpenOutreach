# linkedin/setup/partner.py
"""Partner campaign creation from kit config."""
from __future__ import annotations

import logging

from linkedin.conf import PARTNER_LOG_LEVEL

logger = logging.getLogger(__name__)


def import_partner_campaign(kit_config: dict):
    """Create or update a partner Campaign from kit config.

    Creates the department, pipeline, and adds all active users to the group.
    Returns the Campaign instance or None.
    """
    from common.models import Department
    from linkedin.management.setup_crm import ensure_campaign_pipeline
    from linkedin.models import Campaign, LinkedInProfile

    dept_name = kit_config.get("campaign_name", "Partner Outreach")
    dept, _ = Department.objects.get_or_create(name=dept_name)

    ensure_campaign_pipeline(dept)

    campaign, _ = Campaign.objects.update_or_create(
        department=dept,
        defaults={
            "product_docs": kit_config["product_docs"],
            "campaign_objective": kit_config["campaign_objective"],
            "followup_template": kit_config["followup_template"],
            "booking_link": kit_config["booking_link"],
            "is_partner": True,
            "action_fraction": kit_config["action_fraction"],
        },
    )

    # Add all active LinkedIn users to this department group
    for lp in LinkedInProfile.objects.filter(active=True).select_related("user"):
        if dept not in lp.user.groups.all():
            lp.user.groups.add(dept)

    logger.log(PARTNER_LOG_LEVEL, "[Partner] Campaign imported: %s (action_fraction=%.2f)",
               dept_name, kit_config["action_fraction"])
    return campaign
