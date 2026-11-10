-- Raw samples: one row per host and timestamp, four metrics per row (ADR-002).
CREATE TABLE metrics (
    time        timestamptz      NOT NULL,
    host        text             NOT NULL,
    cpu_pct     double precision NOT NULL,
    mem_pct     double precision NOT NULL,
    latency_ms  double precision NOT NULL,
    rps         double precision NOT NULL,
    -- Makes inserts idempotent: a redelivered message hits ON CONFLICT (host, time) DO NOTHING.
    -- Also serves "one host, time range" queries, so no extra (host, time) index is needed.
    PRIMARY KEY (host, time)
);

SELECT create_hypertable('metrics', by_range('time', INTERVAL '1 day'));

-- Per-minute rollup. materialized_only = false (real-time aggregation): queries combine the
-- materialized buckets with an on-the-fly aggregate of raw rows newer than the last refresh,
-- so the most recent minutes are visible before the policy refreshes them.
CREATE MATERIALIZED VIEW metrics_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 minute', time) AS bucket,
    host,
    avg(cpu_pct)    AS cpu_pct_avg,
    max(cpu_pct)    AS cpu_pct_max,
    avg(mem_pct)    AS mem_pct_avg,
    max(mem_pct)    AS mem_pct_max,
    avg(latency_ms) AS latency_ms_avg,
    max(latency_ms) AS latency_ms_max,
    avg(rps)        AS rps_avg,
    max(rps)        AS rps_max
FROM metrics
GROUP BY bucket, host
WITH NO DATA;

-- Re-materialize the last 2 hours every minute, leaving the still-open minute to real-time
-- aggregation. start_offset must stay well below the raw retention (7 days): refreshing a
-- range whose raw rows were already dropped would erase those buckets from metrics_1m.
SELECT add_continuous_aggregate_policy(
    'metrics_1m',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute'
);

SELECT add_retention_policy('metrics', drop_after => INTERVAL '7 days');
SELECT add_retention_policy('metrics_1m', drop_after => INTERVAL '90 days');
