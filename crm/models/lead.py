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
        from linkedin.api.client import PlaywrightLinkedinAPI

        session.ensure_browser()
        api = PlaywrightLinkedinAPI(session=session)
        profile, _raw = api.get_profile(public_identifier=self.public_identifier)
        if not profile:
            return None

        urn = profile.get("urn") or None
        if urn and self.urn != urn:
            self.urn = urn
            self.save(update_fields=["urn"])

        return profile

    def get_urn(self, session) -> str:
        """LinkedIn URN. Reads cached column; falls back to a live scrape."""
        if self.urn:
            return self.urn
        profile = self.get_profile(session)
        if profile and profile.get("urn"):
            self.urn = profile["urn"]
            self.save(update_fields=["urn"])
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
        from linkedin.ml.embeddings import embed_text
        from linkedin.ml.profile_text import build_profile_text

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

        Labels are derived from Deal state and closing_reason:
        - label=1: Deals at any non-FAILED state (QUALIFIED and beyond)
        - label=0: FAILED Deals with closing_reason "Disqualified" (LLM rejection)
        - Skipped: FAILED Deals with other closing reasons (operational failures)
        """
        from crm.models import ClosingReason
        from crm.models.deal import Deal
        from linkedin.enums import ProfileState

        deals = Deal.objects.filter(
            campaign=campaign, lead_id__isnull=False,
        ).values_list("lead_id", "state", "closing_reason")

        label_by_lead: dict[int, int] = {}
        for lid, state, cr in deals:
            if state == ProfileState.FAILED:
                if cr == ClosingReason.DISQUALIFIED:
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
