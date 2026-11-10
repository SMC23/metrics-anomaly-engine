# ADR-001: Redis Streams as the message bus (vs Pub/Sub and Kafka)

## Status

Accepted.

## Context

The gateway receives metric batches over HTTP and must hand them to the worker, which persists
them and runs the detectors. The bus between the two has to provide:

1. **No loss when the worker crashes or restarts.** Principle 1 of `ARCHITECTURE.md`: every
   received sample is persisted.
2. **Redelivery of messages that were read but not processed** (at-least-once delivery).
3. **Ordering per host**, because detectors keep a sliding window per host.
4. **A way to measure backlog**, to observe a slow consumer.
5. Low operational cost: this runs on one laptop with Docker Compose and in GitHub Actions.

## Decision

Use **Redis 7 Streams** with one consumer group (`detectors`):

- The gateway appends with `XADD metrics:raw:{p} MAXLEN ~ N`.
- The worker reads with `XREADGROUP`, writes to TimescaleDB, and only then sends `XACK`.
- Messages read but not acknowledged stay in the group's **pending entries list (PEL)**.
  `XAUTOCLAIM` reassigns the ones that have been idle too long, so a dead consumer does not
  strand them.
- Partitioning by host (`metrics:raw:{p}`) gives per-host ordering and lets each worker own
  fixed partitions (detailed in ADR-004, phase 10).

## Alternatives considered

| Option | Durable | Redelivery | Backlog visible | Ops cost | Why not |
|---|---|---|---|---|---|
| **Redis Pub/Sub** | No: a message published with no subscriber connected is gone | No | No | Very low | Breaks requirement 1. A worker restart loses every message published while it was down. Forbidden by `CLAUDE.md` for this pipeline. |
| **Redis Lists** (`LPUSH` / `BRPOPLPUSH`) | Yes (with AOF) | Manual (processing list + custom reaper) | Length only | Low | Reimplements by hand what consumer groups already give: per-consumer pending lists, delivery counts, idle time. |
| **Kafka** (or Redpanda) | Yes, replicated log | Yes (offsets) | Yes (consumer lag) | High | Strongest guarantees and true horizontal scaling, but adds a broker (and, for Kafka, a controller quorum) to run, tune and explain. The project's throughput is far below what needs it. |
| **RabbitMQ** | Yes | Yes (ack/nack) | Queue depth | Medium | A good fit for work queues, but per-key ordering with several consumers needs extra setup (consistent-hash exchange), and it would be a second stateful service next to Redis, which the live dashboard already needs. |
| **Postgres as a queue** (`SKIP LOCKED`) | Yes | Yes | Yes | Low | Couples ingestion to the database we want to protect: when the DB is slow, ingestion becomes slow too, instead of the stream absorbing the burst. |

## Consequences

**Positive**

- At-least-once delivery with standard commands, no custom bookkeeping.
- Redis is also used by the gateway to read the live stream (`XREAD`) for the WebSocket, so the
  bus does not add a separate service.
- Backlog is observable (`XPENDING`, `XINFO GROUPS`).

**Negative and accepted**

- **Single node, memory-bound.** Redis keeps the stream in RAM. `MAXLEN ~` caps memory, but
  trims old entries even if they were never processed. That interaction with the PEL is the
  subject of ADR-005.
- **Durability depends on AOF** (`appendfsync everysec`): an abrupt Redis crash can lose about
  the last second of writes. Kafka with replication would not.
- **No replication or high availability.** Listed as a non-goal in `ARCHITECTURE.md` section 13.
- At-least-once means duplicates are possible; the worker's insert must be idempotent
  (`ON CONFLICT (host, time) DO NOTHING`, ADR-002).
- Scaling consumers is not "add more consumers to the group": per-host windows require that a
  host always goes to the same worker. See ADR-004.
