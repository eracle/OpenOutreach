#!/usr/bin/env python
"""
Bootstrap script for initial CRM data.

Creates the default Department, Django Users for each LinkedIn account,
Deal Stages mapped to the profile state machine, ClosingReasons, and LeadSource.

Idempotent — safe to run multiple times.

Usage:
    python -m linkedin.management.setup_crm
"""
import os
import sys
import logging

logger = logging.getLogger(__name__)

DEPARTMENT_NAME = "LinkedIn Outreach"

# Stages map to ProfileState enum values.
# (index, name, default, success_stage)
STAGES = [
    (1, "Discovered", True, False),
    (2, "Enriched", False, False),
    (3, "Pending", False, False),
    (4, "Connected", False, False),
    (5, "Completed", False, True),
    (6, "Failed", False, False),
]

CLOSING_REASONS = [
    (1, "Completed", True),   # success
    (2, "Failed", False),     # failure
]

LEAD_SOURCE_NAME = "LinkedIn Scraper"


def setup_crm():
    from django.contrib.auth.models import User, Group
    from django.contrib.sites.models import Site
    from common.models import Department
    from crm.models import Stage, ClosingReason, LeadSource

    from linkedin.conf import list_active_accounts

    # Ensure default Site exists
    Site.objects.get_or_create(id=1, defaults={"domain": "localhost", "name": "localhost"})

    # DjangoCRM's user creation signal expects a "co-workers" group
    Group.objects.get_or_create(name="co-workers")

    # 1. Create Department
    dept, created = Department.objects.get_or_create(name=DEPARTMENT_NAME)
    if created:
        logger.info("Created department: %s", DEPARTMENT_NAME)
    else:
        logger.info("Department already exists: %s", DEPARTMENT_NAME)

    # 2. Create Django Users for each LinkedIn account handle
    for handle in list_active_accounts():
        user, created = User.objects.get_or_create(
            username=handle,
            defaults={"is_staff": True, "is_active": True},
        )
        if created:
            user.set_unusable_password()
            user.save()
            logger.info("Created user: %s", handle)

        # Add user to department group
        if dept not in user.groups.all():
            user.groups.add(dept)
            logger.info("Added %s to department %s", handle, DEPARTMENT_NAME)

    # 3. Create Deal Stages
    for index, name, is_default, is_success in STAGES:
        stage, created = Stage.objects.get_or_create(
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

    # 4. Create ClosingReasons
    for index, name, is_success in CLOSING_REASONS:
        reason, created = ClosingReason.objects.get_or_create(
            name=name,
            department=dept,
            defaults={
                "index_number": index,
                "success_reason": is_success,
            },
        )
        if created:
            logger.info("Created closing reason: %s", name)

    # 5. Create LeadSource
    source, created = LeadSource.objects.get_or_create(
        name=LEAD_SOURCE_NAME,
        department=dept,
    )
    if created:
        logger.info("Created lead source: %s", LEAD_SOURCE_NAME)

    logger.info("CRM setup complete.")


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

    import django
    django.setup()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    setup_crm()
