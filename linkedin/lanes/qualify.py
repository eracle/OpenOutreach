# linkedin/lanes/qualify.py
"""Qualification lane: entropy-based active learning with GPC model."""
from __future__ import annotations

import logging

import numpy as np
from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)


class QualifyLane:
    def __init__(self, session, qualifier: BayesianQualifier):
        self.session = session
        self.qualifier = qualifier
        self._cfg = CAMPAIGN_CONFIG

    def can_execute(self) -> bool:
        """True if there are unembedded leads or unlabeled profiles to qualify."""
        from linkedin.db.crm_profiles import get_leads_for_qualification
        from linkedin.ml.embeddings import get_embedded_lead_ids, get_unlabeled_profiles

        # Leads awaiting qualification that need embedding first
        leads = get_leads_for_qualification(self.session)
        if leads:
            embedded_ids = get_embedded_lead_ids()
            if any(l["lead_id"] not in embedded_ids for l in leads):
                return True

        unlabeled = get_unlabeled_profiles(limit=1)
        return len(unlabeled) > 0

    def execute(self):
        """Embed one profile or qualify one profile per tick."""
        logger.info(colored("▶ qualify", "blue", attrs=["bold"]))
        # Phase 1: embed leads that don't have embeddings yet
        if self._embed_next_profile():
            return

        # Phase 2: qualify embedded profiles using BALD-based active learning
        self._qualify_next_profile()

    def _embed_next_profile(self) -> bool:
        """Embed one lead that lacks an embedding. Returns True if work was done."""
        from linkedin.db.crm_profiles import get_leads_for_qualification
        from linkedin.ml.embeddings import embed_profile, get_embedded_lead_ids

        leads = get_leads_for_qualification(self.session)
        if not leads:
            return False

        embedded_ids = get_embedded_lead_ids()

        for lead_data in leads:
            lead_id = lead_data["lead_id"]
            if lead_id in embedded_ids:
                continue

            public_id = lead_data["public_identifier"]
            profile_data = lead_data.get("profile") or {}

            if embed_profile(lead_id, public_id, profile_data):
                logger.info("%s %s", public_id, colored("EMBEDDED", "yellow"))
                return True

        return False

    def _qualify_next_profile(self):
        """Select candidate via balance-driven explore/exploit, then auto-decide or query LLM.

        When negatives outnumber positives, exploit by selecting the candidate
        with highest predicted probability (seek a likely positive label).
        Otherwise, explore by selecting the candidate with highest BALD score
        (seek the most informative label).  This keeps the training set balanced
        and ensures promising profiles get qualified sooner.
        """
        from linkedin.ml.embeddings import get_all_unlabeled_embeddings
        from linkedin.ml.qualifier import qualify_profile_llm

        candidates = get_all_unlabeled_embeddings()
        if not candidates:
            return

        entropy_threshold = self._cfg["qualification_entropy_threshold"]

        # Balance-driven candidate selection: explore vs exploit
        selection_score = None  # (strategy_label, score_value)
        if len(candidates) == 1:
            candidate = candidates[0]
        else:
            embeddings = np.array([c["embedding"] for c in candidates], dtype=np.float32)
            n_neg, n_pos = self.qualifier.class_counts

            if n_neg > n_pos:
                # Seek a likely positive — exploit by highest predicted prob
                scores = self.qualifier.predicted_probs(embeddings)
                strategy = "exploit (p)"
            else:
                # Seek a likely negative — explore via BALD
                scores = self.qualifier.bald_scores(embeddings)
                strategy = "explore (BALD)"

            if scores is None:
                logger.debug("GPC not fitted yet — selecting first candidate (FIFO)")
                candidate = candidates[0]
            else:
                best_idx = int(np.argmax(scores))
                candidate = candidates[best_idx]
                selection_score = (strategy, float(scores[best_idx]))
                logger.info("Strategy: %s (neg=%d, pos=%d)",
                            colored(strategy, "cyan", attrs=["bold"]), n_neg, n_pos)

        lead_id = candidate["lead_id"]
        public_id = candidate["public_identifier"]
        embedding = candidate["embedding"]

        result = self.qualifier.predict(embedding)

        # Auto-decide if model is fitted and predictive entropy is below threshold
        if result is not None:
            pred_prob, entropy = result
            if entropy < entropy_threshold:
                label = 1 if pred_prob >= 0.5 else 0
                decision = "auto-accept" if label == 1 else "auto-reject"
                reason = f"{decision} (prob={pred_prob:.3f}, entropy={entropy:.4f})"
                self._record_decision(lead_id, public_id, embedding, label, reason)
                return

            sel = f", {selection_score[0]}={selection_score[1]:.4f}" if selection_score else ""
            logger.debug(
                "%s uncertain (prob=%.3f, entropy=%.4f%s) — querying LLM",
                public_id, pred_prob, entropy, sel,
            )
        else:
            logger.debug(
                "%s GPC not fitted (%d obs) — querying LLM",
                public_id, self.qualifier.n_obs,
            )

        # LLM qualification (cold start or uncertain)
        profile_text = self._get_profile_text(lead_id)
        if not profile_text:
            logger.warning("No profile text for lead %d — disqualifying", lead_id)
            self._record_decision(lead_id, public_id, embedding, 0, "no profile text available")
            return

        campaign = self.session.campaign
        label, reason = qualify_profile_llm(
            profile_text,
            product_docs=campaign.product_docs,
            campaign_objective=campaign.campaign_objective,
        )
        self._record_decision(lead_id, public_id, embedding, label, reason)

    def _record_decision(self, lead_id: int, public_id: str, embedding: np.ndarray, label: int, reason: str):
        """Store label, update model, promote or disqualify Lead."""
        from linkedin.db.crm_profiles import disqualify_lead, promote_lead_to_contact
        from linkedin.ml.embeddings import store_label

        store_label(lead_id, label=label, reason=reason)
        self.qualifier.update(embedding, label)

        if label == 1:
            try:
                promote_lead_to_contact(self.session, public_id)
            except ValueError as e:
                # Lead has no Company — auto-disqualify
                logger.warning("Cannot promote %s: %s — disqualifying", public_id, e)
                disqualify_lead(self.session, public_id, reason=str(e))
                return
        else:
            disqualify_lead(self.session, public_id, reason=reason)

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
