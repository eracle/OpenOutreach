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

    def handle(self, *args, **options):
        from linkedin.models import Campaign
        from linkedin.setup.seeds import create_seed_leads, parse_seed_urls

        campaign = Campaign.objects.filter(pk=options["campaign_id"]).first()
        if not campaign:
            self.stderr.write(f"Campaign {options['campaign_id']} not found.")
            sys.exit(1)

        if sys.stdin.isatty():
            self.stdout.write(
                "Paste LinkedIn profile URLs (one per line).\n"
                "Press Ctrl-D when done:\n"
            )

        text = sys.stdin.read()
        public_ids = parse_seed_urls(text)
        if not public_ids:
            self.stderr.write("No valid LinkedIn URLs found.")
            return

        created = create_seed_leads(campaign, public_ids)
        self.stdout.write(self.style.SUCCESS(f"{created} seed profile(s) added as QUALIFIED."))
