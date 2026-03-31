import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Export a campaign definition to JSON for handoff to another machine."

    def add_arguments(self, parser):
        parser.add_argument(
            "campaign",
            help="Campaign ID or exact campaign name.",
        )
        parser.add_argument(
            "--out",
            help="Write JSON to a file instead of stdout.",
        )
        parser.add_argument(
            "--include-seeds",
            action="store_true",
            help="Include campaign seed_public_ids in the export.",
        )

    def handle(self, *args, **options):
        from linkedin.models import Campaign

        ref = str(options["campaign"]).strip()
        queryset = Campaign.objects.all()
        campaign = queryset.filter(pk=int(ref)).first() if ref.isdigit() else None
        if campaign is None:
            campaign = queryset.filter(name=ref).first()
        if campaign is None:
            raise CommandError(f"Campaign '{ref}' not found.")

        payload = {
            "name": campaign.name,
            "product_docs": campaign.product_docs,
            "campaign_objective": campaign.campaign_objective,
            "booking_link": campaign.booking_link,
            "is_freemium": campaign.is_freemium,
            "action_fraction": campaign.action_fraction,
        }
        if options["include_seeds"]:
            payload["seed_public_ids"] = campaign.seed_public_ids
        rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

        out_path = options.get("out")
        if out_path:
            path = Path(out_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Exported campaign to {path}"))
            return

        self.stdout.write(rendered, ending="")
