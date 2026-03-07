# ARI-14: Runaway Verification Worker Loop Investigation

**Worker**: aristotlebot-slack-w78
**Date**: 2026-03-07
**Codebase investigated**: `/var/lib/openclaw/agents/klaw/repo/` (Klaw controller)
**Klaw PR**: https://github.com/eastseymour/Klaw/pull/67

## Executive Summary

Workers w43-w75 (30+ workers) repeatedly respawned for the same completed
verification task. The root cause is a **missing failure-retry guard** in
the Klaw controller's verification worker spawning logic. When a
verification worker failed, the issue's tracking entry was cleaned up
unconditionally, allowing the next periodic scan (every 10 minutes) to
spawn a brand-new verification worker for the same "In Review" issue.
This created an infinite loop.

## Root Cause Analysis

### The Verification Worker Lifecycle

```
_linear_task_check_loop (every 10 minutes)
  -> _check_in_review_issues()
       -> Query Linear for "In Review" issues
       -> For each issue NOT in running_issue_keys:
            -> Spawn a verification worker
            -> Add issue to _linear_issues_with_workers tracking set
```

### The Bug (Before Fix)

In `_worker_monitor_loop()`, when a worker completed, the code handled
success and failure cases, but unconditionally cleaned up the tracking set:

```python
# ORIGINAL CODE (conceptual, src/klaw/__main__.py)
if success:
    is_verification = worker.task.startswith("VERIFICATION TASK")
    for issue_key in issue_keys:
        target_status = "Done" if is_verification else "In Review"
        await self._linear_resolver.update_issue_status(issue_key, target_status)
# Always clean up tracking set so stale checker can pick up orphans
for issue_key in issue_keys:
    self._linear_issues_with_workers.discard(issue_key)
```

**Problem**: The tracking set was cleaned up unconditionally for ALL
completed workers (success or failure). There was no failure counter.

When a verification worker **failed**:

1. `success=False` → no Linear status update (issue stays "In Review")
2. Tracking set cleaned → `_linear_issues_with_workers.discard(key)`
3. Next scan (10 min later) → issue is "In Review" + not in tracking set
4. **New verification worker spawned** → REPEAT

This loop ran every 10 minutes, spawning w43, w44, w45, ..., w75.

### Three-Layer Deduplication (and Why It Failed)

Klaw has three dedup mechanisms, but none caught this:

| Layer | Mechanism | Why It Failed |
|-------|-----------|---------------|
| 1. In-memory tracking set | `_linear_issues_with_workers: set[str]` | Cleared on worker completion (both success AND failure) |
| 2. Running workers check | `_get_running_issue_keys()` scans RUNNING/CREATING workers | Failed worker is no longer in RUNNING state |
| 3. Linear status gate | Only spawns for "In Review" issues | Failed verification doesn't change status (stays "In Review") |

## The Fix

**Klaw PR**: https://github.com/eastseymour/Klaw/pull/67 (OPEN, not yet merged)

### Changes Made (in `src/klaw/__main__.py`)

**1. New failure counter** (initialization):
```python
self._failed_verification_attempts: dict[str, int] = {}
self.MAX_VERIFICATION_ATTEMPTS: int = 2
```

**2. Worker completion handler** (in `_worker_monitor_loop`):
- On **success**: Clear both tracking set AND failure counter
  ```python
  self._linear_issues_with_workers.discard(issue_key)
  self._failed_verification_attempts.pop(issue_key, None)
  ```
- On **verification failure**: Increment failure counter, discard from
  tracking set (to allow controlled retry), notify boss if max reached
  ```python
  attempts = self._failed_verification_attempts.get(issue_key, 0) + 1
  self._failed_verification_attempts[issue_key] = attempts
  self._linear_issues_with_workers.discard(issue_key)
  if attempts >= self.MAX_VERIFICATION_ATTEMPTS:
      # Give up — notify boss for manual intervention
  ```
- On **non-verification failure**: Clear tracking set (for stale checker)

**3. `_discard_issue_keys_for_worker()`** (new helper for idle_timeout,
terminated_unexpectedly events):
- Verification workers: increment failure counter + clear tracking set
- Non-verification workers: clear tracking set immediately

**4. `_check_in_review_issues()`** (the spawning function):
```python
attempts = self._failed_verification_attempts.get(issue.identifier, 0)
if attempts >= self.MAX_VERIFICATION_ATTEMPTS:
    logger.debug("Skipping %s: verification failed %d times (max %d)", ...)
    continue
```

### Critical Design Decision: Always Discard from Tracking Set

