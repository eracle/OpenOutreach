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
    to expose: which queries we ran, how deep, and which actually pay.

    The three columns are the walk's own signals, and they are three different
    things: ``leads`` is what the query surfaced, ``examined`` how many of those the
    LLM has ruled on, ``qualified`` how many it accepted. ``examined = 0`` with
    ``qualified = 0`` means *nobody looked* — not a barren region.
    """

    list_display = (
        "id", "campaign", "offset", "exhausted", "lead_yield",
        "examined", "qualified", "updated_at",
    )
    list_filter = ("exhausted", "campaign")
    readonly_fields = (
        "campaign", "params", "params_hash", "offset", "exhausted",
        "lead_yield", "examined", "qualified", "created_at", "updated_at",
    )
    date_hierarchy = "created_at"

    def _stats(self, obj):
        """This node's counts, straight from the frontier's own metric.

        One campaign-wide ``GROUP BY`` per row rather than an annotated queryset:
        re-expressing the count here would fork the definition of "qualified" away
        from the walk that acts on it, and this card exists because exactly that kind
        of drift went unnoticed. An admin page renders a handful of nodes; the walk
        is the thing that must not be wrong.
        """
        from openoutreach.core.pipeline.frontier import NodeStats, node_stats

        return node_stats(obj.campaign).get(obj.pk, NodeStats(0, 0))

    @admin.display(description="leads")
    def lead_yield(self, obj):
        """First-touch leads this query surfaced."""
        return obj.leads.count()

    @admin.display(description="examined")
    def examined(self, obj):
        """Its first-touch leads the LLM has ruled on — the node's denominator."""
        return self._stats(obj).examined

    @admin.display(description="qualified")
    def qualified(self, obj):
        """Its first-touch leads the LLM accepted — the node's value, and what the
        frontier deepens on."""
        return self._stats(obj).qualified


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("task_type", "status", "scheduled_at", "payload", "created_at")
    list_filter = ("task_type", "status")
    readonly_fields = (
        "task_type", "status", "scheduled_at", "payload",
        "created_at", "started_at", "completed_at",
    )
    date_hierarchy = "scheduled_at"
