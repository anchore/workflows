"""Tests for runson.core.inference module."""

from __future__ import annotations

import pytest

from runson.core.inference import (
    get_family_prefix,
    get_instance_category,
    infer_arch,
)


class TestGetFamilyPrefix:
    """Tests for get_family_prefix function."""

    @pytest.mark.parametrize(
        "api_name,expected",
        [
            ("m7i.large", "m7i"),
            ("m7i.2xlarge", "m7i"),
            ("r7a.xlarge", "r7a"),
            ("c7g.medium", "c7g"),
            ("p5.48xlarge", "p5"),
            ("t4g.small", "t4g"),
            ("a1.medium", "a1"),
            # no size suffix
            ("m7i", "m7i"),
        ],
    )
    def test_prefix_extraction(self, api_name: str, expected: str):
        assert get_family_prefix(api_name) == expected


class TestInferArch:
    """Tests for infer_arch function."""

    @pytest.mark.parametrize(
        "api_name,expected",
        [
            # Intel instances (x86_64)
            ("m7i.large", "x86_64"),
            ("m6i.xlarge", "x86_64"),
            ("c7i.2xlarge", "x86_64"),
            ("r7i.large", "x86_64"),
            ("m5.large", "x86_64"),  # older gen, no suffix
            ("c5d.xlarge", "x86_64"),  # with 'd' suffix
            ("m6in.large", "x86_64"),  # with 'in' suffix
            # AMD instances (amd64)
            ("m7a.large", "amd64"),
            ("m6a.xlarge", "amd64"),
            ("c7a.2xlarge", "amd64"),
            ("r7a.large", "amd64"),
            ("m6ad.xlarge", "amd64"),  # with 'd' suffix
            # Graviton/ARM instances (arm64)
            ("m7g.large", "arm64"),
            ("m6g.xlarge", "arm64"),
            ("c7g.2xlarge", "arm64"),
            ("r7g.large", "arm64"),
            ("m7gd.xlarge", "arm64"),  # with 'd' suffix
            ("m7gn.large", "arm64"),  # with 'n' suffix
            ("t4g.small", "arm64"),  # t4g special case
            ("a1.medium", "arm64"),  # a1 special case
        ],
    )
    def test_arch_inference(self, api_name: str, expected: str):
        assert infer_arch(api_name) == expected

    def test_short_family(self):
        # families with < 2 chars should default to x86_64
        assert infer_arch("m") == "x86_64"
        assert infer_arch("a") == "x86_64"


class TestGetInstanceCategory:
    """Tests for get_instance_category function."""

    @pytest.mark.parametrize(
        "api_name,expected",
        [
            # compute optimized
            ("c7i.large", "compute"),
            ("c6g.xlarge", "compute"),
            # general purpose
            ("m7i.large", "general"),
            ("m6a.xlarge", "general"),
            # memory optimized
            ("r7i.large", "memory"),
            ("r6g.xlarge", "memory"),
            ("x2idn.large", "memory"),
            # burstable
            ("t3.medium", "burstable"),
            ("t4g.small", "burstable"),
            # storage optimized
            ("i3.xlarge", "storage"),
            ("d3.xlarge", "storage"),
            ("h1.large", "storage"),
            # GPU
            ("g5.xlarge", "gpu"),
            ("p5.48xlarge", "gpu"),
            # high frequency
            ("z1d.large", "high-freq"),
            # FPGA
            ("f1.xlarge", "fpga"),
            # video
            ("vt1.3xlarge", "video"),
            # special cases
            ("a1.medium", "general"),  # a1 is general purpose ARM
            ("inf1.xlarge", "ml-inference"),  # inferentia
        ],
    )
    def test_category_mapping(self, api_name: str, expected: str):
        assert get_instance_category(api_name) == expected

    def test_unknown_prefix(self):
        assert get_instance_category("q9.large") == "other"
