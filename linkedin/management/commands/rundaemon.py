import logging
import sys

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the OpenOutreach daemon (onboard, validate, start task queue)."

    def handle(self, *args, **options):
        self._configure_logging()
        self._ensure_db()
        self._ensure_onboarded()
        session = self._create_session()
        self._ensure_newsletter(session)

        from linkedin.daemon import run_daemon
        run_daemon(session)

    # -- Steps ---------------------------------------------------------------

    def _configure_logging(self):
        logging.getLogger().handlers.clear()
        logging.basicConfig(level=logging.DEBUG, format="%(message)s")
        for name in (
            "urllib3", "httpx", "langchain", "openai", "playwright",
            "httpcore", "fastembed", "huggingface_hub", "filelock", "asyncio",
        ):
            logging.getLogger(name).setLevel(logging.WARNING)

    def _ensure_db(self):
        call_command("migrate", "--no-input")

        from linkedin.management.setup_crm import setup_crm
        setup_crm()

    def _ensure_onboarded(self):
        from linkedin.onboarding import apply, collect_from_wizard, missing_keys

        if not missing_keys():
            return

        if sys.stdin.isatty():
            apply(collect_from_wizard())
        else:
            missing = missing_keys()
            self.stderr.write(
                f"Onboarding incomplete and no TTY available.\n"
                f"Missing: {', '.join(sorted(missing))}\n"
                f"Run with an interactive terminal to complete onboarding."
            )
            sys.exit(1)

    def _create_session(self):
        from linkedin.browser.registry import get_first_active_profile, get_or_create_session
        from linkedin.conf import get_llm_config

        llm_api_key, _, _ = get_llm_config()
        if not llm_api_key:
            logger.error("LLM_API_KEY is required. Set it in Site Configuration (Django Admin).")
            sys.exit(1)

        profile = get_first_active_profile()
        if profile is None:
            logger.error("No active LinkedIn profiles found.")
            sys.exit(1)

        session = get_or_create_session(profile)

        if not session.campaigns:
            logger.error("No campaigns found for this user.")
            sys.exit(1)
        campaign = next(
            (c for c in session.campaigns if not c.is_freemium), None,
        ) or session.campaigns[0]
        session.campaign = campaign

        return session

    def _ensure_newsletter(self, session):
        if session.linkedin_profile.newsletter_processed:
            return

        from linkedin.api.newsletter import ensure_newsletter_subscription
        from linkedin.setup.gdpr import apply_gdpr_newsletter_override
        from linkedin.url_utils import public_id_to_url

        profile = session.self_profile
        country_code = profile.get("country_code")
        apply_gdpr_newsletter_override(session, country_code)
        linkedin_url = public_id_to_url(profile["public_identifier"])
        ensure_newsletter_subscription(session, linkedin_url=linkedin_url)
        session.linkedin_profile.newsletter_processed = True
        session.linkedin_profile.save(update_fields=["newsletter_processed"])
