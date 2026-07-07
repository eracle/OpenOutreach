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

    # The discovery provider's per-person URL — the opaque identity and lookup
    # key. Stored, never fetched.
    profile_url = models.URLField(max_length=200, unique=True)
    # ISO-3166 alpha-2 of the lead's location, stamped from the discovery ICP.
    # Drives the contacts-store geo-gate; blank = unknown (→ never contributed).
    country_code = models.CharField(max_length=2, blank=True, default="")
    embedding = models.BinaryField(null=True, blank=True)
    # Firmographic text built from the Lead Finder row at discovery (same fields as
    # the embedding), fed to the LLM qualifier. No LinkedIn re-scrape.
    profile_text = models.TextField(blank=True, default="")
    # Work email from the enrichment API (BetterContact); null = not found / not yet
    # resolved. Written by the find-email leg once the lead is rank-gated.
    email = models.EmailField(null=True, blank=True, default=None)
    disqualified = models.BooleanField(default=False)
    creation_date = models.DateTimeField(default=timezone.now)
    update_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        label = self.profile_url or f"Lead#{self.pk}"
        if self.disqualified:
            return f"({_('Disqualified')}) {label}"
        return label

    # ------------------------------------------------------------------
    # Accessors — the embedding and profile text are cached at discovery; the
    # email is resolved via the two-leg paid finder (find_email submits, then
    # collect_email polls the job). Nothing is fetched live.
    # ------------------------------------------------------------------

    def to_profile_dict(self) -> dict:
        """Standard profile dict shape used by qualifiers and pools.

        The rich profile is not carried — the identity key (to look up the cached
        embedding) is all the ranking/lookup legs read.
        """
        return {
            "lead_id": self.pk,
            "profile_url": self.profile_url,
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
        from openoutreach.crm.models import DealState

        deals = Deal.objects.filter(
            campaign=campaign, lead_id__isnull=False,
        ).values_list("lead_id", "state", "outcome")

        label_by_lead: dict[int, int] = {}
        for lid, state, outcome in deals:
            if state == DealState.FAILED:
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
