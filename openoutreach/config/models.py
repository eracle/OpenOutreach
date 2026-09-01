# openoutreach/config/models.py
"""Every answer this install gave, in one row — and the two vocabularies it exports into.

**This is the one place a human's answer is remembered, and this is the program with a
human in front of it.** The children are agent-first: `openoutfind` and `openoutsend` read
their configuration from `OPENOUTFIND_*` / `OUTSEND_*` on every run and persist none of
it, because an agent supplies its environment on every invocation and has nothing to
remember. Somebody who types does not want to retype, so the wizard asks once and this row
answers for them from then on.

**Exporting is not a translation layer.** That objection was real while each child *also*
had a config model — two surfaces for the strings to drift against. With the environment as
a child's only surface, `export()` writes the one interface each child has, and the child
fails loudly naming a variable if this row ever stops filling it.

The split against the children is the same rule they follow internally: **an answer
somebody gave lives here; what a pipeline produced or measured lives in the child's own
store.** So the leads, the walk, the mail log, the suppression list and the mailbox's
learned capacity are not here and never will be.
"""
from __future__ import annotations

from django.db import models

# Where each field lands in the finder's vocabulary. A field absent from both maps is one
# only this host reads (`operator_name` is the sender's alone, and so on).
FINDER_ENV = {
    "product_docs": "OPENOUTFIND_PRODUCT_DOCS",
    "campaign_target": "OPENOUTFIND_CAMPAIGN_TARGET",
    "ai_model": "OPENOUTFIND_AI_MODEL",
    "llm_api_key": "OPENOUTFIND_LLM_API_KEY",
    "llm_api_base": "OPENOUTFIND_LLM_API_BASE",
    "bettercontact_api_key": "OPENOUTFIND_BETTERCONTACT_API_KEY",
    "apollo_api_key": "OPENOUTFIND_APOLLO_API_KEY",
    "email_finder": "OPENOUTFIND_EMAIL_FINDER",
    "operator_email": "OPENOUTFIND_OPERATOR_EMAIL",
    "country_code": "OPENOUTFIND_COUNTRY",
    "contacts_api_token": "OPENOUTFIND_CONTACTS_API_TOKEN",
    "accepted_legal_notice": "OPENOUTFIND_ACCEPT_LEGAL_NOTICE",
    "newsletter": "OPENOUTFIND_NEWSLETTER",
}

# And in the sender's. **The suffixes are not always the same** — `OPENOUTFIND_COUNTRY`
# against a sender that asks nothing about jurisdiction, `OUTSEND_OPERATOR_NAME` against a
# finder that signs nothing — which is exactly why the mapping is written down once here
# instead of being guessed from a prefix.
SENDER_ENV = {
    "product_docs": "OUTSEND_PRODUCT_DOCS",
    "campaign_target": "OUTSEND_CAMPAIGN_TARGET",
    "booking_link": "OUTSEND_BOOKING_LINK",
    "ai_model": "OUTSEND_AI_MODEL",
    "llm_api_key": "OUTSEND_LLM_API_KEY",
    "llm_api_base": "OUTSEND_LLM_API_BASE",
    "operator_name": "OUTSEND_OPERATOR_NAME",
    "operator_email": "OUTSEND_OPERATOR_EMAIL",
    "mailbox_address": "OUTSEND_MAILBOX_ADDRESS",
    "mailbox_password": "OUTSEND_MAILBOX_PASSWORD",
    "smtp_host": "OUTSEND_SMTP_HOST",
    "smtp_port": "OUTSEND_SMTP_PORT",
    "imap_host": "OUTSEND_IMAP_HOST",
    "imap_port": "OUTSEND_IMAP_PORT",
    "signature": "OUTSEND_SIGNATURE",
}


class SiteConfig(models.Model):
    """The answers, as a singleton. `load()` is the only way anything reads it."""

    # ── what you sell, and to whom ────────────────────────────────
    product_docs = models.TextField(blank=True, default="")
    campaign_target = models.TextField(blank=True, default="")
    # Never required: the sender renders its whole booking block only when there is one.
    booking_link = models.CharField(max_length=500, blank=True, default="")

    # ── the model that judges and writes ──────────────────────────
    # A pydantic-ai `provider:model` id — the provider lives inside the string, so there
    # is no second field to drift out of sync.
    ai_model = models.CharField(max_length=200, blank=True, default="")
    llm_api_key = models.CharField(max_length=500, blank=True, default="")
    # Only the openai_compatible provider reads this one.
    llm_api_base = models.CharField(max_length=500, blank=True, default="")

    # ── finding people ────────────────────────────────────────────
    # BetterContact's key powers discovery (free) as well as enrichment (paid); Apollo's
    # only resolves an address, so it never stands alone.
    bettercontact_api_key = models.CharField(max_length=500, blank=True, default="")
    apollo_api_key = models.CharField(max_length=500, blank=True, default="")
    email_finder = models.CharField(max_length=32, blank=True, default="")

    # ── the operator ──────────────────────────────────────────────
    # The name signs the mail; the address is where the store keys this install and where
    # the newsletter goes. Both children turn these into the one Django `User` they share.
    operator_name = models.CharField(max_length=200, blank=True, default="")
    operator_email = models.EmailField(blank=True, default="")
    # ISO-3166 alpha-2 — the operator's *jurisdiction*, not a target market.
    country_code = models.CharField(max_length=2, blank=True, default="")
    # An acceptance somebody gave is a record. It is kept because it was given, and the
    # children are told about it on every run because they keep nothing.
    accepted_legal_notice = models.BooleanField(default=False)
    # Consent, and never a default: silence is not a yes in any jurisdiction.
    newsletter = models.BooleanField(default=False)

    # ── the mailbox this install sends from ───────────────────────
    # The password is the provider's app password — a Google box rejects a login password
    # outright. Host and port stay blank unless the operator's provider needs them; the
    # sender's own model carries the Google Workspace defaults, and exporting a blank
    # would override them with nothing.
    mailbox_address = models.EmailField(blank=True, default="")
    mailbox_password = models.CharField(max_length=500, blank=True, default="")
    smtp_host = models.CharField(max_length=200, blank=True, default="")
    smtp_port = models.CharField(max_length=8, blank=True, default="")
    imap_host = models.CharField(max_length=200, blank=True, default="")
    imap_port = models.CharField(max_length=8, blank=True, default="")
    signature = models.TextField(blank=True, default="")

    # ── the contacts store ────────────────────────────────────────
    # Minted by the hub rather than typed, and kept so an install keeps one identity
    # across runs instead of registering itself on each one.
    contacts_api_token = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        verbose_name = "Site Configuration"
        verbose_name_plural = "Site Configuration"

    def __str__(self):
        return "Site Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "SiteConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def export(self) -> dict[str, str]:
        """This row as the environment both children read.

        A blank field exports nothing at all rather than an empty string: to a child,
        unset means *use your default* (the sender's SMTP host, the finder's hub URL),
        while a blank value means the operator asked for nothing there. Booleans go as
        `true`/`false`, the only spellings the finder's gate accepts either way.
        """
        environment = {}
        # The two maps are walked separately, never merged: they are keyed by *field*,
        # and most fields appear in both, so merging them would silently keep one
        # child's variable and drop the other's.
        for mapping in (FINDER_ENV, SENDER_ENV):
            for field, variable in mapping.items():
                value = getattr(self, field)
                if isinstance(value, bool):
                    environment[variable] = "true" if value else "false"
                elif str(value).strip():
                    environment[variable] = str(value).strip()
        return environment
