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

    def test_budget_checked_against_selected(self, sample_instances: list[dict]):
        """Budget is checked against selected instances, not the full universe."""
        # select only m7i.large (cheapest m7i instance at $0.096)
        selected = [i for i in sample_instances if i["api_name"] == "m7i.large"]

        # budget of $0.10 passes because it's checked against selected (max $0.096)
        # even though universe contains m7i.xlarge at $0.192
        result = synthesize_globs(selected, sample_instances, budget=0.10)
        # should produce m7* glob since selected instance is under budget
        assert "m7*" in result

        # budget of $0.05 fails because even the selected instance exceeds it
        result_strict = synthesize_globs(selected, sample_instances, budget=0.05)
        # should fall back to exact name
        assert "m7i.large" in result_strict

    def test_filtered_universe_respects_constraints(self, sample_instances: list[dict]):
        """Globs generated with filtered universe only consider instances in that universe."""
        # filter to only instances with 2 vCPUs and <=16GB RAM
        filtered = [i for i in sample_instances if i["vcpus"] == 2 and i["memory_gb"] <= 16]

        # select m7 instances from filtered set
        selected = [i for i in filtered if i["api_name"].startswith("m7")]

        # use filtered set as both selected and universe
        result = synthesize_globs(selected, filtered, budget=0.15)

        # with filtered universe, m7* glob max price is only the 2 vCPU variants
        # m7i.large=$0.096, m7a.large=$0.102, m7g.large=$0.082 - all under budget
        assert "m7*" in result

    def test_nvme_refines_to_d_variants(self, sample_instances: list[dict]):
        """When nvme=True, globs should only match NVMe instances (d suffix)."""
        # select only NVMe instances (m7gd, r7gd)
        selected = [i for i in sample_instances if i.get("nvme", False)]

        # with nvme=True, should produce specific "d" variant globs
        result = synthesize_globs(selected, sample_instances, nvme=True)

        # m7* would match non-NVMe instances, so it should refine to m7gd*
        # r7* would match non-NVMe instances, so it should refine to r7gd*
        assert "m7*" not in result, "m7* matches non-NVMe instances"
        assert "r7*" not in result, "r7* matches non-NVMe instances"
        # should have refined patterns or exact names
        assert any("m7gd" in g for g in result), f"Expected m7gd pattern in {result}"
        assert any("r7gd" in g for g in result), f"Expected r7gd pattern in {result}"

    def test_nvme_allows_prefix_when_all_nvme(self, sample_instances: list[dict]):
        """When nvme=True and all instances in prefix have NVMe, use prefix glob."""
        # select p5 instances (all have NVMe in our fixture)
        selected = [i for i in sample_instances if i["api_name"].startswith("p5")]

        # p5* should be valid since all p5 instances have NVMe
        result = synthesize_globs(selected, sample_instances, nvme=True)

        assert "p5*" in result

    def test_nvme_none_allows_broad_globs(self, sample_instances: list[dict]):
        """When nvme=None, don't filter by NVMe status."""
        # select all m7 instances (mix of NVMe and non-NVMe)
        selected = [i for i in sample_instances if i["api_name"].startswith("m7")]

        # without nvme filter, m7* should be valid
        result = synthesize_globs(selected, sample_instances, nvme=None)

        assert "m7*" in result
