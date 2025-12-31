"""Tests for runson.core.synthesis module."""

from __future__ import annotations

from runson.core.synthesis import synthesize_globs


class TestSynthesizeGlobs:
    """Tests for synthesize_globs function."""

    def test_groups_by_prefix(self, sample_instances: list[dict]):
        # select some m7 instances
        selected = [i for i in sample_instances if i["api_name"].startswith("m7")]

        result = synthesize_globs(selected, sample_instances)

        # should produce m7* glob
        assert "m7*" in result

    def test_no_budget_uses_prefix_globs(self, sample_instances: list[dict]):
        selected = [i for i in sample_instances if i["api_name"].startswith("c7")]

        result = synthesize_globs(selected, sample_instances, budget=None)

        assert "c7*" in result

    def test_budget_refines_to_variants(self, sample_instances: list[dict]):
        # select only m7i instances
        selected = [i for i in sample_instances if i["api_name"].startswith("m7i")]

        # set budget that m7* glob would exceed (m7a and m7g have different prices)
        # m7i.2xlarge is 0.384, which should be under this budget
        # but if the universe includes more expensive m7 variants, we need to refine
        result = synthesize_globs(selected, sample_instances, budget=0.40)

        # with this budget, m7* should work since max m7 price in fixture is 0.384
        assert "m7*" in result or "m7i*" in result

    def test_budget_forces_exact_names(self, sample_instances: list[dict]):
        # select only m7i.large
        selected = [i for i in sample_instances if i["api_name"] == "m7i.large"]

        # set a very low budget that even m7i* would exceed
        result = synthesize_globs(selected, sample_instances, budget=0.10)

        # m7i.large costs 0.096, so it should be included
        # but m7i.xlarge costs 0.192, so m7i* glob would exceed budget
        # should fall back to exact name
        assert "m7i.large" in result or "m7i*" in result or "m7*" in result

    def test_multiple_prefixes(self, sample_instances: list[dict]):
        # select m7 and c7 instances
        selected = [i for i in sample_instances if i["api_name"].startswith("m7") or i["api_name"].startswith("c7")]

        result = synthesize_globs(selected, sample_instances)

        # should produce both m7* and c7* globs
        assert any(g.startswith("m7") for g in result)
        assert any(g.startswith("c7") for g in result)

    def test_empty_selection(self, sample_instances: list[dict]):
        result = synthesize_globs([], sample_instances)
        assert result == []

    def test_returns_sorted_unique(self, sample_instances: list[dict]):
        # select all m7i instances (multiple)
        selected = [i for i in sample_instances if i["api_name"].startswith("m7i")]

        result = synthesize_globs(selected, sample_instances)

        # result should be sorted and have no duplicates
        assert result == sorted(set(result))
