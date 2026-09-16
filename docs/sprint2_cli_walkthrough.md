# S2.3 CLI walkthrough transcript

Generated `2026-09-16 21:46 UTC` on branch `feat/s2-3-redis-celery-dlq` via `just walkthrough`
(scripts/s2_3_cli_walkthrough.sh): docker postgres + redis, a locally started
celery worker scaled to retries=3 / base=0.5s / no jitter, everything else driven
through inspectable CLIs (psql, redis-cli, the replay tool).

```text
postgres + redis are up.
queues cleared for a clean run.

════════════════════════════════════════════════════════════════
  Starting the local celery worker (scaled config: retries=3, base=0.5s, jitter=off)
════════════════════════════════════════════════════════════════
worker answered inspect ping.

════════════════════════════════════════════════════════════════
  1/7 — healthy event: webhook payload → queue → worker → succeeded
════════════════════════════════════════════════════════════════
enqueued event evt-walkthrough-3e8496a30a64 (execution a4980d97-98d2-410a-9517-6b6db0cf2d4e)
  status   |          started_at           |           ended_at            | termination_cause 
-----------+-------------------------------+-------------------------------+-------------------
 succeeded | 2026-09-16 21:46:25.312509+00 | 2026-09-16 21:46:25.619176+00 | completed
(1 row)


════════════════════════════════════════════════════════════════
  2/7 — duplicate delivery: the same event_id twice → exactly one execution
════════════════════════════════════════════════════════════════
first  delivery: accepted  execution=f27e047b-8c9c-4940-b4a9-0b012ac26209
second delivery: duplicate execution=None
events rows for that event_id: 1 (unique constraint arbitrated — no check-then-insert race)

════════════════════════════════════════════════════════════════
  3/7 — transient failure: 3 attempts, exponential backoff (0.5s, 1.0s), then dead-letter
════════════════════════════════════════════════════════════════
enqueued evt-walkthrough-c7db2e834074 (execution 714f4737-ba7a-4a01-84e9-57ee1076bf6e)
measured gaps between failures.occurred_at (expect ≈ stub sleep 0.1s + delay):
 attempt |  failure_type  | retryable |          occurred_at          | gap_seconds |                   message                    
---------+----------------+-----------+-------------------------------+-------------+----------------------------------------------
       1 | RetryableError | t         | 2026-09-16 21:46:32.072859+00 |             | forced transient failure for INCFAIL70787138
       2 | RetryableError | t         | 2026-09-16 21:46:32.717252+00 |       0.644 | forced transient failure for INCFAIL70787138
       3 | RetryableError | t         | 2026-09-16 21:46:33.857593+00 |       1.140 | forced transient failure for INCFAIL70787138
(3 rows)

   state   | attempt_count | max_attempts | next_retry_at 
-----------+---------------+--------------+---------------
 exhausted |             3 |            3 | 
(1 row)

 status |           ended_at            |   termination_cause   
--------+-------------------------------+-----------------------
 failed | 2026-09-16 21:46:33.864984+00 | max retries exhausted
(1 row)

DLQ depth: 1

════════════════════════════════════════════════════════════════
  4/7 — poison event: terminal error → cancelled on attempt 1 (budget NOT forged)
════════════════════════════════════════════════════════════════
enqueued evt-walkthrough-54ca69975d4d (execution 175a3a94-8e2e-452b-8886-0b2ed3dda26e)
   state   | attempt_count | max_attempts 
-----------+---------------+--------------
 cancelled |             1 |            3
(1 row)

 attempt | failure_type  | retryable |                    message                     
---------+---------------+-----------+------------------------------------------------
       1 | TerminalError | f         | forced terminal failure for INCBROKEN166557674
(1 row)


════════════════════════════════════════════════════════════════
  5/7 — inspecting the DLQ (bulletin board for humans — no worker consumes it)
════════════════════════════════════════════════════════════════
2 dead-lettered record(s), newest first:

event_id                             retries  failed_at                   reason
evt-walkthrough-54ca69975d4d               1  2026-09-16T21:46:42.910263+00:00 forced terminal failure for INCBROKEN166557674
evt-walkthrough-c7db2e834074               3  2026-09-16T21:46:33.880396+00:00 forced transient failure for INCFAIL70787138

════════════════════════════════════════════════════════════════
  6/7 — replay the EXHAUSTED event: fresh budget, history preserved
════════════════════════════════════════════════════════════════
2026-09-17 00:46:50 [info     ] event_replayed                 event_id=evt-walkthrough-c7db2e834074 execution_id=714f4737-ba7a-4a01-84e9-57ee1076bf6e removed_records=1
replayed evt-walkthrough-c7db2e834074 (execution 714f4737-ba7a-4a01-84e9-57ee1076bf6e): state reset, 1 DLQ record(s) removed, event re-enqueued
failure history after replay (1st run rows kept, 2nd run appended):
 attempt |          occurred_at          |                   message                    
---------+-------------------------------+----------------------------------------------
       1 | 2026-09-16 21:46:32.072859+00 | forced transient failure for INCFAIL70787138
       2 | 2026-09-16 21:46:32.717252+00 | forced transient failure for INCFAIL70787138
       3 | 2026-09-16 21:46:33.857593+00 | forced transient failure for INCFAIL70787138
       1 | 2026-09-16 21:46:50.796364+00 | forced transient failure for INCFAIL70787138
       2 | 2026-09-16 21:46:51.455136+00 | forced transient failure for INCFAIL70787138
       3 | 2026-09-16 21:46:52.601291+00 | forced transient failure for INCFAIL70787138
(6 rows)

total failure rows: 6 (was 3 — the replay really did reset and re-run)
DLQ depth: 2

════════════════════════════════════════════════════════════════
  7/7 — replay the CANCELLED event: it runs again and, being poison, cancels honestly
════════════════════════════════════════════════════════════════
2026-09-17 00:47:00 [info     ] event_replayed                 event_id=evt-walkthrough-54ca69975d4d execution_id=175a3a94-8e2e-452b-8886-0b2ed3dda26e removed_records=1
replayed evt-walkthrough-54ca69975d4d (execution 175a3a94-8e2e-452b-8886-0b2ed3dda26e): state reset, 1 DLQ record(s) removed, event re-enqueued
retry_state after replaying a cancelled event:
   state   | attempt_count | max_attempts 
-----------+---------------+--------------
 cancelled |             1 |            3
(1 row)

failure rows: 2 (one per replay run)

════════════════════════════════════════════════════════════════
  Walkthrough complete
════════════════════════════════════════════════════════════════
worker log: /tmp/barq-walkthrough-worker.pDCZl2.log
inspect further with: just psql-executions / psql-failures / psql-retry-state / redis-dlq-peek / dlq-list
```
