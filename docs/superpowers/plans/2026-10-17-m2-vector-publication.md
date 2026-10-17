# Authorized Vector Publication Implementation Plan

> Execute inline with executing-plans/TDD in the existing M2 worktree. Architecture A
> and autonomous routine decisions are approved; no repeated approval menu.

**Goal:** Persist complete, version-approved vector results through the authoritative C++ queue.

**Architecture:** Extend processing_jobs with vectorize; keep parser and vector states
separate. Reuse file → queue → job locking and the same bounded queue. Only the
trusted internal worker routes can claim/read/publish. MySQL holds staged windows;
no partial batch or incomplete version is marked completed. Existing project-role
routes provide status/cancel/retry. No second permissions store or vector database.

**Tech Stack:** C++14, existing nlohmann JSON/crypto/MySQL 8, schema 5.

**Spec:** `docs/superpowers/specs/2026-10-17-m2-document-processing-design.md`,
requirements R-01/R-02/R-03, sections 9/10, T-07/T-08/T-09/T-18.

## Constraints and protocol

- Queue <=100 pending across both task types, <=1 running; parser claims/recovery
  must not consume/interrupt vector jobs. Vector recovery is separately scoped.
- Queue vector work after approved current-version parsing or subsequent approval.
  Policy updates invalidate existing vector attempts, including revoke/reapprove;
  old tokens cannot publish even after approval returns. Never inherit approval.
- Every source batch/publish rechecks current version, availability, deletion,
  exact-version approval, parse completion and active attempt in a transaction.
- `/internal/v1/processing/vector/claim`, `/vector/recover`,
  `/vector/{job_id}/chunks`, `/vector/{job_id}/result`; existing fail/status/cancel/
  retry handle task type. Cookie/origin forbidden; service bearer required.
- Chunk response: batch_start, chunk_count, embedding_revision, chunks[{chunk_id,text}].
  At most 8 authoritative chunks from next unpublished ordinal. Result request:
  `{batch_start: integer, result: <encode_chunks output>}`; <=2 MiB plus envelope.
- Fixed encoder revision; exact ID/order/text hash, contiguous Unicode ranges,
  384 finite unit floats, <=64 windows/chunk and <=128/batch; <=4096 windows/version.
  Strictly monotonic batch cursor; identical same-attempt replay is idempotent,
  conflicting/out-of-order results fail; staged windows stay non-ready until final batch.
- Rejected result/fault rolls back rows, cursor and states. Retry clears previous
  staging only when a fresh attempt starts. Source data/citations remain immutable.
- Migration seeds eligible existing parsed versions without fabricating completion;
  excess backlog is failed/queue_full, source/keyword states unchanged. Stop services
  and back up before DDL. Only isolated temporary DB is migrated in this task.
- Current ECS: one capped test unit at a time, -j1, 1200M/1600M memory, no swap,
  CPU 100%, Nice10, Tasks256, /dev/vda 5M/200 IOPS both directions, <=900s.
- This increment does not execute Python model jobs, rank vectors, expose Agent
  retrieval, or claim M2 complete. Those must consume this actual publication path.

## Task 1: queue, authorization, bounded staging and publication

Files: extend `processing_service.{h,cpp}`, `worker_routes.cpp`,
`processing_routes.cpp`, `file_service.cpp`; add `vector_result.{h,cpp}` and
`db/migrations/005_vector_results.sql`; update fixture reset/schema checks and
`test/integration/processing_jobs_test.cpp`; document API/README/evidence.

- [x] RED: add HTTP tests for missing vector claim/batch routes, read-before-approval
  refusal, exact batch publication/replay, revoke/reapprove stale tokens, partial
  rollback, retry/recovery/type separation; compile existing code and observe failures.
- [x] Add schema and lifecycle plumbing; preserve existing parse/keyword states.
- [x] Add result validator and bounded transactional batch persistence. Implement
  `ReadVectorBatch(id,token)` and `PublishVectorBatch(id,token,start,result)`.
- [x] Run full C++ suite plus isolated migration/replay, add queue/cancel/deletion/
  supersession/malformed-vector/reordered-output boundaries; inspect real assertions.
- [x] Read-only review, actual evidence, API/README update, normal commit/push.

Verified result: 135 C++ tests passed, 0 failed/skipped; isolated schema upgrade
and replay passed. See `docs/evidence/m2/vector-publication/README.md` for exact
scope, earlier fixture failures, limits and source identities. No production DB
migration, model API call, automatic vector worker or hybrid retrieval claim.

Representative expected behavior (fixture vectors test protocol, not model quality):

```cpp
auto claim = VectorCall(app, "POST", "/claim");
auto first = VectorCall(app, "GET", "/" + id + "/chunks", token);
CHECK(first["batch_start"] == 0 && first["chunks"].size() == 8);
// Publish first eight of nine: vector processing, parse/keyword still completed.
// Publish last: vector completed. Replay same batch: row count unchanged.
// Revoke then reapprove: old token rejected; fresh attempt starts from ordinal 0.
```
