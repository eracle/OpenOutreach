import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Import a campaign definition from JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "json_path",
            help="Path to a campaign JSON file created by export_campaign.",
        )
        parser.add_argument(
            "--name",
            help="Override the imported campaign name.",
        )

    def handle(self, *args, **options):
        from linkedin.models import Campaign

        path = Path(options["json_path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {path}: {exc}") from exc

        name = (options.get("name") or payload.get("name") or "").strip()
        if not name:
            raise CommandError("Campaign JSON must include a non-empty 'name'.")

        defaults = {
            "product_docs": payload.get("product_docs", ""),
            "campaign_objective": payload.get("campaign_objective", ""),
            "booking_link": payload.get("booking_link", ""),
            "is_freemium": bool(payload.get("is_freemium", False)),
            "action_fraction": payload.get("action_fraction", 0.2),
            "seed_public_ids": payload.get("seed_public_ids") or [],
        }
        campaign, created = Campaign.objects.update_or_create(name=name, defaults=defaults)
        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} campaign '{campaign.name}' (id={campaign.pk})"
            )
        )
