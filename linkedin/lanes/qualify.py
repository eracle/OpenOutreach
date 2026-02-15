# linkedin/lanes/qualify.py
"""Qualification lane: BALD-based active learning with online Bayesian model."""
from __future__ import annotations

import logging

import numpy as np
from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import set_profile_state
from linkedin.ml.qualifier import BayesianQualifier, _binary_entropy
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)


class QualifyLane:
    def __init__(self, session, qualifier: BayesianQualifier):
        self.session = session
        self.qualifier = qualifier
        self._cfg = CAMPAIGN_CONFIG

    def can_execute(self) -> bool:
        """True if there are unembedded ENRICHED profiles or unlabeled profiles to qualify."""
        from linkedin.db.crm_profiles import get_enriched_profiles
        from linkedin.ml.embeddings import get_embedded_lead_ids, get_unlabeled_profiles

        # Legacy: ENRICHED profiles not yet in DuckDB need embedding first
        profiles = get_enriched_profiles(self.session)
        if profiles:
            embedded_ids = get_embedded_lead_ids()
            if any(self._lead_id_for(p) not in embedded_ids for p in profiles):
                return True

        unlabeled = get_unlabeled_profiles(limit=1)
        return len(unlabeled) > 0

    def execute(self):
        """Embed one profile or qualify one profile per tick."""
        logger.info(colored("▶ qualify", "blue", attrs=["bold"]))
        # Phase 1: embed profiles that don't have embeddings yet (legacy backfill)
        if self._embed_next_profile():
            return

        # Phase 2: qualify embedded profiles using BALD-based active learning
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
        """Select the most informative profile via BALD, then auto-decide or query LLM."""
        from linkedin.ml.embeddings import get_all_unlabeled_embeddings
        from linkedin.ml.qualifier import qualify_profile_llm

        candidates = get_all_unlabeled_embeddings()
        if not candidates:
            return

        entropy_threshold = self._cfg["qualification_entropy_threshold"]

        # Select candidate with highest BALD (most informative for the model)
        if len(candidates) == 1:
            candidate = candidates[0]
        else:
            embeddings = np.array([c["embedding"] for c in candidates], dtype=np.float32)
            bald_scores = self.qualifier.bald_scores(embeddings)
            best_idx = int(np.argmax(bald_scores))
            candidate = candidates[best_idx]

        lead_id = candidate["lead_id"]
        public_id = candidate["public_identifier"]
        embedding = candidate["embedding"]

        pred_prob, bald = self.qualifier.predict(embedding)
        entropy = float(_binary_entropy(pred_prob))

        # Auto-decide if predictive entropy is below threshold (model is confident)
        if self.qualifier.n_obs > 0 and entropy < entropy_threshold:
            label = 1 if pred_prob >= 0.5 else 0
            decision = "auto-accept" if label == 1 else "auto-reject"
            reason = f"{decision} (prob={pred_prob:.3f}, entropy={entropy:.4f}, bald={bald:.4f})"
            self._record_decision(lead_id, public_id, embedding, label, reason)
            return

        # LLM qualification (cold start or uncertain)
        logger.debug(
            "%s uncertain (prob=%.3f, entropy=%.4f, bald=%.4f) — querying LLM",
            public_id, pred_prob, entropy, bald,
        )
        profile_text = self._get_profile_text(lead_id)
        if not profile_text:
            logger.warning("No profile text for lead %d — disqualifying", lead_id)
            self._record_decision(lead_id, public_id, embedding, 0, "no profile text available")
            return

        label, reason = qualify_profile_llm(profile_text)
        self._record_decision(lead_id, public_id, embedding, label, reason)

    def _record_decision(self, lead_id: int, public_id: str, embedding: np.ndarray, label: int, reason: str):
        """Store label, update Bayesian posterior online, transition CRM state."""
        from linkedin.ml.embeddings import store_label

        store_label(lead_id, label=label, reason=reason)
        self.qualifier.update(embedding, label)

        new_state = ProfileState.QUALIFIED if label == 1 else ProfileState.DISQUALIFIED
        set_profile_state(self.session, public_id, new_state.value)

        decision = "QUALIFIED" if label == 1 else "REJECTED"
        color = "green" if label == 1 else "red"
        logger.info("%s %s: %s", public_id, colored(decision, color, attrs=["bold"]), reason)

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
