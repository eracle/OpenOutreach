import logging

import numpy as np
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class Lead(models.Model):
    class Meta:
        verbose_name = _("Lead")
        verbose_name_plural = _("Leads")

    linkedin_url = models.URLField(max_length=200, unique=True)
    public_identifier = models.CharField(max_length=200, unique=True)
    urn = models.CharField(max_length=200, null=True, blank=True, unique=True, db_index=True)
    embedding = models.BinaryField(null=True, blank=True)
    # Email enrichment — one field per source (roadmap: p1-e1 storage decision):
    #   contact_info — raw LinkedIn contact-info overlay {email, emails, phone_numbers},
    #                  captured once at CONNECTED; null = never scraped (idempotency flag).
    #   api_email    — enrichment-API result (BetterContact); its writer lands with the
    #                  finder slice (p1-e3). null = not found.
    contact_info = models.JSONField(null=True, blank=True, default=None)
    api_email = models.EmailField(null=True, blank=True, default=None)
    disqualified = models.BooleanField(default=False)
    creation_date = models.DateTimeField(default=timezone.now)
    update_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        label = self.public_identifier or self.linkedin_url or f"Lead#{self.pk}"
        if self.disqualified:
            return f"({_('Disqualified')}) {label}"
        return label

    # ------------------------------------------------------------------
    # Lazy accessors — re-scrape live on demand, persist only the
    # derived caches we still keep (urn, embedding).
    # ------------------------------------------------------------------

    def get_profile(self, session) -> dict | None:
        """Live Voyager scrape of the parsed profile dict.

        No DB caching: the heavy fields (raw JSON, names, company) live
        only in memory for as long as the caller holds the dict. We do
        opportunistically populate ``self.urn`` if it's still null and
        the scrape returns one.
        """
        from linkedin_cli.api.client import PlaywrightLinkedinAPI
        from linkedin_cli.exceptions import ProfileInaccessibleError

        session.ensure_browser()
        api = PlaywrightLinkedinAPI(session=session)
        try:
            profile, _raw = api.get_profile(public_identifier=self.public_identifier)
        except ProfileInaccessibleError:
            return None
        if not profile:
            return None

        urn = profile.get("urn") or None
        if urn and self.urn != urn:
            if Lead.objects.filter(urn=urn).exclude(pk=self.pk).exists():
                logger.warning("URN %s already owned by another lead — skipping for %s", urn, self.public_identifier)
            else:
                self.urn = urn
                self.save(update_fields=["urn"])

        return profile

    def capture_contact_info(self, session) -> None:
        """Scrape + persist the LinkedIn contact-info overlay once the lead is a
        1st-degree connection.

        Idempotent: a non-null ``contact_info`` — even an empty
        ``{email: None, emails: [], phone_numbers: []}`` — means we already tried,
        so a re-connect or a second campaign does not re-scrape. The raw overlay is
        stored unfiltered (work-vs-personal cleaning is downstream, in dbt).

        Errors are left to the caller: capture is driven from ``set_profile_state``
        on the CONNECTED transition, which owns the best-effort guard
        (``ProfileInaccessibleError``/``IOError`` swallowed; ``AuthenticationError``
        propagates to the daemon's reauth handler).
        """
        if self.contact_info is not None:
            return
        from linkedin_cli.api.client import PlaywrightLinkedinAPI

        session.ensure_browser()
        api = PlaywrightLinkedinAPI(session=session)
        contact, _raw = api.get_contact_info(public_identifier=self.public_identifier)
        self.contact_info = contact
        self.save(update_fields=["contact_info"])

    def resolve_api_email(self) -> bool | None:
        """Resolve + persist a work email via the finder, once the lead qualifies.

        Returns True on a hit (``api_email`` set, cached — never re-resolved),
        False on a genuine miss (finder ran, found nothing → caller parks the
        Deal in NO_EMAIL), and None when the finder couldn't run (no key, or
        the service was unreachable → caller leaves the Deal QUALIFIED to retry).
        A miss is free to retry — BetterContact bills only usable hits.
        """
        if self.api_email:
            return True
        from openoutreach.emails.finder import FinderQuery, FinderUnavailable, resolve_email

        try:
            result = resolve_email(FinderQuery(linkedin_url=self.linkedin_url))
        except FinderUnavailable:
            return None
        if result:
            self.api_email = result.email
            self.save(update_fields=["api_email"])
            return True
        return False

    def get_urn(self, session) -> str:
        """LinkedIn URN. Reads cached column; falls back to a live scrape."""
        if self.urn:
            return self.urn
        self.get_profile(session)  # sets self.urn as side-effect
        if self.urn:
            return self.urn
        raise ValueError(f"Lead {self.pk}: could not resolve URN after re-fetch")

    def get_embedding(self, session) -> np.ndarray | None:
        """384-dim embedding. Lazy: scrapes + embeds on first access."""
        if self.embedding is None:
            profile = self.get_profile(session)
            if profile:
                self.embed_from_profile(profile)
        return self.embedding_array

    def embed_from_profile(self, profile: dict) -> None:
        """Compute and persist the 384-dim embedding from an in-hand profile.

        Used by callers that already have a freshly parsed profile dict,
        so they can skip the scrape that ``get_embedding`` would trigger.
        """
        from openoutreach.linkedin.ml.embeddings import embed_text
        from openoutreach.linkedin.ml.profile_text import build_profile_text

        text = build_profile_text({"profile": profile})
        emb = embed_text(text)
        self.embedding = emb.tobytes()
        self.save(update_fields=["embedding"])

    def to_profile_dict(self) -> dict:
        """Standard profile dict shape used by qualifiers and pools.

        The ``profile`` key is intentionally absent — callers that need
        the full Voyager-parsed dict must call ``get_profile(session)``
        themselves (live scrape).
        """
        return {
            "lead_id": self.pk,
            "public_identifier": self.public_identifier,
            "url": self.linkedin_url or "",
            "meta": {},
        }

    @property
    def embedding_array(self) -> np.ndarray | None:
        """384-dim float32 numpy array from stored bytes, or None."""
        if self.embedding is None:
            return None
        return np.frombuffer(bytes(self.embedding), dtype=np.float32).copy()

    @embedding_array.setter
    def embedding_array(self, arr: np.ndarray):
        self.embedding = np.asarray(arr, dtype=np.float32).tobytes()

    @classmethod
    def get_labeled_arrays(cls, campaign) -> tuple[np.ndarray, np.ndarray]:
        """Labeled embeddings for a campaign as (X, y) numpy arrays for warm start.

        Labels are derived from Deal state and outcome:
        - label=1: Deals at any non-FAILED state (QUALIFIED and beyond)
        - label=0: FAILED Deals with outcome "wrong_fit" (LLM rejection)
        - Skipped: FAILED Deals with other outcomes (operational failures)
        """
        from openoutreach.crm.models import Outcome
        from openoutreach.crm.models.deal import Deal
        from linkedin_cli.enums import ProfileState

        deals = Deal.objects.filter(
            campaign=campaign, lead_id__isnull=False,
        ).values_list("lead_id", "state", "outcome")

        label_by_lead: dict[int, int] = {}
        for lid, state, outcome in deals:
            if state == ProfileState.FAILED:
                if outcome == Outcome.WRONG_FIT:
                    label_by_lead[lid] = 0
            else:
                label_by_lead[lid] = 1

        if not label_by_lead:
            return np.empty((0, 384), dtype=np.float32), np.empty(0, dtype=np.int32)

        leads_with_emb = dict(
            cls.objects.filter(pk__in=label_by_lead, embedding__isnull=False)
            .values_list("pk", "embedding")
        )

        X_list, y_list = [], []
        for lid, label in label_by_lead.items():
            emb = leads_with_emb.get(lid)
            if emb is None:
                continue
            X_list.append(np.frombuffer(bytes(emb), dtype=np.float32))
            y_list.append(label)

        if not X_list:
            return np.empty((0, 384), dtype=np.float32), np.empty(0, dtype=np.int32)

        return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.int32)
