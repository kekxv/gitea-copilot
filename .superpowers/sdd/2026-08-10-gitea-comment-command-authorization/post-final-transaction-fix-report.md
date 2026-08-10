# Post-final-review transaction recovery fix

## Scope and cause

The authorization callback supplied by `handle_notification()` writes a
`ProcessedEvent` and immediately calls `db.commit()`.  `EventProcessor.process()`
catches callback exceptions and returns `False`.  That means a failed commit
previously took the poller `continue` path without reaching the surrounding
exception handler's `db.rollback()`.  The SQLAlchemy session was consequently
left in a failed transaction state and the next mention could not be processed.

## TDD evidence

Before changing production code, I added
`test_tracking_commit_failure_rolls_back_before_later_authorized_event` in
`tests/test_poller.py`.  It uses a separate real in-memory SQLite engine and
session, authorizes two comment mentions through the real `EventProcessor`, and
uses a one-time SQLAlchemy `ProcessedEvent.before_insert` listener to raise a
real `IntegrityError` during the first tracking commit.  The test asserts that
the session remains active and that the second event is recorded.

RED command:

```text
uv run pytest -q tests/test_poller.py::test_tracking_commit_failure_rolls_back_before_later_authorized_event
```

RED result:

```text
FAILED tests/test_poller.py::test_tracking_commit_failure_rolls_back_before_later_authorized_event
assert False
 +  where False = <sqlalchemy.orm.session.Session ...>.is_active
1 failed, 1 warning in 0.16s
```

The log also recorded the expected causal failure: after the first callback
commit raised `IntegrityError`, the second mention hit `PendingRollbackError`.

## Implementation

`app/tasks/notification_poller.py` now makes the callback's transaction
boundary explicit:

```python
try:
    db.commit()
except Exception:
    db.rollback()
    raise
```

The exception is intentionally re-raised.  `EventProcessor.process()` retains
its established behavior of returning `False` for that event, while the session
has already been restored to a reusable state for later events.

## GREEN and verification evidence

Focused regression after the production edit:

```text
uv run pytest -q tests/test_poller.py::test_tracking_commit_failure_rolls_back_before_later_authorized_event
1 passed in 0.08s
```

Poller module:

```text
uv run pytest -q tests/test_poller.py
4 passed in 0.09s
```

Full suite:

```text
uv run pytest -q
141 passed in 5.51s
```

`git diff --check` also exited successfully.

## Security and ordering review

- The authorization callback is still invoked only after
  `SkillRouter.has_write_access()` returns true.  The existing unauthorized
  poller test remains green and verifies no target-thread reactions and no
  `ProcessedEvent` record for a denied command.
- The ordering for authorized events is unchanged: `ProcessedEvent` is added
  and committed before the `eyes` reaction is attempted.
- On tracking failure, the reaction code is not reached because the failure is
  re-raised after rollback; the failed event is not falsely marked processed.
- The later authorized event can proceed because rollback occurs at the exact
  commit boundary whose exception is converted into `False` upstream.

## Self-review and concerns

The change is deliberately small and does not alter `EventProcessor`'s public
failure contract.  Catching `Exception` matches the surrounding transaction
handling style and guarantees rollback for any commit failure; the exception is
not swallowed.  A failed database commit still makes that particular command
fail and it may be retried later, which is preferable to recording or reacting
to an event whose idempotency record was not persisted.  No remaining concerns
were found by the focused tests, existing authorization coverage, full suite,
or diff whitespace check.
