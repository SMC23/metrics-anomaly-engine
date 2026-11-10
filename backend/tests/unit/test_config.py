import pytest
from pydantic import ValidationError

from engine.config import Settings

REQUIRED_ENV = {
    "REDIS_URL": "redis://localhost:6379/0",
    "DATABASE_URL": "postgresql://user:password@localhost:5432/metrics",
}
OPTIONAL_ENV = [
    "STREAM_PARTITIONS",
    "STREAM_MAXLEN",
    "WORKER_PARTITIONS",
    "WINDOW_SIZE",
    "IFOREST_EVAL_EVERY",
    "MODEL_PATH",
    "CORS_ORIGINS",
    "LOG_LEVEL",
]


def load_settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OPTIONAL_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)


def test_defaults_match_architecture() -> None:
    settings = load_settings()

    assert str(settings.redis_url) == REQUIRED_ENV["REDIS_URL"]
    assert str(settings.database_url) == REQUIRED_ENV["DATABASE_URL"]
    assert settings.stream_partitions == 1
    assert settings.stream_maxlen == 1_000_000
    assert settings.worker_partitions == [0]
    assert settings.window_size == 60
    assert settings.log_level == "INFO"


@pytest.mark.parametrize("name", ["REDIS_URL", "DATABASE_URL"])
def test_required_variable_missing_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.delenv(name)

    with pytest.raises(ValidationError):
        load_settings()


def test_values_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STREAM_PARTITIONS", "4")
    monkeypatch.setenv("STREAM_MAXLEN", "500")
    monkeypatch.setenv("WORKER_PARTITIONS", "1, 3")
    monkeypatch.setenv("WINDOW_SIZE", "120")
    monkeypatch.setenv("IFOREST_EVAL_EVERY", "5")
    monkeypatch.setenv("MODEL_PATH", "/models/custom.joblib")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = load_settings()

    assert settings.stream_partitions == 4
    assert settings.stream_maxlen == 500
    assert settings.worker_partitions == [1, 3]
    assert settings.window_size == 120
    assert settings.iforest_eval_every == 5
    assert settings.model_path == "/models/custom.joblib"
    assert settings.cors_origins == ["http://a.test", "http://b.test"]
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("REDIS_URL", "http://localhost:6379"),
        ("DATABASE_URL", "mysql://localhost/metrics"),
        ("STREAM_PARTITIONS", "0"),
        ("STREAM_MAXLEN", "0"),
        ("WINDOW_SIZE", "1"),
        ("IFOREST_EVAL_EVERY", "0"),
        ("LOG_LEVEL", "VERBOSE"),
        ("WORKER_PARTITIONS", ""),
        ("WORKER_PARTITIONS", "a"),
        ("WORKER_PARTITIONS", "-1"),
    ],
)
def test_invalid_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        load_settings()


def test_worker_partition_at_upper_bound_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STREAM_PARTITIONS", "2")
    monkeypatch.setenv("WORKER_PARTITIONS", "2")

    with pytest.raises(ValidationError):
        load_settings()


def test_last_valid_worker_partition_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STREAM_PARTITIONS", "2")
    monkeypatch.setenv("WORKER_PARTITIONS", "1")

    assert load_settings().worker_partitions == [1]


def test_duplicate_worker_partitions_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STREAM_PARTITIONS", "2")
    monkeypatch.setenv("WORKER_PARTITIONS", "0,0")

    with pytest.raises(ValidationError):
        load_settings()
