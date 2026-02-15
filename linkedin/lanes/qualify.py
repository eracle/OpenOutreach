# linkedin/lanes/qualify.py
"""Qualification lane: embedding + LLM-based lead qualification with active learning."""
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import set_profile_state
from linkedin.ml.qualifier import QualificationScorer
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)


class QualifyLane:
    def __init__(self, session, scorer: QualificationScorer):
        self.session = session
        self.scorer = scorer
        self._cfg = CAMPAIGN_CONFIG

    def can_execute(self) -> bool:
        """True if there are unembedded ENRICHED profiles or unlabeled profiles to qualify."""
        from linkedin.db.crm_profiles import get_enriched_profiles
        from linkedin.ml.embeddings import get_embedded_lead_ids, get_positive_centroid, get_unlabeled_profiles_by_similarity

        # Legacy: ENRICHED profiles not yet in DuckDB need embedding first
        profiles = get_enriched_profiles(self.session)
        if profiles:
            embedded_ids = get_embedded_lead_ids()
            if any(self._lead_id_for(p) not in embedded_ids for p in profiles):
                return True

        centroid = get_positive_centroid()
        if centroid is None:
            return False

        unlabeled = get_unlabeled_profiles_by_similarity(limit=1)
        return len(unlabeled) > 0

    def execute(self):
        """Embed one profile or qualify one profile per tick."""
        # Phase 1: embed profiles that don't have embeddings yet (legacy backfill)
        if self._embed_next_profile():
            return

        # Phase 2: qualify embedded profiles using active learning
        self._qualify_next_profile()

    def _embed_next_profile(self) -> bool:
        """Embed one ENRICHED profile that lacks an embedding. Returns True if work was done."""
        from linkedin.db.crm_profiles import get_enriched_profiles
        from linkedin.ml.embeddings import embed_profile, get_embedded_lead_ids

        profiles = get_enriched_profiles(self.session)
        if not profiles:
            return False

        embedded_ids = get_embedded_lead_ids()

        for p in profiles:
            lead_id = self._lead_id_for(p)
            if lead_id is None or lead_id in embedded_ids:
                continue

            public_id = p["public_identifier"]
            profile_data = p.get("profile") or {}

            if embed_profile(lead_id, public_id, profile_data):
                logger.info("%s %s", public_id, colored("EMBEDDED", "yellow"))
                return True

        return False

    def _qualify_next_profile(self):
        """Qualify one embedded profile using active learning."""
        from linkedin.ml.embeddings import get_unlabeled_profiles_by_similarity
        from linkedin.ml.qualifier import qualify_profile_llm

        candidates = get_unlabeled_profiles_by_similarity(limit=1)
        if not candidates:
            return

        candidate = candidates[0]
        lead_id = candidate["lead_id"]
        public_id = candidate["public_identifier"]
        embedding = candidate["embedding"]

        high_threshold = self._cfg["qualification_high_threshold"]
        low_threshold = self._cfg["qualification_low_threshold"]
        uncertainty_threshold = self._cfg["qualification_uncertainty_threshold"]

        # Determine whether to use classifier auto-decision or LLM
        if self.scorer._trained:
            mean_prob, std_prob = self.scorer.predict(embedding)

            if mean_prob >= high_threshold and std_prob < uncertainty_threshold:
                self._record_decision(lead_id, public_id, label=1,
                                      reason=f"auto-accept (mean={mean_prob:.3f}, std={std_prob:.3f})")
                return

            if mean_prob <= low_threshold and std_prob < uncertainty_threshold:
                self._record_decision(lead_id, public_id, label=0,
                                      reason=f"auto-reject (mean={mean_prob:.3f}, std={std_prob:.3f})")
                return

            logger.debug(
                "%s uncertain (mean=%.3f, std=%.3f) — querying LLM",
                public_id, mean_prob, std_prob,
            )

        # LLM qualification (bootstrap phase or uncertain)
        profile_text = self._get_profile_text(lead_id)
        if not profile_text:
            logger.warning("No profile text for lead %d — skipping", lead_id)
            return

        try:
            label, reason = qualify_profile_llm(profile_text)
        except Exception:
            logger.exception("LLM qualification failed for %s", public_id)
            return

        self._record_decision(lead_id, public_id, label, reason)

    def _record_decision(self, lead_id: int, public_id: str, label: int, reason: str):
        """Store label, transition CRM state, log, and retrain if needed."""
        from linkedin.ml.embeddings import store_label

        store_label(lead_id, label=label, reason=reason)

        new_state = ProfileState.QUALIFIED if label == 1 else ProfileState.DISQUALIFIED
        set_profile_state(self.session, public_id, new_state.value)

        decision = "QUALIFIED" if label == 1 else "REJECTED"
        color = "green" if label == 1 else "red"
        logger.info("%s %s: %s", public_id, colored(decision, color, attrs=["bold"]), reason[:80])

        if self.scorer.needs_retrain():
            logger.info(colored("Retraining qualification classifier...", "cyan", attrs=["bold"]))
            self.scorer.train()

    def _get_profile_text(self, lead_id: int) -> str | None:
        """Load profile JSON from CRM Lead and build text."""
        import json

        from crm.models import Lead

        from linkedin.ml.profile_text import build_profile_text

        try:
            lead = Lead.objects.get(pk=lead_id)
        except Lead.DoesNotExist:
            return None

        if not lead.description:
            return None

        try:
            profile_data = json.loads(lead.description)
        except (json.JSONDecodeError, TypeError):
            return None

        return build_profile_text({"profile": profile_data})

    @staticmethod
    def _lead_id_for(profile: dict) -> int | None:
        """Resolve lead_id from profile dict (CRM lookup by URL)."""
        from crm.models import Lead

        from linkedin.db.crm_profiles import public_id_to_url

        public_id = profile.get("public_identifier")
        if not public_id:
            return None

        url = public_id_to_url(public_id)
        lead = Lead.objects.filter(website=url).first()
        return lead.pk if lead else None