A previous version of the fix kept the issue in `_linear_issues_with_workers`
to block respawn. This was **incorrect** because it permanently blocked
the issue — the failure counter could never advance to MAX since no new
worker was ever spawned.

The correct approach: **always discard from the tracking set** and rely on
`_failed_verification_attempts` as the sole guard. This allows controlled
retries (up to MAX_VERIFICATION_ATTEMPTS) before giving up.

### Invariants Enforced

1. **Failure counter is monotonically non-decreasing** per issue (only
   incremented or reset on success, never decremented).
2. **Once attempts >= MAX, no more verification workers are spawned**
   for that issue until Klaw restarts (in-memory only).
3. **On success, the counter is reset** so future verification cycles
   start fresh.
4. **The boss is always notified** when verification attempts are exhausted.

## Architecture Notes

### Klaw Worker Status Progression

```
Linear: Todo -> In Progress -> In Review -> Done
                  ^worker       ^verification    ^verified
                   runs          worker runs      & merged
```

### Key Files (Klaw Controller)

| File | Purpose |
|------|---------|
| `src/klaw/__main__.py` | Main controller, worker lifecycle, Linear integration |
| `src/klaw/workers/orchestrator.py` | Worker creation, health checks, plan management |
| `src/klaw/workers/agent_gateway.py` | Claude Agent SDK gateway |
| `src/klaw/workers/types.py` | Worker, TaskPlan, TaskStep data types |
| `tests/test_runaway_verification_loop.py` | Tests for this fix (18 tests, in Klaw PR #67) |

### Periodic Loops

| Loop | Interval | Purpose |
|------|----------|---------|
| `_message_poll_loop` | 30s | Slack messages |
| `_worker_monitor_loop` | 60s | Worker health + completion |
| `_linear_task_check_loop` | 600s | New work + verification + stale recovery |
| `_budget_poll_loop` | configurable | Budget enforcement |
| `_log_sync_loop` | 300s | Log synchronization |
| `_backup_loop` | configurable | Database/config backups |

### Concurrency Limits

| Limit | Value | Purpose |
|-------|-------|---------|
| `MAX_WORKERS` | configurable (default 5) | Total concurrent workers |
| `MAX_AUTO_CREATE` | 2 | New work per Linear scan |
| `MAX_VERIFY` | 1 | Verification workers per scan |
| `MAX_RECOVER` | 2 | Stale issue recovery per scan |
| `MAX_VERIFICATION_ATTEMPTS` | 2 | Retries before giving up |
| `MAX_ACTION_CHAIN` | 5 | Max follow-up brain turns per message |

### Worker Event Types

| Event | Meaning | Tracking Action |
|-------|---------|-----------------|
| `completed` (success) | Worker finished successfully | Clear tracking set + failure counter |
| `completed` (failure, verification) | Verification worker failed | Increment failure counter, discard tracking |
| `completed` (failure, non-verification) | Regular worker failed | Discard from tracking set |
| `idle_timeout` | Worker timed out with no activity | Increment failure counter (if verification) |
| `terminated_unexpectedly` | Process/session died | Increment failure counter (if verification) |
| `max_turns_restart` | Worker hit max agentic turns | Auto-retry with new worker |
| `transient_failure_retry` | Transient error, auto-retrying | No tracking change |

## Task 1: Redeployment Verification

Redeployment of aristotlebot-slack was also completed as part of this task:

| Check | Status |
|-------|--------|
| `git pull origin main` | Commit `558cebd` confirmed at HEAD |
| `pip install -e .` | Successfully installed aristotlebot-slack 0.1.0 |
| `systemctl restart aristotlebot` | Restarted, PID 34717 |
| `systemctl status` | `active (running)` since 01:30:57 UTC |
| Health endpoint | `{"status":"ok"}`, `socket_mode_connected: true` |
| Journal logs | Zero errors/warnings/exceptions |
| Branch | Confirmed on `main` |

## Test Coverage

18 new tests added in `tests/test_runaway_verification_loop.py` (in Klaw PR #67):

- `TestCheckInReviewIssuesGating` (4 tests): Exhausted issues skipped,
  retry allowed below max, running workers skipped, fresh issues spawned
- `TestGetRunningIssueKeys` (4 tests): Tracking set, running workers,
  stopped workers, failed workers
- `TestDiscardIssueKeysForWorker` (4 tests): Verification increment,
  max reached, non-verification discard, missing worker noop
- `TestVerificationFailureLifecycle` (2 tests): Full two-failures-then-blocked
  lifecycle, success resets counter
- `TestInvariants` (4 tests): Positive max, non-negative counter, budget
  gate, Linear gate
