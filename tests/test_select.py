# tests/test_select.py
"""Query selection — the counted, add-only frontier walk.

A node is a keyword set; its value is arithmetic over the label store
(``P̂ = (a + 2·P̂(parent)) / (a + b + 2)``) and nothing here touches a model. The GP used
to score candidates by embedding their keywords; §13 of the roadmap card measured that
against plain counting and the counts won, so these tests assert counting behaviour and
the retirement rules — the two things the walk's correctness rests on.
"""
import numpy as np
import pytest

from openoutreach.core.models import Campaign, Keyword, QueryNode
from openoutreach.core.pipeline import select
from openoutreach.core.pipeline.select import REACH_CAP, LabelStore, token_key
from openoutreach.crm.models import Deal, DealState, Lead, Outcome


def _campaign(**kw):
    defaults = dict(name="C", product_docs="p", campaign_target="t")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


def _node(campaign, pairs, parent=None, **kw):
    node = QueryNode.objects.create(
        campaign=campaign, token_key=token_key(pairs), parent=parent, **kw)
    node.keywords.set(Keyword.rows_for(pairs))
    return node


def _labelled(campaign, profile_text, qualified):
    """A lead with a verdict — the only evidence the walk reads."""
    lead = Lead.objects.create(
        profile_url=f"https://x/{Lead.objects.count()}", profile_text=profile_text)
    Deal.objects.create(
        lead=lead, campaign=campaign,
        state=DealState.QUALIFIED if qualified else DealState.FAILED,
        outcome="" if qualified else Outcome.WRONG_FIT)
    return lead


class TestTokenKey:
    def test_is_order_independent(self):
        # Add-only over three fields means most nodes are reachable several ways, so
        # identity has to be the set, not the path that reached it.
        a = [("lead_job_title", "founder"), ("lead_job_title", "cto")]
        assert token_key(a) == token_key(list(reversed(a)))

    def test_distinguishes_the_field(self):
        # `marketing` counts 3.9M in job_title and 5.5M in department — different sets.
        assert token_key([("lead_job_title", "x")]) != token_key([("lead_location", "x")])


class TestLabelStore:
    def test_counts_are_containment_over_all_tokens(self, db):
        c = _campaign()
        _labelled(c, "founder cto stealth ai startup", qualified=True)
        _labelled(c, "founder cto fintech", qualified=True)
        _labelled(c, "founder marketing agency", qualified=False)
        store = LabelStore.load(c)

        assert store.counts([("lead_job_title", "founder")]) == (2, 1)
        assert store.counts([("lead_job_title", "founder"), ("lead_job_title", "cto")]) == (2, 0)
        assert store.counts([("lead_job_title", "nobody")]) == (0, 0)

    def test_base_rate_is_the_level_a_depth_one_node_inherits(self, db):
        c = _campaign()
        _labelled(c, "alpha", qualified=True)
        _labelled(c, "beta", qualified=True)
        _labelled(c, "gamma", qualified=False)
        # Laplace: (2 + 1) / (3 + 2)
        assert LabelStore.load(c).base_rate == pytest.approx(3 / 5)

    def test_empty_store_is_an_even_prior(self, db):
        assert LabelStore.load(_campaign()).base_rate == 0.5

    def test_an_all_rejection_campaign_still_has_a_usable_level(self, db):
        # The state the anchors exist for, so a common one. A raw rate of 0 here makes
        # every unlabelled node's Beta degenerate (α = a + 2·0 = 0).
        c = _campaign()
        for _ in range(20):
            _labelled(c, "nope", qualified=False)
        assert 0 < LabelStore.load(c).base_rate < 0.5

    def test_an_all_qualified_campaign_stays_below_one(self, db):
        c = _campaign()
        for _ in range(20):
            _labelled(c, "yes", qualified=True)
        assert 0.5 < LabelStore.load(c).base_rate < 1

    def test_cooccurring_only_offers_tokens_seen_with_a_qualified_lead(self, db):
        c = _campaign()
        _labelled(c, "founder cto ai", qualified=True)
        _labelled(c, "founder plumber", qualified=False)
        store = LabelStore.load(c)
        candidates = [("lead_job_title", t) for t in ("cto", "ai", "plumber", "unseen")]

        offered = store.cooccurring([("lead_job_title", "founder")], candidates)

        # `plumber` shares a profile with founder but not a *qualified* one; `unseen`
        # shares none. Both would enter with a=b=0 and be indistinguishable noise.
        assert sorted(offered) == [("lead_job_title", "ai"), ("lead_job_title", "cto")]

    def test_cooccurring_never_offers_a_token_the_node_already_has(self, db):
        c = _campaign()
        _labelled(c, "founder cto", qualified=True)
        store = LabelStore.load(c)
        pairs = [("lead_job_title", "founder")]
        assert ("lead_job_title", "founder") not in store.cooccurring(pairs, pairs)


