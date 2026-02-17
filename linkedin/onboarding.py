# linkedin/onboarding.py
"""Onboarding: create Campaign + LinkedInProfile in DB via interactive prompts."""
from __future__ import annotations

import logging

from linkedin.conf import ENV_FILE

logger = logging.getLogger(__name__)


def _read_multiline(prompt_msg: str) -> str:
    """Read multi-line input via input() until Ctrl-D (EOF)."""
    print(prompt_msg, flush=True)
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _prompt(prompt_msg: str, default: str = "") -> str:
    """Prompt for a single-line value with an optional default."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt_msg}{suffix}: ").strip()
    return value or default


def _ensure_llm_api_key() -> None:
    """Check .env for LLM_API_KEY; if missing, prompt and write it."""
    import linkedin.conf as conf

    if conf.LLM_API_KEY:
        return

    print()
    print("LLM_API_KEY is required for lead qualification and message generation.")
    while True:
        key = input("Enter your LLM API key (e.g. sk-...): ").strip()
        if key:
            break
        print("API key cannot be empty. Please try again.")

    # Write to .env file
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if ENV_FILE.exists():
        content = ENV_FILE.read_text(encoding="utf-8")
        if "LLM_API_KEY" not in content:
            with open(ENV_FILE, "a", encoding="utf-8") as f:
                f.write(f"\nLLM_API_KEY={key}\n")
    else:
        ENV_FILE.write_text(f"LLM_API_KEY={key}\n", encoding="utf-8")

    # Update runtime
    import os
    os.environ["LLM_API_KEY"] = key
    conf.LLM_API_KEY = key
    logger.info("LLM_API_KEY written to %s", ENV_FILE)


def _onboard_campaign():
    """Create a Campaign via interactive prompts. Returns the Campaign instance."""
    from common.models import Department
    from linkedin.conf import DEFAULT_FOLLOWUP_TEMPLATE_PATH
    from linkedin.management.setup_crm import DEPARTMENT_NAME
    from linkedin.models import Campaign

    print()
    print("=" * 60)
    print("  OpenOutreach — Campaign Setup")
    print("=" * 60)
    print()

    campaign_name = _prompt("Campaign name", default=DEPARTMENT_NAME)

    print()
    print("To qualify LinkedIn profiles, we need two things:")
    print("  1. A description of your product/service")
    print("  2. Your campaign objective (e.g. 'sell X to Y')")
    print()

    while True:
        product_docs = _read_multiline(
            "Paste your product/service description below.\n"
            "Press Ctrl-D when done:\n"
        )
        if product_docs:
            break
        print("Product description cannot be empty. Please try again.\n")

    print()

    while True:
        objective = _read_multiline(
            "Enter your campaign objective (e.g. 'sell analytics platform to CTOs').\n"
            "Press Ctrl-D when done:\n"
        )
        if objective:
            break
        print("Campaign objective cannot be empty. Please try again.\n")

    print()
    booking_link = _prompt("Booking link (optional, e.g. https://cal.com/you)", default="")

    dept, _ = Department.objects.get_or_create(name=campaign_name)
    campaign = Campaign.objects.create(
        department=dept,
        product_docs=product_docs,
        campaign_objective=objective,
        followup_template=DEFAULT_FOLLOWUP_TEMPLATE_PATH.read_text(),
        booking_link=booking_link,
    )

    logger.info("Created campaign: %s", campaign_name)
    print()
    print(f"Campaign '{campaign_name}' created!")
    return campaign


def _onboard_account(campaign):
    """Create a LinkedInProfile via interactive prompts. Returns the profile."""
    from django.contrib.auth.models import User
    from linkedin.models import LinkedInProfile

    print()
    print("-" * 60)
    print("  LinkedIn Account Setup")
    print("-" * 60)
    print()

    while True:
        username = input("LinkedIn email: ").strip()
        if username and "@" in username:
            break
        print("Please enter a valid email address.")

    while True:
        password = input("LinkedIn password: ").strip()
        if password:
            break
        print("Password cannot be empty.")

    subscribe_raw = _prompt("Subscribe to OpenOutreach newsletter? (Y/n)", default="Y")
    subscribe = subscribe_raw.lower() not in ("n", "no", "false", "0")

    connect_daily = int(_prompt("Connection requests daily limit", default="20"))
    connect_weekly = int(_prompt("Connection requests weekly limit", default="100"))
    follow_up_daily = int(_prompt("Follow-up messages daily limit", default="30"))

    # Derive handle from email slug
    handle = username.split("@")[0].lower().replace(".", "_").replace("+", "_")

    user, created = User.objects.get_or_create(
        username=handle,
        defaults={"is_staff": True, "is_active": True},
    )
    if created:
        user.set_unusable_password()
        user.save()

    # Add user to department group
    dept = campaign.department
    if dept not in user.groups.all():
        user.groups.add(dept)

    profile = LinkedInProfile.objects.create(
        user=user,
        campaign=campaign,
        linkedin_username=username,
        linkedin_password=password,
        subscribe_newsletter=subscribe,
        connect_daily_limit=connect_daily,
        connect_weekly_limit=connect_weekly,
        follow_up_daily_limit=follow_up_daily,
    )

    logger.info("Created LinkedIn profile for %s (handle=%s)", username, handle)
    print()
    print(f"Account '{handle}' created!")
    print()
    return profile


def ensure_onboarding() -> None:
    """Ensure a Campaign and active LinkedInProfile exist in DB.

    If missing, runs interactive onboarding to create them.
    Also ensures LLM_API_KEY is set in .env.
    """
    from linkedin.models import Campaign, LinkedInProfile

    _ensure_llm_api_key()

    campaign = Campaign.objects.first()
    if campaign is None:
        campaign = _onboard_campaign()

    if not LinkedInProfile.objects.filter(active=True).exists():
        _onboard_account(campaign)
