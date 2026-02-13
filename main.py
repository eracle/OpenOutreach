import argparse
import logging
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

import django
django.setup()

from linkedin.management.setup_crm import setup_crm

logging.getLogger().handlers.clear()
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)

# Suppress noisy third-party loggers
for _name in ("urllib3", "httpx", "langchain", "dbt", "playwright", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def cmd_load(args):
    from linkedin.csv_launcher import load_profiles_df
    from linkedin.db.crm_profiles import add_profile_urls
    from linkedin.sessions.registry import get_session
    from linkedin.conf import get_first_active_account

    handle = args.handle
    if handle is None:
        handle = get_first_active_account()
        if handle is None:
            logger.error("No handle provided and no active accounts found.")
            sys.exit(1)

    session = get_session(handle=handle)
    profiles_df = load_profiles_df(args.csv)

    url_col = next(
        col for col in profiles_df.columns
        if col.lower() in ["url", "linkedin_url", "profile_url"]
    )
    urls = profiles_df[url_col].tolist()
    add_profile_urls(session, urls)


def cmd_generate_keywords(args):
    import yaml

    from linkedin.conf import KEYWORDS_FILE
    from linkedin.onboarding import generate_keywords

    with open(args.product_docs, "r", encoding="utf-8") as f:
        product_docs = f.read()

    data = generate_keywords(product_docs, args.campaign_objective)

    with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    logger.info("Keywords written to %s", KEYWORDS_FILE)
    logger.info("  %d positive, %d negative, %d exploratory", len(data["positive"]), len(data["negative"]), len(data["exploratory"]))


def cmd_run(args):
    from linkedin.api.emails import ensure_newsletter_subscription
    from linkedin.conf import get_first_active_account
    from linkedin.daemon import run_daemon
    from linkedin.onboarding import ensure_keywords
    from linkedin.sessions.registry import get_session

    ensure_keywords(
        product_docs_path=args.product_docs,
        campaign_objective_path=args.campaign_objective,
    )

    handle = args.handle
    if handle is None:
        handle = get_first_active_account()
        if handle is None:
            logger.error("No handle provided and no active accounts found.")
            sys.exit(1)

    session = get_session(handle=handle)
    session.ensure_browser()
    ensure_newsletter_subscription(session)
    run_daemon(session)


def _ensure_db():
    from django.core.management import call_command
    call_command("migrate", "--no-input", verbosity=0)
    setup_crm()


if __name__ == "__main__":
    _ensure_db()

    parser = argparse.ArgumentParser(prog="openoutreach", description="OpenOutreach CLI")
    subparsers = parser.add_subparsers(dest="command")

    # load subcommand
    load_parser = subparsers.add_parser("load", help="Load profile URLs from CSV into CRM")
    load_parser.add_argument("csv", help="Path to CSV file with LinkedIn URLs")
    load_parser.add_argument("--handle", default=None, help="Account handle to use")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run the daemon campaign loop")
    run_parser.add_argument("handle", nargs="?", default=None, help="Account handle to use")
    run_parser.add_argument("--product-docs", default=None, help="Path to product description file")
    run_parser.add_argument("--campaign-objective", default=None, help="Path to campaign objective file")

    # generate-keywords subcommand
    gk_parser = subparsers.add_parser("generate-keywords", help="Generate campaign keywords via LLM")
    gk_parser.add_argument("product_docs", help="Path to product documentation file")
    gk_parser.add_argument("campaign_objective", help="Campaign objective description")

    args = parser.parse_args()

    if args.command == "load":
        cmd_load(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "generate-keywords":
        cmd_generate_keywords(args)
    else:
        parser.print_help()
        sys.exit(1)