class TestAnchorsAsPositives:
    """The cold phase's only positives — see ``LabelStore.load``."""

    def test_anchors_count_as_qualified_profiles(self, db):
        c = _campaign(anchor_profiles=["founder cto stealth ai health startup"])
        _labelled(c, "utilities telecom manager", qualified=False)
        store = LabelStore.load(c)

        assert store.qualified_count == 1
        assert store.counts([("lead_job_title", "founder")]) == (1, 0)

    def test_they_unblock_expansion_when_nothing_has_qualified(self, db):
        # Without them this is the closed loop: no qualified profile → nothing
        # co-occurs → the frontier never grows past its one-token seed nodes → the
        # queries stay too broad to qualify anybody → still nothing co-occurs.
        c = _campaign(anchor_profiles=["founder cto health supplements startup"])
        for _ in range(5):
            _labelled(c, "utilities telecom manager", qualified=False)
        store = LabelStore.load(c)
        seed = [("lead_job_title", t) for t in ("founder", "cto", "health", "manager")]
        node = _node(c, [("lead_job_title", "founder")])

        assert select.expand(node, store, seed) == 2  # cto, health — not manager
        assert QueryNode.objects.filter(parent=node).count() == 2

    def test_without_anchors_a_cold_campaign_cannot_expand(self, db):
        c = _campaign(anchor_profiles=[])
        for _ in range(5):
            _labelled(c, "founder utilities telecom", qualified=False)
        store = LabelStore.load(c)
        node = _node(c, [("lead_job_title", "founder")])

        assert select.expand(node, store, [("lead_job_title", "telecom")]) == 0

    def test_anchors_lift_a_matching_node_above_a_rejected_one(self, db):
        c = _campaign(anchor_profiles=["founder cto health supplements"] * 4)
        for _ in range(4):
            _labelled(c, "content manager utilities", qualified=False)
        store = LabelStore.load(c)
        good = _node(c, [("lead_job_title", "founder")])
        bad = _node(c, [("lead_job_title", "content")])

        assert select.estimate(good, store) > select.estimate(bad, store)

    def test_a_real_positive_ends_it_without_a_phase_check(self, db):
        # BayesianQualifier clears anchor_profiles on the first real positive, so the
        # field is empty exactly when the cold phase is over.
        c = _campaign(anchor_profiles=["founder cto invented"])
        assert LabelStore.load(c).qualified_count == 1

        c.anchor_profiles = []
        c.save(update_fields=["anchor_profiles"])
        _labelled(c, "founder cto real", qualified=True)
        store = LabelStore.load(c)

        assert store.qualified_count == 1
        assert store.counts([("lead_job_title", "invented")]) == (0, 0)


