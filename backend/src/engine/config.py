"""Application settings loaded from environment variables."""

from typing import Annotated, Literal, Self

from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _split_csv(value: object) -> object:
    """Turn a comma-separated environment value into a list of stripped items."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    """Runtime configuration, documented in `.env.example`."""

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    redis_url: RedisDsn
    database_url: PostgresDsn
    stream_partitions: int = Field(default=1, ge=1)
    stream_maxlen: int = Field(default=1_000_000, ge=1)
    worker_partitions: Annotated[list[int], NoDecode] = Field(default=[0], min_length=1)
    window_size: int = Field(default=60, ge=2)
    iforest_eval_every: int = Field(default=10, ge=1)
    model_path: str = "models/iforest.joblib"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:8080"]
    log_level: LogLevel = "INFO"

    _split_lists = field_validator("worker_partitions", "cors_origins", mode="before")(_split_csv)

    @model_validator(mode="after")
    def _check_worker_partitions(self) -> Self:
        if len(set(self.worker_partitions)) != len(self.worker_partitions):
            raise ValueError("WORKER_PARTITIONS must not contain duplicates")
        for partition in self.worker_partitions:
            if not 0 <= partition < self.stream_partitions:
                raise ValueError(
                    f"WORKER_PARTITIONS value {partition} is outside "
                    f"[0, {self.stream_partitions - 1}]"
                )
        return self
