import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Add seed LinkedIn profile URLs as QUALIFIED leads for a campaign."

    def add_arguments(self, parser):
        parser.add_argument(
            "campaign_id",
            type=int,
            help="Campaign ID to add seeds to.",
        )
        parser.add_argument(
            "--csv",
            action="store_true",
            help="Read CSV with Profile URL, First Name, Last Name, Company columns.",
        )
        parser.add_argument(
            "--ready-to-connect",
            action="store_true",
            help="Create imported deals directly in Ready to Connect.",
        )

    def handle(self, *args, **options):
        from linkedin.models import Campaign
        from linkedin.setup.seeds import (
            create_seed_leads,
            create_seed_leads_from_csv,
            parse_csv_leads,
            parse_seed_urls,
        )

        campaign = Campaign.objects.filter(pk=options["campaign_id"]).first()
        if not campaign:
            self.stderr.write(f"Campaign {options['campaign_id']} not found.")
            sys.exit(1)

        initial_state = "Ready to Connect" if options["ready_to_connect"] else "Qualified"

        if sys.stdin.isatty():
            if options["csv"]:
                self.stdout.write(
                    "Paste CSV data (with header row).\n"
                    "Press Ctrl-D when done:\n"
                )
            else:
                self.stdout.write(
                    "Paste LinkedIn profile URLs (one per line).\n"
                    "Press Ctrl-D when done:\n"
                )

        text = sys.stdin.read()

        if options["csv"]:
            try:
                leads = parse_csv_leads(text)
            except ValueError as e:
                self.stderr.write(str(e))
                sys.exit(1)
            if not leads:
                self.stderr.write("No valid LinkedIn URLs found in CSV.")
                return
            created = create_seed_leads_from_csv(campaign, leads, initial_state=initial_state)
            self.stdout.write(self.style.SUCCESS(
                f"{created} seed(s) added as {initial_state.upper()} from {len(leads)} CSV rows."
            ))
        else:
            public_ids = parse_seed_urls(text)
            if not public_ids:
                self.stderr.write("No valid LinkedIn URLs found.")
                return
            created = create_seed_leads(campaign, public_ids, initial_state=initial_state)
            self.stdout.write(self.style.SUCCESS(f"{created} seed profile(s) added as {initial_state.upper()}."))