class TestEstimate:
    def test_an_unseen_node_sits_at_the_inherited_level(self, db):
        c = _campaign()
        _labelled(c, "alpha", qualified=True)
        _labelled(c, "beta", qualified=False)
        store = LabelStore.load(c)
        node = _node(c, [("lead_job_title", "unseen")])
        # a=b=0, level=0.5 → (0 + 1) / (0 + 0 + 2)
        assert select.estimate(node, store) == pytest.approx(0.5)

    def test_smoothing_points_at_the_parent_not_at_a_half(self, db):
        # The measured design decision: the parent supplies the level, the child's own
        # counts move it off. A thin-evidence child stays near its parent.
        c = _campaign()
        for _ in range(8):
            _labelled(c, "founder ai", qualified=True)
        _labelled(c, "founder ai rare", qualified=True)
        store = LabelStore.load(c)

        parent = _node(c, [("lead_job_title", "founder")])
        child = _node(c, [("lead_job_title", "founder"), ("lead_job_title", "rare")],
                      parent=parent)

        parent_p = select.estimate(parent, store)      # 9 pos, 0 neg → high
        child_p = select.estimate(child, store)        # 1 pos, 0 neg → thin
        assert parent_p > 0.85
        # One observation cannot drag the child far from its parent's level.
        assert abs(child_p - parent_p) < 0.15

    def test_negatives_pull_a_child_below_its_parent(self, db):
        c = _campaign()
        for _ in range(6):
            _labelled(c, "founder ai", qualified=True)
        for _ in range(6):
            _labelled(c, "founder sales", qualified=False)
        store = LabelStore.load(c)

        parent = _node(c, [("lead_job_title", "founder")])
        bad = _node(c, [("lead_job_title", "founder"), ("lead_job_title", "sales")],
                    parent=parent)
        assert select.estimate(bad, store) < select.estimate(parent, store)


class TestFrontier:
    def test_holds_unfired_children_and_fired_veins_together(self, db):
        # One pool: deepening a vein and opening a fresh node are two rows scored the
        # same way, not two policies needing an alternation rule.
        c = _campaign()
        fresh = _node(c, [("lead_job_title", "a")])
        vein = _node(c, [("lead_job_title", "b")], state=QueryNode.State.FIRED,
                     next_offset=100)
        _node(c, [("lead_job_title", "c")], state=QueryNode.State.DEAD)
        _node(c, [("lead_job_title", "d")], state=QueryNode.State.DRAINED)

        assert {n.pk for n in select.frontier(c)} == {fresh.pk, vein.pk}

    def test_next_node_is_none_when_nothing_is_fireable(self, db):
        c = _campaign()
        _node(c, [("lead_job_title", "a")], state=QueryNode.State.DEAD)
        assert select.next_node(c, LabelStore.load(c)) is None

    @pytest.mark.parametrize("qualified", [True, False])
    def test_a_single_class_store_can_still_be_drawn_from(self, db, qualified):
        # Reproduces a live crash: a campaign whose every verdict was a rejection gave
        # base_rate 0, so `rng.beta(0, ...)` raised `ValueError: a <= 0` and killed the
        # find_email task. Both saturated directions must stay drawable.
        c = _campaign()
        for _ in range(20):
            _labelled(c, "seen", qualified=qualified)
        store = LabelStore.load(c)
        _node(c, [("lead_job_title", "seen")])
        _node(c, [("lead_job_title", "unseen")])

        assert select.next_node(c, store) is not None

    def test_greedy_picks_the_best_estimate(self, db, monkeypatch):
        monkeypatch.setattr(select, "THOMPSON", False)
        c = _campaign()
        for _ in range(5):
            _labelled(c, "good", qualified=True)
        for _ in range(5):
            _labelled(c, "bad", qualified=False)
        store = LabelStore.load(c)
        _node(c, [("lead_job_title", "bad")])
        good = _node(c, [("lead_job_title", "good")])

        assert select.next_node(c, store).pk == good.pk

    def test_thompson_still_favours_the_better_node_on_average(self, db):
        # A draw, not a shuffle: width tracks evidence, so a well-measured good node
        # wins most of the time without ever locking the frontier.
        c = _campaign()
        for _ in range(20):
            _labelled(c, "good", qualified=True)
        for _ in range(20):
            _labelled(c, "bad", qualified=False)
        store = LabelStore.load(c)
        _node(c, [("lead_job_title", "bad")])
        good = _node(c, [("lead_job_title", "good")])

        rng = np.random.default_rng(0)
        wins = sum(select.next_node(c, store, rng).pk == good.pk for _ in range(50))
        assert wins > 45


