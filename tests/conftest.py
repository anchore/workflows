"""Shared pytest fixtures for runson tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# sample CSV content with representative instances covering various
# families, architectures, and sizes
SAMPLE_CSV_CONTENT = """\
API Name,vCPUs,Instance Memory,On Demand,Linux Spot Minimum cost,EBS Optimized: Baseline Bandwidth,Instance Storage
m7i.large,2 vCPUs,8 GiB,$0.096 hourly,$0.038 hourly,10000 Mbps,EBS only
m7i.xlarge,4 vCPUs,16 GiB,$0.192 hourly,$0.077 hourly,10000 Mbps,EBS only
m7i.2xlarge,8 vCPUs,32 GiB,$0.384 hourly,$0.154 hourly,10000 Mbps,EBS only
m7a.large,2 vCPUs,8 GiB,$0.102 hourly,$0.041 hourly,10000 Mbps,EBS only
m7a.xlarge,4 vCPUs,16 GiB,$0.204 hourly,$0.082 hourly,10000 Mbps,EBS only
m7g.large,2 vCPUs,8 GiB,$0.082 hourly,$0.033 hourly,10000 Mbps,EBS only
m7g.xlarge,4 vCPUs,16 GiB,$0.163 hourly,$0.065 hourly,10000 Mbps,EBS only
m7gd.large,2 vCPUs,8 GiB,$0.095 hourly,$0.038 hourly,10000 Mbps,118 GB NVMe SSD
m7gd.xlarge,4 vCPUs,16 GiB,$0.190 hourly,$0.076 hourly,10000 Mbps,237 GB NVMe SSD
c7i.large,2 vCPUs,4 GiB,$0.085 hourly,$0.034 hourly,10000 Mbps,EBS only
c7i.xlarge,4 vCPUs,8 GiB,$0.170 hourly,$0.068 hourly,10000 Mbps,EBS only
r7i.large,2 vCPUs,16 GiB,$0.126 hourly,$0.050 hourly,10000 Mbps,EBS only
r7i.xlarge,4 vCPUs,32 GiB,$0.252 hourly,$0.101 hourly,10000 Mbps,EBS only
r7a.large,2 vCPUs,16 GiB,$0.134 hourly,$0.054 hourly,10000 Mbps,EBS only
r7gd.large,2 vCPUs,16 GiB,$0.145 hourly,$0.058 hourly,10000 Mbps,118 GB NVMe SSD
t4g.small,2 vCPUs,2 GiB,$0.017 hourly,$0.005 hourly,,EBS only
a1.medium,1 vCPUs,2 GiB,$0.025 hourly,$0.008 hourly,,EBS only
p5.48xlarge,192 vCPUs,2048 GiB,$98.32 hourly,$29.50 hourly,80000 Mbps,8x3800 GB NVMe SSD
"""


@pytest.fixture
def sample_csv_content() -> str:
    """Return sample CSV content as a string."""
    return SAMPLE_CSV_CONTENT


@pytest.fixture
def sample_csv_path(tmp_path: Path, sample_csv_content: str) -> Path:
    """Create a temp CSV file with sample data and return its path."""
    csv_file = tmp_path / "amz-prices.csv"
    csv_file.write_text(sample_csv_content)
    return csv_file


@pytest.fixture
def mock_csv_path(sample_csv_path: Path):
    """Patch get_csv_path to return the sample CSV path."""
    with patch("runson.cli.config.get_csv_path", return_value=sample_csv_path):
        yield sample_csv_path


@pytest.fixture
def sample_instances(sample_csv_path: Path) -> list[dict]:
    """Load instances from sample CSV for direct use in tests."""
    from runson.cli.config import load_instances

    return load_instances(sample_csv_path)
