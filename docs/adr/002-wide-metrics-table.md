# ADR-002: Wide `metrics` table (one row per host and timestamp)

## Status

Accepted.

## Context

Each sample has four values for the same host and timestamp: `cpu_pct`, `mem_pct`,
`latency_ms` and `rps` (`Metric`, `ARCHITECTURE.md` section 4.1). They are always produced and
sent together. The table must support:

- An idempotent insert: a redelivered stream message must not create duplicate rows.
- Reads of "one host, one time range, all four metrics" (dashboard, window rebuild at worker
  startup).
- Building the 24-feature vector of the Isolation Forest, which needs the four metrics aligned
  on the same timestamp.
- A per-minute continuous aggregate (`metrics_1m`).

There are two usual layouts:

- **Wide:** `(time, host, cpu_pct, mem_pct, latency_ms, rps)`, primary key `(host, time)`.
- **Long (narrow):** `(time, host, metric, value)`, primary key `(host, metric, time)`.

## Decision

Use the **wide** layout, as specified in `ARCHITECTURE.md` section 6.

## Reasons

1. **One message = one row = one idempotency key.** A stream entry carries one `Metric`. With
   the wide layout, `ON CONFLICT (host, time) DO NOTHING` deduplicates the whole message in one
   row. With the long layout, one message becomes four rows, and a partially applied batch is
   possible to reason about only per row, not per message.
2. **Fewer rows and index entries.** The long layout stores four rows per sample. In PostgreSQL
   every row has its own tuple header (about 24 bytes) and its own entry in the primary-key
   index, and the `host` and `time` values are repeated four times. The wide layout stores them
   once.
3. **No pivot for the model.** Isolation Forest features combine all four metrics at the same
   timestamps. With the long layout every read would need a `GROUP BY time` pivot or a
   four-way join.
4. **The schema matches the contract.** The table and `Metric` have the same fields, so the
   insert maps fields one-to-one and type checks (`double precision NOT NULL`) catch mistakes.
5. **Simple aggregate.** `metrics_1m` is a plain `avg`/`max` per column grouped by host and
   minute.

## Alternatives considered

| Option | Why not |
|---|---|
| Long format `(time, host, metric, value)` | Flexible (a new metric needs no migration) and handles sparse metrics well, but 4x rows, a pivot for every multi-metric read, and an idempotency unit smaller than the message. The flexibility is not needed: the metric set is fixed by the contract. |
| Wide table + `jsonb` for extra metrics | Keeps flexibility for future metrics, but values inside `jsonb` lose column types, cannot be indexed cheaply and complicate the aggregate. Can be added later if real `psutil` metrics need it. |
| One table per metric | Four inserts per batch and four-way joins for every read. |

## Consequences

- **Adding a metric is a migration:** `ALTER TABLE metrics ADD COLUMN ...`, plus recreating
  `metrics_1m` (a continuous aggregate's query cannot be altered), plus a change to `Metric`.
  Acceptable because changing the contract already requires an ADR.
- **No partial samples.** Every column is `NOT NULL`; a host that cannot report one of the four
  metrics cannot be stored. This matches the contract, where all four fields are required.
- The `(host, time)` primary key also serves "one host, time range" queries, so no extra
  index is needed for them.
- If TimescaleDB native compression is enabled later, the size difference between layouts
  shrinks (columnar compression handles repeated values well); reasons 1, 3 and 4 still hold.