class TestExpansion:
    def test_children_are_the_node_plus_one_co_occurring_token(self, db):
        c = _campaign()
        _labelled(c, "founder cto ai", qualified=True)
        store = LabelStore.load(c)
        parent = _node(c, [("lead_job_title", "founder")])
        candidates = [("lead_job_title", t) for t in ("cto", "ai")]

        assert select.expand(parent, store, candidates) == 2
        children = QueryNode.objects.filter(parent=parent)
        assert {len(n.pairs) for n in children} == {2}

    def test_expansion_is_idempotent(self, db):
        c = _campaign()
        _labelled(c, "founder cto", qualified=True)
        store = LabelStore.load(c)
        parent = _node(c, [("lead_job_title", "founder")])
        candidates = [("lead_job_title", "cto")]

        select.expand(parent, store, candidates)
        assert select.expand(parent, store, candidates) == 0

    def test_a_field_holding_two_tokens_takes_no_third(self, db):
        """Same-field tokens are ANDed inside that field, so a third asks for a title
        carrying all three words — which nobody's does."""
        c = _campaign()
        _labelled(c, "founder cto ai", qualified=True)
        store = LabelStore.load(c)
        parent = _node(c, [("lead_job_title", "founder"), ("lead_job_title", "cto")])

        assert select.expand(parent, store, [("lead_job_title", "ai")]) == 0

    def test_a_capped_field_does_not_stop_another_axis(self, db):
        """The cap is per field: a node full on job title can still narrow on location."""
        c = _campaign()
        _labelled(c, "founder cto oman", qualified=True)
        store = LabelStore.load(c)
        parent = _node(c, [("lead_job_title", "founder"), ("lead_job_title", "cto")])

        assert select.expand(parent, store, [("lead_location", "Oman")]) == 1

    def test_a_closed_axis_never_holds_two_values(self, db):
        """Measured: `lead_seniority ["director founder"]` counts 0 where `["director"]`
        counts 5.0M, and the same for two places. A node that conjoined either would come
        back empty at offset 0 and prune its whole subtree over our own syntax."""
        c = _campaign()
        _labelled(c, "founder director cto oman spain", qualified=True)
        store = LabelStore.load(c)

        senior = _node(c, [("lead_seniority", "director")])
        assert select.expand(senior, store, [("lead_seniority", "founder")]) == 0

        place = _node(c, [("lead_location", "Oman")])
        assert select.expand(place, store, [("lead_location", "Spain")]) == 0

    def test_a_phrase_keyword_matches_on_its_words(self, db):
        """A location is one value carrying several words, and profiles are counted as a
        bag of words — so `California, United States` has to reach a Californian."""
        c = _campaign()
        _labelled(c, "founder california united states", qualified=True)
        store = LabelStore.load(c)
        parent = _node(c, [("lead_job_title", "founder")])

        assert store.counts([("lead_location", "California, United States")]) == (1, 0)
        assert select.expand(parent, store, [("lead_location", "California, United States")]) == 1
        assert select.expand(parent, store, [("lead_location", "Bavaria, Germany")]) == 0

    def test_a_dead_subset_prunes_the_child_before_it_is_created(self, db):
        # The anti-monotone half that survives the lattice being a DAG: a superset of an
        # empty conjunction is empty, whichever parent reaches it.
        c = _campaign()
        _labelled(c, "founder oman", qualified=True)
        store = LabelStore.load(c)
        _node(c, [("lead_location", "oman")], state=QueryNode.State.DEAD)
        parent = _node(c, [("lead_job_title", "founder")])

        assert select.expand(parent, store, [("lead_location", "oman")]) == 0

    def test_a_node_reached_twice_keeps_the_better_parent(self, db):
        c = _campaign()
        for _ in range(10):
            _labelled(c, "founder cto ai", qualified=True)
        for _ in range(10):
            _labelled(c, "cto agency", qualified=False)
        store = LabelStore.load(c)

        strong = _node(c, [("lead_job_title", "founder")])   # all positive
        weak = _node(c, [("lead_job_title", "cto")])         # mixed
        select.expand(weak, store, [("lead_job_title", "founder")])
        child = QueryNode.objects.get(
            token_key=token_key([("lead_job_title", "founder"), ("lead_job_title", "cto")]))
        assert child.parent_id == weak.pk

        select.expand(strong, store, [("lead_job_title", "cto")])
        child.refresh_from_db()
        assert child.parent_id == strong.pk


