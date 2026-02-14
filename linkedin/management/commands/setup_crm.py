from django.core.management.base import BaseCommand

from linkedin.management.setup_crm import setup_crm


class Command(BaseCommand):
    help = "Bootstrap CRM data (departments, stages, users). Idempotent."

    def handle(self, *args, **options):
        setup_crm()
