# openoutreach/core/conf.py
from __future__ import annotations

from pathlib import Path


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
PROMPTS_DIR = Path(__file__).parent / "templates" / "prompts"

# ----------------------------------------------------------------------
# The three send guards used to live here — warm capacity (the per-box daily
# ceiling, measured from the box's own Sent folder rather than declared), send
# pacing (a 3.5–4.5 minute gap between two first emails), and the sending window
# (Mon–Fri, 08:00–20:00 in the operator's own time). Together they were most of
# this file. They went to OpenOutSend with the code they governed.
#
# They are worth knowing about if anything here ever grows an outbound leg again,
# because the reasoning was hard-won and is recorded in `cold_outreach/README.md`
# on that side: receivers punish *rate* and *volume* separately and a recipient
# reads the *hour*, so one guard could not have covered the other two.
#
# Nothing replaced them, because a finder emits nothing to pace.
# ----------------------------------------------------------------------
# collect_email poll backoff — the bound leg that polls an in-flight paid
# lookup. Each still-running poll doubles the delay (BASE·2^attempt, capped at
# MAX); past DEADLINE the collect leg gives up and reverts the deal to
# READY_TO_FIND_EMAIL for a fresh submit. A provider job resolves in
# seconds-to-minutes, so these are short (unlike the retired channel's
# connect-accept poll, which backed off in hours). A future provider (Apollo, …)
# would carry its own triple.
#
#
# **The backoff is uncapped and there is no deadline.** Both used to exist, and
# together they made the failure worse than the outage: past the deadline the leg
# abandoned the job and reverted the deal to READY_TO_FIND_EMAIL, where the submit
# leg picked it up and paid for a *new* job — a hot resubmit loop against a
# provider already struggling. Measured on a live install during what the provider
# later confirmed was a multi-day infrastructure incident: 418 submits and 4,512
# polls in a week for ~40 leads, none of which ever terminated.
#
# Doubling without a rail fixes both halves. An unterminated job is *queued*, not
# lost, so the right move is to keep the same request_id and ask later — no second
# submit, and the deal stays at FINDING_EMAIL where nothing re-selects it. And
# doubling reaches long waits cheaply: 5s → a week in 17 polls, ~30 for a month.
# A capped backoff would poll a week-long outage 10,000 times to learn the same
# thing. Nothing is ever written off either, which matters because a timeout is
# evidence about the provider, not about the lead — when the queue drains, the
# next poll simply lands and the deal proceeds with no intervention.
# ----------------------------------------------------------------------
COLLECT_BACKOFF_BASE_S = 5

# The rail is on the *interval*, not on the number of attempts: polling stretches
# to a month and then stays there forever. That is not a give-up in disguise —
# nothing is abandoned or relabelled, the job is simply checked monthly instead of
# ever more rarely. It exists because unbounded doubling stops being representable:
# 5s·2^41 is ~348,000 years, `datetime` raises OverflowError, the handler dies
# before minting its successor, and the deal is stranded at FINDING_EMAIL with no
# pending task — the one outcome the whole design is meant to prevent. Past a
# month the difference between "later" and "much later" has no practical meaning
# anyway, while the difference between "later" and "never polled again" is total.
COLLECT_BACKOFF_MAX_S = 30 * 24 * 3600

# ``COLLECT_TODAY_HORIZON_S`` stood here — past it, a lookup in a long backoff
# stopped counting against today's send headroom, so a handful of stalled jobs
# could not wedge the pipeline shut. It has no meaning without send headroom to
# count against: nothing gates buying an address on anything but having a provider.

# ----------------------------------------------------------------------
# Campaign config (timing + ML defaults — hardcoded, no YAML)
# ----------------------------------------------------------------------
CAMPAIGN_CONFIG = {
    "qualification_n_mc_samples": 100,
    # GP confidence gate: P(f>0.5) above this promotes QUALIFIED → READY_TO_FIND_EMAIL
    # (rations the paid BetterContact lookup to leads the model is confident about), and
    # with it the LLM call — a lead the model would not buy an address for is not one it
    # spends a verdict judging (pipeline/top_up.py).
    #
    # 0.7, down from 0.9. The bar is what the exploit state has to clear to do anything at
    # all, and at 0.9 a live campaign cleared it with nothing: 7 leads waited to be ranked
    # while every pass fell through to discovery, so the pool widened and the class balance
    # never moved. A gate the pool cannot reach rations the pipeline shut rather than
    # rationing the spend.
    "min_gp_confidence": 0.7,
    # There is no discovery cadence knob. Growing the vocabulary used to be an LLM call
    # worth rationing ("mint_every_n_qualified"); it is now a tokenize-and-count over a
    # few hundred profiles (pipeline/vocabulary.py), so it simply runs every pass. The
    # walk's only other constant is the df≥2 admission floor, which lives with the code
    # that measured it. Discovery steering is arithmetic over labels — no threshold, no
    # confidence gate, no model. See pipeline/select.py.
    "embedding_model": "BAAI/bge-small-en-v1.5",
}


