from django import forms

from openoutreach.config.models import SiteConfig


class CampaignForm(forms.ModelForm):
    product_docs = forms.CharField(max_length=20000)
    campaign_target = forms.CharField(max_length=20000)

    class Meta:
        model = SiteConfig
        fields = ["product_docs", "campaign_target", "booking_link", "signature"]

    def clean_booking_link(self):
        value = self.cleaned_data["booking_link"]
        if value:
            forms.URLField().clean(value)
            if not value.startswith("https://"):
                raise forms.ValidationError("Use um link HTTPS.")
        return value


class IntegrationForm(forms.ModelForm):
    """Blank secret fields mean keep the saved value; secrets are never returned."""
    secret_fields = ("llm_api_key", "bettercontact_api_key", "mailbox_password")

    class Meta:
        model = SiteConfig
        fields = [
            "ai_model", "llm_api_key", "llm_api_base", "bettercontact_api_key",
            "operator_name", "operator_email", "operator_country_code",
            "mailbox_address", "mailbox_password", "smtp_host", "smtp_port",
            "imap_host", "imap_port", "accepted_legal_notice",
        ]

    def clean(self):
        cleaned = super().clean()
        for field in self.secret_fields:
            if not cleaned.get(field):
                cleaned[field] = getattr(self.instance, field)
        country = cleaned.get("operator_country_code", "").upper()
        if country:
            import pytz
            if country not in pytz.country_names:
                self.add_error("operator_country_code", "Use um país válido, como BR ou PT.")
        cleaned["operator_country_code"] = country.lower()
        for field in ("smtp_port", "imap_port"):
            value = cleaned.get(field)
            if value and (not value.isdecimal() or not 1 <= int(value) <= 65535):
                self.add_error(field, "Use uma porta entre 1 e 65535.")

        # Normalize ai_model with openai_compatible: prefix if needed
        ai_model = cleaned.get("ai_model", "").strip()
        llm_base = cleaned.get("llm_api_base", "").strip() or getattr(self.instance, "llm_api_base", "")
        if ai_model and ":" not in ai_model:
            if llm_base or any(ai_model.startswith(p) for p in ("cbai/", "ollama/", "bzl/", "cx/")):
                cleaned["ai_model"] = f"openai_compatible:{ai_model}"

        # Auto-configure Resend SMTP host & port if API key or Resend host is supplied
        mb_pass = cleaned.get("mailbox_password", "").strip()
        smtp_h = cleaned.get("smtp_host", "").strip()
        if mb_pass.startswith("re_") or smtp_h == "smtp.resend.com":
            if not smtp_h:
                cleaned["smtp_host"] = "smtp.resend.com"
            if not cleaned.get("smtp_port"):
                cleaned["smtp_port"] = "587"

        return cleaned
