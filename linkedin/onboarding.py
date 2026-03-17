# linkedin/onboarding.py
"""Onboarding: create Campaign + LinkedInProfile in DB via interactive prompts."""
from __future__ import annotations

import logging

from linkedin.conf import ENV_FILE, ROOT_DIR

DEFAULT_PRODUCT_DOCS = ROOT_DIR / "README.md"
DEFAULT_CAMPAIGN_OBJECTIVE = ROOT_DIR / "docs" / "default_campaign.md"

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


def _write_env_var(var_name: str, value: str) -> None:
    """Append a variable to .env if not already present."""
    if ENV_FILE.exists():
        content = ENV_FILE.read_text(encoding="utf-8")
        if var_name not in content:
            with open(ENV_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n{var_name}={value}\n")
    else:
        ENV_FILE.write_text(f"{var_name}={value}\n", encoding="utf-8")


def _ensure_env_var(
    var_name: str, prompt_msg: str, *, required: bool = True
) -> None:
    """Check .env for *var_name*; if missing, prompt and write it."""
    import os

    import linkedin.conf as conf

    if getattr(conf, var_name, None):
        return

    print()
    while True:
        value = input(f"{prompt_msg}: ").strip()
        if value or not required:
            break
        print(f"{var_name} cannot be empty. Please try again.")

    if not value:
        return

    _write_env_var(var_name, value)

    os.environ[var_name] = value
    setattr(conf, var_name, value)
    logger.info("%s written to %s", var_name, ENV_FILE)


def _ensure_llm_config() -> None:
    """Ensure all LLM-related env vars are set; prompt for missing ones."""
    print()
    print("Checking LLM configuration...")
    _ensure_env_var(
        "LLM_API_KEY",
        "Enter your LLM API key (e.g. sk-...)",
        required=True,
    )
    _ensure_env_var(
        "AI_MODEL",
        "Enter AI model name (e.g. gpt-4o, claude-sonnet-4-5-20250929)",
        required=True,
    )
    _ensure_env_var(
        "LLM_API_BASE",
        "Enter LLM API base URL (leave empty for OpenAI default)",
        required=False,
    )


def _onboard_campaign():
    """Create a Campaign via interactive prompts. Returns the Campaign instance."""
    from linkedin.management.setup_crm import DEFAULT_CAMPAIGN_NAME
    from linkedin.models import Campaign

    print()
    print("=" * 60)
    print("  OpenOutreach — Campaign Setup")
    print("=" * 60)
    print()

    campaign_name = _prompt("Campaign name", default=DEFAULT_CAMPAIGN_NAME)

    # Load defaults from files
    default_product = ""
    if DEFAULT_PRODUCT_DOCS.exists():
        default_product = DEFAULT_PRODUCT_DOCS.read_text(encoding="utf-8").strip()
    default_objective = ""
    if DEFAULT_CAMPAIGN_OBJECTIVE.exists():
        default_objective = DEFAULT_CAMPAIGN_OBJECTIVE.read_text(encoding="utf-8").strip()

    print()
    print("To qualify LinkedIn profiles, we need two things:")
    print("  1. A description of your product/service")
    print("  2. Your campaign objective (e.g. 'sell X to Y')")
    print()

    product_docs = ""
    if default_product:
        use_default = _prompt(
            "Use default product description from README.md? (Y/n)",
            default="Y",
        )
        if use_default.lower() not in ("n", "no"):
            product_docs = default_product

    if not product_docs:
        while True:
            product_docs = _read_multiline(
                "Paste your product/service description below.\n"
                "Press Ctrl-D when done:\n"
            )
            if product_docs:
                break
            print("Product description cannot be empty. Please try again.\n")

    print()

    objective = ""
    if default_objective:
        use_default_obj = _prompt(
            "Use default campaign objective from docs/default_campaign.md? (Y/n)",
            default="Y",
        )
        if use_default_obj.lower() not in ("n", "no"):
            objective = default_objective

    if not objective:
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

    campaign = Campaign.objects.create(
        name=campaign_name,
        product_docs=product_docs,
        campaign_objective=objective,
        booking_link=booking_link,
    )

    logger.info("Created campaign: %s", campaign_name)
    print()
    print(f"Campaign '{campaign_name}' created!")
    return campaign


def _onboard_seed_urls(campaign):
    """Optionally collect LinkedIn URLs to use as positive seed profiles."""
    print()
    add_seeds = _prompt(
        "Do you have LinkedIn profile URLs to use as positive seeds? (y/N)",
        default="N",
    )
    if add_seeds.lower() not in ("y", "yes"):
        return

    from linkedin.setup.seeds import parse_seed_urls, create_seed_leads

    text = _read_multiline(
        "Paste LinkedIn profile URLs (one per line).\n"
        "Press Ctrl-D when done:\n"
    )
    public_ids = parse_seed_urls(text)
    if not public_ids:
        print("No valid LinkedIn URLs found.")
        return

    created = create_seed_leads(campaign, public_ids)
    print(f"{created} seed profile(s) added as QUALIFIED.")


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

    connect_daily = int(_prompt("Connection requests daily limit", default="50"))
    connect_weekly = int(_prompt("Connection requests weekly limit", default="250"))
    follow_up_daily = int(_prompt("Follow-up messages daily limit", default="100"))

    # Derive handle from email slug
    handle = username.split("@")[0].lower().replace(".", "_").replace("+", "_")

    user, created = User.objects.get_or_create(
        username=handle,
        defaults={"is_staff": True, "is_active": True},
    )
    if created:
        user.set_unusable_password()
        user.save()

    # Add user to campaign
    campaign.users.add(user)

    profile = LinkedInProfile.objects.create(
        user=user,
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


def _require_legal_acceptance(profile) -> None:
    """Require the user to accept the legal notice for a LinkedIn profile."""
    if profile.legal_accepted:
        return

    url = "https://github.com/eracle/linkedin/blob/master/LEGAL_NOTICE.md"
    print()
    print("=" * 60)
    print(f"  LEGAL NOTICE — Account: {profile.linkedin_username}")
    print("=" * 60)
    print()
    print(f"Please read the Legal Notice before continuing:\n  {url}")
    print()
    while True:
        answer = input(
            f"Do you accept the Legal Notice for '{profile.linkedin_username}'? (y/n): "
        ).strip().lower()
        if answer == "y":
            profile.legal_accepted = True
            profile.save(update_fields=["legal_accepted"])
            return
        if answer == "n":
            print()
            print(
                "You must accept the Legal Notice to use OpenOutreach. "
                "Please read it carefully and try again."
            )
            print()
            continue
        print("Please type 'y' or 'n'.")


def ensure_onboarding() -> None:
    """Ensure Campaign, LinkedInProfile, LLM config, and legal acceptance.

    If missing, runs interactive prompts to configure them.
    """
    from linkedin.models import Campaign, LinkedInProfile

    campaign = Campaign.objects.first()
    if campaign is None:
        campaign = _onboard_campaign()
        _onboard_seed_urls(campaign)

    if not LinkedInProfile.objects.filter(active=True).exists():
        _onboard_account(campaign)

    _ensure_llm_config()

    for p in LinkedInProfile.objects.filter(legal_accepted=False, active=True):
        _require_legal_acceptance(p)
