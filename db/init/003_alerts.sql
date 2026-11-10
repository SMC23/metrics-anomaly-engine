-- One row per alert episode; the alert manager updates it in place until it resolves.
-- CHECK constraints mirror the validation in engine/schemas.py (Alert).
CREATE TABLE alerts (
    alert_id     uuid             PRIMARY KEY,
    host         text             NOT NULL,
    metric       text             NOT NULL
        CHECK (metric IN ('cpu_pct', 'mem_pct', 'latency_ms', 'rps', 'multivariate')),
    detector     text             NOT NULL CHECK (detector IN ('rules', 'ewma', 'iforest')),
    severity     text             NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    state        text             NOT NULL CHECK (state IN ('firing', 'resolved')),
    message      text             NOT NULL,
    score        double precision NOT NULL,
    started_at   timestamptz      NOT NULL,
    updated_at   timestamptz      NOT NULL,
    resolved_at  timestamptz      NULL,
    CHECK ((state = 'resolved') = (resolved_at IS NOT NULL))
);

-- GET /v1/alerts?state=firing, newest first.
CREATE INDEX alerts_state_updated_at_idx ON alerts (state, updated_at DESC);
-- GET /v1/alerts?host=..., newest first.
CREATE INDEX alerts_host_started_at_idx ON alerts (host, started_at DESC);
