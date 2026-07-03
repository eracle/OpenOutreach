# openoutreach/emails/admin.py
from django.contrib import admin

from django import forms
from openoutreach.emails.models import Mailbox


class MailboxForm(forms.ModelForm):
    """Custom form for Mailbox to mask sensitive SMTP password in Django Admin."""

    class Meta:
        model = Mailbox
        fields = "__all__"
        widgets = {
            # Use PasswordInput to hide the SMTP password from clear view.
            # render_value=True is required so the existing password is not cleared when saving other fields.
            "password": forms.PasswordInput(render_value=True),
        }


@admin.register(Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    form = MailboxForm
    # Ensure sensitive credentials are never added to list_display
    list_display = ("from_address", "host", "port", "daily_limit", "sent_today")
    search_fields = ("from_address", "username")