class TestRetirement:
    def test_offset_zero_kills_the_node_and_its_subtree(self, db):
        c = _campaign()
        parent = _node(c, [("lead_job_title", "a")])
        child = _node(c, [("lead_job_title", "a"), ("lead_job_title", "b")], parent=parent)
        grandchild = _node(c, [("lead_job_title", "a"), ("lead_job_title", "c")], parent=child)

        assert select.retire(parent, at_offset=0) == "dead"
        for node in (parent, child, grandchild):
            node.refresh_from_db()
            assert node.state == QueryNode.State.DEAD

    def test_draining_below_the_cap_also_prunes_the_subtree(self, db):
        # Every match is already a Lead here, so a superset is drawn from an exhausted
        # population — there is nothing new below it.
        c = _campaign()
        parent = _node(c, [("lead_job_title", "a")], next_offset=400)
        child = _node(c, [("lead_job_title", "a"), ("lead_job_title", "b")], parent=parent)

        assert select.retire(parent, at_offset=400) == "drained"
        parent.refresh_from_db()
        child.refresh_from_db()
        assert parent.state == QueryNode.State.DRAINED
        assert child.state == QueryNode.State.DEAD

    def test_hitting_the_reach_cap_keeps_the_subtree_alive(self, db):
        # 10k is Elasticsearch's window, not the end of the population: adding a token
        # opens a fresh window over the part we could not reach.
        c = _campaign()
        parent = _node(c, [("lead_job_title", "a")], next_offset=REACH_CAP)
        child = _node(c, [("lead_job_title", "a"), ("lead_job_title", "b")], parent=parent)

        assert select.retire(parent, at_offset=REACH_CAP) == "capped"
        parent.refresh_from_db()
        child.refresh_from_db()
        assert parent.state == QueryNode.State.DRAINED
        assert child.state == QueryNode.State.FRONTIER

    def test_advance_moves_the_offset_and_keeps_the_node_fireable(self, db):
        c = _campaign()
        node = _node(c, [("lead_job_title", "a")])
        select.advance(node, leads_found=9027)
        node.refresh_from_db()
        assert node.next_offset == select.DISCOVERY_PAGE_SIZE
        assert node.state == QueryNode.State.FIRED
        assert node.leads_found == 9027


class TestSeedFrontier:
    def test_opens_one_depth_one_node_per_keyword(self, db):
        # No root: the empty query matches everyone and its 10k window is the provider's
        # famous-company head, so the level comes from the label store instead.
        c = _campaign()
        keywords = [("lead_job_title", "founder"), ("lead_seniority", "founder")]
        assert select.seed_frontier(c, keywords) == 2
        assert QueryNode.objects.filter(campaign=c, parent__isnull=True).count() == 2
        assert select.seed_frontier(c, keywords) == 0
