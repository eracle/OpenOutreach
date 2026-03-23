import json
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

    first_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100, blank=True, default="")
    company_name = models.CharField(max_length=200, blank=True, default="")
    linkedin_url = models.URLField(max_length=200, blank=True, default="", unique=True)
    public_identifier = models.CharField(max_length=200, blank=True, default="")
    description = models.TextField(blank=True, default="")
    embedding = models.BinaryField(null=True, blank=True)
    disqualified = models.BooleanField(default=False)
    creation_date = models.DateTimeField(default=timezone.now)
    update_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        name = f"{self.first_name} {self.last_name}".strip()
        if self.disqualified:
            name = f"({_('Disqualified')}) {name}"
        if self.company_name:
            return f"{name}, {self.company_name}"
        return name or self.public_identifier or self.linkedin_url

    @property
    def full_name(self):
        name = f"{self.first_name} {self.last_name}".strip()
        if self.disqualified:
            name = f"({_('Disqualified')}) {name}"
        return name

    # ------------------------------------------------------------------
    # Lazy accessors — fetch / compute on first access, cache in DB
    # ------------------------------------------------------------------

    def get_profile(self, session) -> dict | None:
        """Parsed profile dict. Fetches from Voyager API if not yet enriched."""
        if not self.description:
            self._enrich(session)
        return json.loads(self.description) if self.description else None

    def get_urn(self, session) -> str | None:
        """LinkedIn URN. Chains through get_profile."""
        profile = self.get_profile(session)
        return profile.get("urn") if profile else None

    def get_embedding(self, session) -> np.ndarray | None:
        """384-dim embedding. Chains through get_profile → embed."""
        if self.embedding is None:
            profile = self.get_profile(session)
            if profile:
                self._embed(profile)
        return self.embedding_array

    def to_profile_dict(self) -> dict | None:
        """Standard profile dict shape used by qualifiers and pools.

        Reads existing data only — does not trigger enrichment.
        Returns None if the lead has no public_identifier.
        """
        from linkedin.db.urls import url_to_public_id

        profile = json.loads(self.description) if self.description else {}
        public_id = self.public_identifier
        if not public_id:
            public_id = url_to_public_id(self.linkedin_url) if self.linkedin_url else ""
        if not public_id:
            return None
        return {
            "lead_id": self.pk,
            "public_identifier": public_id,
            "url": self.linkedin_url or "",
            "profile": profile,
            "meta": {},
        }

    def _enrich(self, session):
        """Fetch profile from Voyager API and save to DB fields."""
        from linkedin.api.client import PlaywrightLinkedinAPI

        session.ensure_browser()
        api = PlaywrightLinkedinAPI(session=session)
        profile, _raw = api.get_profile(public_identifier=self.public_identifier)
        if not profile:
            return

        self.first_name = profile.get("first_name", "") or ""
        self.last_name = profile.get("last_name", "") or ""
        positions = profile.get("positions", [])
        if positions:
            self.company_name = positions[0].get("company_name", "") or ""
        self.description = json.dumps(profile, ensure_ascii=False, default=str)
        self.save()

    def _embed(self, profile: dict):
        """Compute embedding from profile and save to DB."""
        from linkedin.ml.embeddings import embed_text
        from linkedin.ml.profile_text import build_profile_text

        text = build_profile_text({"profile": profile})
        emb = embed_text(text)
        self.embedding = emb.tobytes()
        self.save(update_fields=["embedding"])

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
