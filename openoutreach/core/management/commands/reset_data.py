from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Delete all Leads and Deals and clear GP model blobs. Keeps Campaigns."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    def handle(self, *args, **options):
        from openoutreach.crm.models import Deal, Lead

        from openoutreach.core.models import Campaign

        counts = {
            "Leads": Lead.objects.count(),
            "Deals": Deal.objects.count(),
        }

        campaigns_with_models = Campaign.objects.exclude(model_blob=None).count()

        self.stdout.write("Will delete:")
        for name, count in counts.items():
            self.stdout.write(f"  {name}: {count}")
        self.stdout.write(f"  Campaign model blobs: {campaigns_with_models}")

        if not options["yes"]:
            confirm = input("\nProceed? [y/N] ")
            if confirm.lower() != "y":
                self.stdout.write("Aborted.")
                return

        # Order matters: delete dependents first
        Deal.objects.all().delete()
        Lead.objects.all().delete()

        # Clear GP model blobs
        Campaign.objects.exclude(model_blob=None).update(model_blob=None)

        self.stdout.write(self.style.SUCCESS("Reset complete."))
