#!/usr/bin/env python
"""
Bootstrap script for initial CRM data.

Creates the default Department, Deal Stages mapped to the profile state
machine, ClosingReasons, and LeadSource.

Idempotent — safe to run multiple times.
"""
import logging

logger = logging.getLogger(__name__)

DEPARTMENT_NAME = "LinkedIn Outreach"

# Stages map to ProfileState enum values (post-qualification pipeline).
# (index, name, default, success_stage)
STAGES = [
    (1, "New", True, False),
    (2, "Pending", False, False),
    (3, "Connected", False, False),
    (4, "Completed", False, True),
    (5, "Failed", False, False),
]

CLOSING_REASONS = [
    (1, "Completed", True),   # success
    (2, "Failed", False),     # failure
]

LEAD_SOURCE_NAME = "LinkedIn Scraper"


def setup_crm():
    from django.contrib.auth.models import Group
    from django.contrib.sites.models import Site
    from common.models import Department
    from crm.models import Stage, ClosingReason, LeadSource

    # Ensure default Site exists
    Site.objects.get_or_create(id=1, defaults={"domain": "localhost", "name": "localhost"})

    # DjangoCRM's user creation signal expects a "co-workers" group
    Group.objects.get_or_create(name="co-workers")

    # 1. Create Department
    dept, created = Department.objects.get_or_create(name=DEPARTMENT_NAME)
    if created:
        logger.info("Created department: %s", DEPARTMENT_NAME)
    else:
        logger.debug("Department already exists: %s", DEPARTMENT_NAME)

    # 2. Create Deal Stages
    for index, name, is_default, is_success in STAGES:
        stage, created = Stage.objects.update_or_create(
            name=name,
            department=dept,
            defaults={
                "index_number": index,
                "default": is_default,
                "success_stage": is_success,
            },
        )
        if created:
            logger.info("Created stage: %s (index=%d)", name, index)

    # 3. Create ClosingReasons
    for index, name, is_success in CLOSING_REASONS:
        reason, created = ClosingReason.objects.update_or_create(
            name=name,
            department=dept,
            defaults={
                "index_number": index,
                "success_reason": is_success,
            },
        )
        if created:
            logger.info("Created closing reason: %s", name)

    # 4. Create LeadSource
    source, created = LeadSource.objects.get_or_create(
        name=LEAD_SOURCE_NAME,
        department=dept,
    )
    if created:
        logger.info("Created lead source: %s", LEAD_SOURCE_NAME)

    logger.debug("CRM setup complete.")
