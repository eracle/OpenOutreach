# openoutreach/core/admin.py
from django.contrib import admin

from django import forms
from openoutreach.core.models import Campaign, SiteConfig, Task


class SiteConfigForm(forms.ModelForm):
    """Custom form for SiteConfig to mask sensitive API keys and tokens in Django Admin."""

    class Meta:
        model = SiteConfig
        fields = "__all__"
        widgets = {
            # Use PasswordInput to hide keys/tokens from clear view.
            # render_value=True is required so the existing key/token is not cleared when saving other fields.
            "llm_api_key": forms.PasswordInput(render_value=True),
            "bettercontact_api_key": forms.PasswordInput(render_value=True),
            "contacts_api_token": forms.PasswordInput(render_value=True),
        }


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    form = SiteConfigForm
    # Ensure sensitive credentials are never added to list_display
    list_display = ("__str__", "ai_model", "llm_api_base")

    def has_add_permission(self, request):
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "booking_link", "is_freemium", "action_fraction")
    filter_horizontal = ("users",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("task_type", "status", "scheduled_at", "payload", "created_at")
    list_filter = ("task_type", "status")
    readonly_fields = (
        "task_type", "status", "scheduled_at", "payload",
        "created_at", "started_at", "completed_at",
    )
    date_hierarchy = "scheduled_at"
