# openoutreach/linkedin/admin.py
from django.contrib import admin

from django import forms
from openoutreach.linkedin.models import ActionLog, LinkedInProfile, SearchKeyword


class LinkedInProfileForm(forms.ModelForm):
    """Custom form for LinkedInProfile to mask sensitive password in Django Admin."""

    class Meta:
        model = LinkedInProfile
        fields = "__all__"
        widgets = {
            # Use PasswordInput to hide the password from clear view.
            # render_value=True is required so the existing password is not cleared when saving other fields.
            "linkedin_password": forms.PasswordInput(render_value=True),
        }


@admin.register(LinkedInProfile)
class LinkedInProfileAdmin(admin.ModelAdmin):
    form = LinkedInProfileForm
    # Ensure sensitive credentials are never added to list_display
    list_display = ("user", "linkedin_username", "active", "legal_accepted")
    list_filter = ("active",)
    raw_id_fields = ("user", "self_lead")


@admin.register(SearchKeyword)
class SearchKeywordAdmin(admin.ModelAdmin):
    list_display = ("keyword", "campaign", "used", "used_at")
    list_filter = ("used", "campaign")
    raw_id_fields = ("campaign",)


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ("action_type", "linkedin_profile", "campaign", "created_at")
    list_filter = ("action_type", "campaign")
    raw_id_fields = ("linkedin_profile", "campaign")
    date_hierarchy = "created_at"
    readonly_fields = ("linkedin_profile", "campaign", "action_type", "created_at")
