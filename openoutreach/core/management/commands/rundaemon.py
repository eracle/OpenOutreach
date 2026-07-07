import logging
import sys

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the OpenOutreach daemon (onboard, validate, start task queue)."

    def handle(self, *args, **options):
        self._configure_logging(verbose=options["verbosity"] >= 2)
        self._ensure_db()
        self._reconcile_operator()
        self._ensure_onboarded()
        session = self._create_session()

        from openoutreach.core.daemon import run_daemon
        run_daemon(session)

    # -- Steps ---------------------------------------------------------------

    def _configure_logging(self, verbose: bool = False):
        from openoutreach.core.logging import configure_logging, print_banner

        level = logging.DEBUG if verbose else logging.INFO
        configure_logging(level=level)
        print_banner()

    def _ensure_db(self):
        call_command("migrate", "--no-input")

        from openoutreach.core.management.setup_crm import setup_crm
        setup_crm()

    def _reconcile_operator(self):
        """Keep a legacy/stale operator email in sync with the mailbox before the
        onboarding check reads it — otherwise a blank email would both disable the
        BCC self-copy and (with the tightened account check) mint a duplicate
        operator on the next onboard."""
        from openoutreach.core.session import reconcile_operator_email

        reconcile_operator_email()

    def _ensure_onboarded(self):
        from openoutreach.core.onboarding import missing_keys, onboard_interactive

        missing = missing_keys()
        if not missing:
            return

        if sys.stdin.isatty():
            onboard_interactive()
        else:
            self.stderr.write(
                f"Onboarding incomplete and no TTY available.\n"
                f"Missing: {', '.join(sorted(missing))}\n"
                f"Run with an interactive terminal to complete onboarding "
                f"(a mailbox and a BetterContact key must be connected)."
            )
            sys.exit(1)

    def _create_session(self):
        from openoutreach.core.models import SiteConfig
        from openoutreach.core.session import get_active_user, get_or_create_session

        if not SiteConfig.load().llm_api_key:
            logger.error("LLM_API_KEY is required. Set it in Site Configuration (Django Admin).")
            sys.exit(1)

        user = get_active_user()
        if user is None:
            logger.error("No active operator account found.")
            sys.exit(1)

        session = get_or_create_session(user)

        if not session.campaigns:
            logger.error("No campaigns found for this operator.")
            sys.exit(1)
        session.campaign = session.campaigns[0]

        return session
