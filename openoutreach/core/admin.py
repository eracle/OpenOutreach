# openoutreach/core/admin.py
from django.contrib import admin

from openoutreach.core.models import Campaign, DiscoveryQuery, SiteConfig, Task


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "ai_model", "llm_api_base")

    def has_add_permission(self, request):
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "booking_link", "is_freemium", "action_fraction")
    filter_horizontal = ("users",)


@admin.register(DiscoveryQuery)
class DiscoveryQueryAdmin(admin.ModelAdmin):
    """Per-query discovery analytics — the record the graph-search card set out
    to expose: which queries we ran, how deep, and which actually pay."""

    list_display = (
        "id", "campaign", "offset", "score", "exhausted", "lead_yield",
        "accepted_leads", "updated_at",
    )
    list_filter = ("exhausted", "campaign")
    readonly_fields = (
        "campaign", "params", "params_hash", "offset", "exhausted", "score",
        "lead_yield", "accepted_leads", "created_at", "updated_at",
    )
    date_hierarchy = "created_at"

    @admin.display(description="leads")
    def lead_yield(self, obj):
        """First-touch leads this query surfaced."""
        return obj.leads.count()

    @admin.display(description="qualified")
    def accepted_leads(self, obj):
        """Its first-touch leads that reached a (non-failed) Deal — the slower,
        LLM-confirmed truth the cheap score stands in for."""
        from openoutreach.crm.models import DealState

        return obj.leads.filter(deal__isnull=False).exclude(deal__state=DealState.FAILED).distinct().count()


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("task_type", "status", "scheduled_at", "payload", "created_at")
    list_filter = ("task_type", "status")
    readonly_fields = (
        "task_type", "status", "scheduled_at", "payload",
        "created_at", "started_at", "completed_at",
    )
    date_hierarchy = "scheduled_at"
