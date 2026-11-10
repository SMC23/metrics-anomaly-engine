import json

import pytest
import structlog

from engine.logging import configure_logging


def test_events_are_written_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")

    structlog.get_logger().info("batch_saved", size=3)

    record = json.loads(capsys.readouterr().out)
    assert record["event"] == "batch_saved"
    assert record["size"] == 3
    assert record["level"] == "info"
    assert record["timestamp"].endswith("Z")


def test_events_below_configured_level_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING")

    structlog.get_logger().info("ignored")

    assert capsys.readouterr().out == ""
