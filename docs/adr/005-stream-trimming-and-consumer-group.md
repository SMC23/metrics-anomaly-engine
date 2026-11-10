# ADR-005: Stream trimming vs the PEL, and who creates the consumer group

## Status

Proposed. Implementation in phase 2 (gateway) and phase 4 (worker).

ADR-003 and ADR-004 are reserved by `PROMPTS.md` for phases 6 and 10.

## Context

`ARCHITECTURE.md` specifies two things that interact badly:

- The gateway appends with `XADD metrics:raw:{p} MAXLEN ~ STREAM_MAXLEN` (1,000,000).
- The worker acknowledges (`XACK`) only after the batch is in TimescaleDB, so read-but-unsaved
  messages stay in the consumer group's **pending entries list (PEL)**.

**Trimming ignores the consumer group.** `MAXLEN` removes the oldest entries by count, whether
or not any group has read or acknowledged them. When the worker is stopped, or slower than the
gateway, for long enough that the stream exceeds `STREAM_MAXLEN`, two kinds of entries are lost:

1. **Pending entries** (delivered, not acknowledged). Their IDs stay in the PEL but the data is
   gone. Reading the consumer's history (`XREADGROUP ... 0`) returns the ID with no fields, and
   `XAUTOCLAIM` returns them in its third array ("deleted IDs") and removes them from the PEL.
2. **Undelivered entries** (never read by the group). They disappear without any trace in the
   PEL.

Either case breaks principle 1 ("todo dato recibido se persiste"). Section 12 mentions it
("el stream se recorta con `MAXLEN ~`") but does not say that this is data loss or how it is
detected.

A second, related problem is **who creates the group and from which ID**. Phase 4 makes the
worker create it. If the gateway starts first and receives traffic, then:

- creating the group with `$` skips everything already in the stream;
- creating it with `0` delivers those entries, but if the stream was trimmed in between, the
  group's `entries-read` counter is unknown and `lag` cannot be used to count losses.

Observed on the local `redis:7.4-alpine` container, Redis 7.4.12 (scratch keys, `XTRIM MAXLEN 3` after 10
entries, 4 of them read and not acknowledged):

| Check | Result |
|---|---|
| `XINFO STREAM` | `entries-added 10`, `length 3` |
| `XINFO GROUPS`, group created with `0 MKSTREAM` before the first `XADD` | `entries-read 4`, `lag 3` |
| `entries-added − entries-read − lag` | `3`: exactly the undelivered entries that were trimmed |
| `XREADGROUP GROUP g c STREAMS key 0` | 4 IDs with empty field lists |
| `XAUTOCLAIM key g c2 0 0-0` | the 4 IDs in the deleted list; `XPENDING` count drops to 0 |
| Group created later with `0` on the trimmed stream | `entries-read` empty (unknown) |
| Group created with an explicit ID (`1-0`) | `entries-read` empty (unknown) |
| `XDEL` of an unread entry | `lag` empty (unknown) |
| Second `XGROUP CREATE` on an existing group | `BUSYGROUP` error |

## Decision

1. **Both the gateway and the worker create the group, idempotently, from ID `0`.**
   At startup, for every partition `p` in `[0, STREAM_PARTITIONS)` (gateway) or in
   `WORKER_PARTITIONS` (worker): `XGROUP CREATE metrics:raw:{p} detectors 0 MKSTREAM`.
   A `BUSYGROUP` reply counts as success. Because the gateway does this before accepting
   traffic, the group exists before the first entry, so:
   - no message can be added outside the group's view;
   - `entries-read` starts at a known value, so `lag` is defined and losses are countable.
   `$` and `XGROUP SETID` are never used on `metrics:raw:*`.
2. **The gateway is not ready until the groups exist.** If group creation fails (Redis down),
   `/readyz` fails and ingestion answers `503`, the same as any Redis failure.
3. **The gateway appends with `NOMKSTREAM`.** If the stream disappeared (for example Redis
   restarted without its AOF), `XADD` returns nil instead of silently recreating a stream with
   no group. The gateway then recreates the group (step 1) and retries once; if that fails,
   `503`.
4. **Keep `MAXLEN ~` as a memory safety valve, not as normal operation.** Trimming unprocessed
   entries is treated as data loss that must be visible:
   - The worker counts and logs the deleted IDs returned by `XAUTOCLAIM`, then drops them from
     the PEL (they cannot be persisted: the data no longer exists).
   - The worker exports, per partition, the group `lag` and the number of undelivered entries
     lost to trimming (`entries-added − entries-read − lag`, from `XINFO STREAM` and
     `XINFO GROUPS`).
   - `metrics:raw:*` entries are never removed with `XDEL`, which would make `lag` unknown.
5. **Size `STREAM_MAXLEN` in hours of outage it can absorb**, and document it:
   `hours = STREAM_MAXLEN / (messages per second per partition × 3600)`. At the demo rate of
   5 hosts at 1 message/s in one partition, 1,000,000 entries hold about 55 hours. The load
   scenario will be far shorter; the phase 10 load test reports the real rate.

## Proposed changes to `ARCHITECTURE.md` (only if this ADR is accepted)

- 3.3 Gateway: steps 1–3 above (group creation at startup, readiness, `NOMKSTREAM`).
- 3.4 Worker: group creation from `0`; handling of deleted IDs returned by `XAUTOCLAIM`.
- 10 Observability: add `stream_lag_messages{partition}` and
  `stream_lost_messages_total{partition,reason}` with `reason` in
  `trimmed_pending | trimmed_undelivered`. Note that the existing
  `worker_pending_messages{partition}` is the PEL size, which is not the same as lag.
- 12 Failure modes: "Consumer lento" states that trimming beyond `STREAM_MAXLEN` loses data and
  is counted by the metrics above.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep things as they are | Losses would happen silently, and a late group creation makes them impossible to count. |
| No `MAXLEN` at all, rely on Redis `maxmemory` with `noeviction` | Turns overload into backpressure (`XADD` fails, gateway `503`, agent buffers) instead of loss, which is attractive. But a full Redis also blocks `alerts:events` and the live stream, and the failure mode is harder to recover from on one node. Worth revisiting in phase 10 with measurements. |
| Gateway checks lag before every `XADD` and returns `503` above a threshold | Real backpressure without filling memory, but one extra round trip per request. Possible later refinement. |
| Trim only acknowledged entries | Newer Redis releases (8.2 and later, per their release notes) add trim options that respect consumer groups. Not available on the Redis 7 fixed by `CLAUDE.md`; to be verified before relying on it. |
| Only the worker creates the group | The original plan. Leaves the startup order and trims before the first worker start uncovered. |

## Consequences

- At-least-once delivery holds as long as the worker keeps up within the `STREAM_MAXLEN`
  window; beyond it, loss is bounded to what is trimmed and always counted.
- Creating the group from `0` in the gateway means a worker that starts for the first time
  processes everything already in the stream. With `MAXLEN` this is bounded, but the first start
  after a long outage can take a while; the backlog is visible through `lag`.
- Two services issue `XGROUP CREATE`; both must handle `BUSYGROUP`. Integration tests in phase 2
  and phase 4 cover: group exists before the first `XADD`, restart with an existing group, and
  the loss counters after a forced trim.
