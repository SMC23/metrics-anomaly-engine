import os
import subprocess
import sys

import pytest

from engine.redis_keys import (
    ALERTS_STREAM,
    CONSUMER_GROUP,
    DLQ_STREAM,
    consumer_name,
    metrics_stream,
    partition_for_host,
)

# Golden values: if these change, hosts move to other partitions and their in-memory windows
# end up on the wrong worker. Update only together with a migration plan.
GOLDEN = {
    ("web-01", 2): 0,
    ("web-01", 16): 12,
    ("db-01", 16): 5,
    ("batch-07", 16): 5,
}


def test_key_names_match_architecture() -> None:
    assert metrics_stream(0) == "metrics:raw:0"
    assert metrics_stream(3) == "metrics:raw:3"
    assert DLQ_STREAM == "metrics:dlq"
    assert ALERTS_STREAM == "alerts:events"
    assert CONSUMER_GROUP == "detectors"


def test_negative_partition_is_rejected() -> None:
    with pytest.raises(ValueError):
        metrics_stream(-1)


@pytest.mark.parametrize(("host", "partitions"), list(GOLDEN))
def test_partition_matches_golden_value(host: str, partitions: int) -> None:
    assert partition_for_host(host, partitions) == GOLDEN[(host, partitions)]


@pytest.mark.parametrize("partitions", [1, 2, 3, 7, 16])
def test_partition_is_in_range(partitions: int) -> None:
    for i in range(500):
        assert 0 <= partition_for_host(f"host-{i}", partitions) < partitions


def test_single_partition_is_always_zero() -> None:
    assert {partition_for_host(f"host-{i}", 1) for i in range(100)} == {0}


def test_hosts_spread_over_all_partitions() -> None:
    counts = [0, 0, 0, 0]
    for i in range(1000):
        counts[partition_for_host(f"web-{i:03d}", 4)] += 1
    # Uniform would be 250 each; this only guards against a degenerate hash.
    assert min(counts) > 200


@pytest.mark.parametrize("partitions", [0, -1])
def test_invalid_partition_count_is_rejected(partitions: int) -> None:
    with pytest.raises(ValueError):
        partition_for_host("web-01", partitions)


def test_partition_is_stable_across_processes() -> None:
    hosts = [f"host-{i}" for i in range(50)]
    code = (
        "from engine.redis_keys import partition_for_host;"
        f"print([partition_for_host(h, 8) for h in {hosts!r}])"
    )
    outputs = set()
    for seed in ("1", "2", "random"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        )
        outputs.add(result.stdout)

    assert len(outputs) == 1
    assert outputs.pop().strip() == str([partition_for_host(h, 8) for h in hosts])


def test_consumer_name_format() -> None:
    assert consumer_name("worker-a", 123) == "worker-a-123"
    assert consumer_name().endswith(f"-{os.getpid()}")
