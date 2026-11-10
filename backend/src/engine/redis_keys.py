"""Redis key names and host partitioning (ARCHITECTURE.md section 5)."""

import hashlib
import os
import socket

METRICS_STREAM_PREFIX = "metrics:raw"
DLQ_STREAM = "metrics:dlq"
ALERTS_STREAM = "alerts:events"
ALERTS_STREAM_MAXLEN = 10_000
CONSUMER_GROUP = "detectors"


def metrics_stream(partition: int) -> str:
    """Name of the raw metrics stream for one partition, e.g. `metrics:raw:0`."""
    if partition < 0:
        raise ValueError(f"partition must be >= 0, got {partition}")
    return f"{METRICS_STREAM_PREFIX}:{partition}"


def partition_for_host(host: str, partitions: int) -> int:
    """Return the partition in `[0, partitions)` that owns `host`.

    Uses BLAKE2b instead of the built-in `hash()`, which is salted per process
    (PYTHONHASHSEED) and would send the same host to different partitions from
    different gateway processes. Changing this function moves hosts between
    partitions, so it is covered by golden-value tests.
    """
    if partitions < 1:
        raise ValueError(f"partitions must be >= 1, got {partitions}")
    digest = hashlib.blake2b(host.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % partitions


def consumer_name(hostname: str | None = None, pid: int | None = None) -> str:
    """Consumer name inside the group: `{hostname}-{pid}`."""
    return f"{hostname or socket.gethostname()}-{pid if pid is not None else os.getpid()}"
