# Gitea Comment Command Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require the Gitea user who triggers any bot command to have effective `write`, `admin`, or `owner` permission on the target repository before the bot performs work.

**Architecture:** Put authorization at the central `SkillRouter.route()` dispatch boundary so every current and future event caller is protected. Use the existing fail-closed `GiteaClient.check_user_repo_access()` API wrapper, cache its Boolean result only for the lifetime of one `SkillRouter`, and reuse one router for all intents in a single event so multi-command comments make one permission request.

**Tech Stack:** Python 3.11+, FastAPI, async `httpx`, Gitea REST API, pytest, pytest-asyncio, pytest-mock

## Global Constraints

- Every bot-triggering skill requires repository write access; no skill is public by default.
- Accepted effective permissions are exactly `write`, `admin`, and `owner`.
- Missing repository identity, missing sender login, 404, timeout, and all Gitea API failures must deny execution.
- Authorization must happen before LLM calls and before any Gitea write operation.
- Cache authorization only inside one event processor/router lifetime; do not add a cross-event or time-based permission cache.
- Denial logs may contain sender and `owner/repo`, but must not contain comment bodies, access tokens, or API response bodies.
- Unauthorized users receive the fixed response `权限不足：此命令需要仓库写入权限。` and the requested skill is not instantiated or executed.

---

## File Structure

- Modify `app/skills/router.py`: own the command authorization policy, fail-closed permission lookup, per-event permission cache, denial response, and structured warning log.
- Modify `app/core/event_processor.py`: construct one `SkillRouter` per event processor and reuse it for every intent in that event.
- Modify `tests/test_router.py`: cover allowed, denied, malformed, failed, cached, and non-execution behavior at the authorization boundary.
- Modify `tests/test_gitea.py`: pin the accepted `owner` permission behavior alongside existing permission-level tests.
- Modify `tests/test_event_processor.py`: verify a processor reuses its router rather than constructing one per intent.
- Modify `README.md`: document who may invoke bot commands and the Gitea token permission prerequisite.

### Task 1: Enforce fail-closed authorization in the skill router

**Files:**
- Modify: `tests/test_router.py`
- Modify: `app/skills/router.py:10-119`

**Interfaces:**
- Consumes: `GiteaClient.check_user_repo_access(owner: str, repo: str, username: str) -> bool`
- Produces: `SkillRouter._has_write_access(payload: Dict[Any, Any]) -> bool`
- Produces: `SkillRouter.route(...) -> str`, returning the fixed denial message when authorization fails
- Produces: module constant `PERMISSION_DENIED_MESSAGE: str`

- [ ] **Step 1: Add failing tests for permitted and denied commands**

Add these imports and tests to `tests/test_router.py`:

```python
from app.skills.router import PERMISSION_DENIED_MESSAGE


def command_payload(username: str = "writer") -> dict:
    return {
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": username},
        "issue": {"number": 123},
    }


@pytest.mark.asyncio
async def test_route_allows_writer_and_executes_close(mocker):
    mock_git = mocker.Mock()
    mock_git.check_user_repo_access = mocker.AsyncMock(return_value=True)
    mock_git.close_issue = mocker.AsyncMock()
    mock_db = mocker.Mock()
    mock_db.query.return_value.first.return_value = None

    router = SkillRouter(db_session=mock_db, gitea_client=mock_git)
    result = await router.route("close", {}, None, command_payload())

    assert result == ""
    mock_git.check_user_repo_access.assert_awaited_once_with(
        "owner", "repo", "writer"
    )
    mock_git.close_issue.assert_awaited_once_with("owner", "repo", 123)


@pytest.mark.asyncio
async def test_route_denies_reader_before_close_executes(mocker):
    mock_git = mocker.Mock()
    mock_git.check_user_repo_access = mocker.AsyncMock(return_value=False)
    mock_git.close_issue = mocker.AsyncMock()
    mock_db = mocker.Mock()
    mock_db.query.return_value.first.return_value = None

    router = SkillRouter(db_session=mock_db, gitea_client=mock_git)
    result = await router.route("close", {}, None, command_payload("reader"))

    assert result == PERMISSION_DENIED_MESSAGE
    mock_git.close_issue.assert_not_awaited()
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest \
  tests/test_router.py::test_route_allows_writer_and_executes_close \
  tests/test_router.py::test_route_denies_reader_before_close_executes -v
```

Expected: FAIL because `PERMISSION_DENIED_MESSAGE` and router authorization do not exist.

- [ ] **Step 3: Add malformed-input, API-failure, all-skill, and cache tests**

Add the following parameterized tests to `tests/test_router.py`:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    ["help", "如何部署", "label bug", "review", "close", "open"],
)
async def test_every_skill_requires_write_access(mocker, intent):
    mock_llm = mocker.Mock()
    mock_llm.generate = mocker.AsyncMock()
    mock_llm.generate_with_tools = mocker.AsyncMock()
    mocker.patch(
        "app.skills.router.get_llm_client_from_config",
        return_value=mock_llm,
    )
    mock_git = mocker.Mock()
    mock_git.check_user_repo_access = mocker.AsyncMock(return_value=False)
    mock_git.get_repo_file_content = mocker.AsyncMock()
    mock_git.add_issue_label = mocker.AsyncMock()
    mock_git.get_pull_request = mocker.AsyncMock()
    mock_git.close_issue = mocker.AsyncMock()
    mock_git.open_issue = mocker.AsyncMock()
    mock_db = mocker.Mock()
    mock_db.query.return_value.first.return_value = None

    router = SkillRouter(db_session=mock_db, gitea_client=mock_git)
    result = await router.route(intent, {}, None, command_payload("reader"))

    assert result == PERMISSION_DENIED_MESSAGE
    mock_llm.generate.assert_not_awaited()
    mock_llm.generate_with_tools.assert_not_awaited()
    mock_git.get_repo_file_content.assert_not_awaited()
    mock_git.add_issue_label.assert_not_awaited()
    mock_git.get_pull_request.assert_not_awaited()
    mock_git.close_issue.assert_not_awaited()
    mock_git.open_issue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"sender": {"login": "writer"}},
        {"repository": {"full_name": "owner/repo"}},
        {
            "repository": {"full_name": "invalid-full-name"},
            "sender": {"login": "writer"},
        },
    ],
)
async def test_route_denies_when_identity_is_incomplete(mocker, payload):
    mock_git = mocker.Mock()
    mock_git.check_user_repo_access = mocker.AsyncMock(return_value=True)
    mock_db = mocker.Mock()
    mock_db.query.return_value.first.return_value = None

    router = SkillRouter(db_session=mock_db, gitea_client=mock_git)
    result = await router.route("help", {}, None, payload)

    assert result == PERMISSION_DENIED_MESSAGE
    mock_git.check_user_repo_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_denies_when_permission_lookup_raises(mocker):
    mock_git = mocker.Mock()
    mock_git.check_user_repo_access = mocker.AsyncMock(
        side_effect=RuntimeError("Gitea unavailable")
    )
    mock_db = mocker.Mock()
    mock_db.query.return_value.first.return_value = None

    router = SkillRouter(db_session=mock_db, gitea_client=mock_git)
    result = await router.route("help", {}, None, command_payload())

    assert result == PERMISSION_DENIED_MESSAGE


@pytest.mark.asyncio
async def test_permission_result_is_cached_within_one_router(mocker):
    mock_git = mocker.Mock()
    mock_git.check_user_repo_access = mocker.AsyncMock(return_value=True)
    mock_db = mocker.Mock()
    mock_db.query.return_value.first.return_value = None

    router = SkillRouter(db_session=mock_db, gitea_client=mock_git)
    first = await router.route("help", {}, None, command_payload())
    second = await router.route("help", {}, None, command_payload())

    assert "Hi" in first
    assert "Hi" in second
    mock_git.check_user_repo_access.assert_awaited_once_with(
        "owner", "repo", "writer"
    )
```

- [ ] **Step 4: Run the expanded authorization tests and verify they fail**

Run:

```bash
uv run pytest tests/test_router.py -k "write_access or incomplete or lookup or cached or writer or reader" -v
```

Expected: FAIL because all skills currently dispatch without authorization and no permission cache exists.

- [ ] **Step 5: Implement the centralized authorization boundary**

In `app/skills/router.py`, add the fixed response, initialize a per-router cache, add the helper below, and invoke it immediately after `skill_name = self.classify_intent(intent)` and before importing or constructing any skill:

```python
PERMISSION_DENIED_MESSAGE = "权限不足：此命令需要仓库写入权限。"


class SkillRouter:
    def __init__(self, db_session=None, gitea_client: GiteaClient = None):
        self.db_session = db_session
        self.llm = get_llm_client_from_config(db_session)
        self.gitea = gitea_client
        self.config = self._load_config()
        self._permission_cache: Dict[tuple[str, str, str], bool] = {}
        # Keep the existing intent_keywords mapping below this initialization.

    async def _has_write_access(self, payload: Dict[Any, Any]) -> bool:
        repository = payload.get("repository") or {}
        sender = payload.get("sender") or {}
        full_name = repository.get("full_name") or ""
        username = sender.get("login") or ""

        if not username or "/" not in full_name:
            logger.warning(
                "Denied bot command: incomplete repository or sender identity"
            )
            return False

        owner, repo = full_name.split("/", 1)
        if not owner or not repo or self.gitea is None:
            logger.warning(
                "Denied bot command: invalid repository identity for sender=%s",
                username,
            )
            return False

        cache_key = (owner, repo, username)
        if cache_key in self._permission_cache:
            return self._permission_cache[cache_key]

        try:
            allowed = await self.gitea.check_user_repo_access(
                owner, repo, username
            )
        except Exception:
            logger.warning(
                "Permission lookup failed for sender=%s repository=%s/%s",
                username, owner, repo,
                exc_info=True,
            )
            allowed = False

        self._permission_cache[cache_key] = allowed
        return allowed
```

Insert this guard in `route()`:

```python
        skill_name = self.classify_intent(intent)
        logger.info(f"Classified as skill: {skill_name}")

        if not await self._has_write_access(payload):
            repository = payload.get("repository") or {}
            sender = payload.get("sender") or {}
            logger.warning(
                "Denied skill=%s sender=%s repository=%s",
                skill_name,
                sender.get("login") or "unknown",
                repository.get("full_name") or "unknown",
            )
            return PERMISSION_DENIED_MESSAGE
```

Do not create a `PUBLIC_SKILLS` allowlist in this change. This makes authorization default-deny for every existing and future skill.

- [ ] **Step 6: Pin the complete accepted permission set in the Gitea client tests**

Add this test to `tests/test_gitea.py` under `TestGiteaClientPermission`:

```python
@pytest.mark.asyncio
async def test_check_user_repo_access_owner(self, mocker):
    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"permission": "owner"}
    mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

    client = GiteaClient(
        base_url="http://gitea.local",
        access_token="fake-token",
    )
    result = await client.check_user_repo_access(
        "owner", "repo", "repository-owner"
    )

    assert result is True
```

- [ ] **Step 7: Update existing successful route tests with authorized senders**

For every existing `tests/test_router.py` test that calls `router.route()`, configure:

```python
mock_git.check_user_repo_access = mocker.AsyncMock(return_value=True)
```

and pass this payload shape:

```python
payload = {
    "repository": {"full_name": "owner/repo"},
    "sender": {"login": "writer"},
    "issue": {"number": 123},
}
```

For `test_route_to_analyze`, preserve its existing title/body target and README mocks while adding the authorized sender. This explicitly distinguishes “routing works” tests from the new denial tests.

- [ ] **Step 8: Run all router and Gitea permission tests**

Run:

```bash
uv run pytest tests/test_router.py tests/test_gitea.py::TestGiteaClientPermission -v
```

Expected: all tests PASS; read permission and Gitea API errors deny access, while `write` and `admin` permit access.

- [ ] **Step 9: Commit the authorization boundary**

```bash
git add app/skills/router.py tests/test_router.py tests/test_gitea.py
git commit -m "fix: require repository write access for bot commands"
```

### Task 2: Reuse one router and permission decision per event

**Files:**
- Modify: `tests/test_event_processor.py`
- Modify: `app/core/event_processor.py:14-24,89-94,198-202,237-241,321-338`

**Interfaces:**
- Consumes: `SkillRouter.route(intent, target, comment, payload) -> str` from Task 1
- Produces: `EventProcessor.router: SkillRouter`, scoped to one processor/event handling lifetime
- Preserves: `EventProcessor._route_to_skill(..., db: Session) -> str` so existing callers do not change
- Produces: one user-facing denial section even when one comment contains multiple unauthorized intents

- [ ] **Step 1: Write a failing router-reuse test**

Add this test to `tests/test_event_processor.py`:

```python
@pytest.mark.asyncio
async def test_route_to_skill_reuses_processor_router(mocker):
    from app.core.event_processor import EventProcessor

    processor = EventProcessor.__new__(EventProcessor)
    processor.router = mocker.Mock()
    processor.router.route = mocker.AsyncMock(return_value="done")
    payload = {
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": "writer"},
    }
    target = {"number": 1}
    db = mocker.Mock()

    first = await processor._route_to_skill("help", target, None, payload, db)
    second = await processor._route_to_skill("close", target, None, payload, db)

    assert first == "done"
    assert second == "done"
    assert processor.router.route.await_count == 2
```

- [ ] **Step 2: Write a failing test that deduplicates repeated denial responses**

Add this test to `tests/test_event_processor.py`:

```python
@pytest.mark.asyncio
async def test_multiple_denied_intents_post_one_denial(mocker):
    from app.core.event_processor import EventProcessor
    from app.skills.router import PERMISSION_DENIED_MESSAGE

    processor = EventProcessor.__new__(EventProcessor)
    processor.bot_username = "bot"
    processor.router = mocker.Mock()
    processor.router.route = mocker.AsyncMock(
        return_value=PERMISSION_DENIED_MESSAGE
    )
    processor.client = mocker.Mock()
    processor.client.create_comment = mocker.AsyncMock(return_value={})
    payload = {
        "comment": {"body": "@bot help @bot close"},
        "issue": {"number": 7},
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": "reader"},
    }

    await processor._process_issue_comment(payload, mocker.Mock())

    processor.client.create_comment.assert_awaited_once_with(
        "owner", "repo", 7, PERMISSION_DENIED_MESSAGE
    )
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```bash
uv run pytest \
  tests/test_event_processor.py::test_route_to_skill_reuses_processor_router \
  tests/test_event_processor.py::test_multiple_denied_intents_post_one_denial -v
```

Expected: both tests FAIL: `_route_to_skill()` constructs a local router, and duplicate denial strings are currently joined with a Markdown separator.

- [ ] **Step 4: Construct and reuse the router**

In `EventProcessor.__init__()` in `app/core/event_processor.py`, add:

```python
        self.router = SkillRouter(db_session=db, gitea_client=self.client)
```

Replace `_route_to_skill()`'s local router construction with:

```python
            return await self.router.route(intent, target, comment, payload)
```

Keep the `db` parameter for compatibility with all three existing processing paths. Because `SkillRouter._permission_cache` belongs to this instance, two protected intents from one comment share one Gitea permission lookup, while a later polling event creates a new processor and revalidates permission.

- [ ] **Step 5: Deduplicate identical responses within each event**

In each response-collection loop in `_process_issue_comment()`, `_process_issue()`, and `_process_pull_request()`, replace:

```python
            if response and response.strip():
                responses.append(response)
```

with:

```python
            if response and response.strip() and response not in responses:
                responses.append(response)
```

This keeps one denial message for a multi-command comment without changing the response returned for a single denied command.

- [ ] **Step 6: Run event processor, poller, and router tests**

Run:

```bash
uv run pytest tests/test_event_processor.py tests/test_poller.py tests/test_router.py -v
```

Expected: all tests PASS. The poller still constructs one processor per notification thread, and the authorization result does not persist globally.

- [ ] **Step 7: Commit router reuse and response deduplication**

```bash
git add app/core/event_processor.py tests/test_event_processor.py
git commit -m "refactor: reuse authorization within each event"
```

### Task 3: Document the authorization contract and run the security regression gate

**Files:**
- Modify: `README.md:64-91,148-152`

**Interfaces:**
- Consumes: the write-access policy implemented by Tasks 1-2
- Produces: operator documentation for required bot token scope and command eligibility

- [ ] **Step 1: Add the command authorization documentation**

Under `## 📋 支持的命令` in `README.md`, insert:

```markdown
### 命令权限

只有对目标仓库拥有 `write`、`admin` 或 `owner` 权限的 Gitea 用户才能触发机器人命令。系统会在调用 LLM 或修改 Issue/PR 前，通过 Gitea 的仓库权限 API 实时核验评论作者；权限不足或核验失败时默认拒绝执行。

机器人使用的 Token 必须能够读取目标仓库及其协作者权限，否则所有命令都会被安全拒绝。建议仅授予机器人完成评论、Review、标签和 Issue 状态操作所需的最小权限。
```

- [ ] **Step 2: Run the complete test suite**

Run:

```bash
uv run pytest -v
```

Expected: all tests PASS with no warnings indicating an un-awaited permission coroutine.

- [ ] **Step 3: Verify the security-critical call ordering**

Run:

```bash
rg -n "_has_write_access|check_user_repo_access|skill_class\(|skill.execute\(" app/skills/router.py
```

Expected: `_has_write_access()` is called in `route()` before `skill_class(...)` and `skill.execute(...)`; there is no route branch that executes a skill first.

- [ ] **Step 4: Verify no alternate command dispatch bypass exists**

Run:

```bash
rg -n "\.route\(|SkillRouter\(" app tests -g '*.py'
```

Expected: production command dispatches enter `SkillRouter.route()`; direct skill execution appears only in isolated skill unit tests.

- [ ] **Step 5: Review the final diff for secret-safe logging and scope**

Run:

```bash
git diff --check
git diff -- app/skills/router.py app/core/event_processor.py tests/test_router.py tests/test_event_processor.py README.md
```

Expected: no whitespace errors; denial logs contain only skill name, sender login, and repository name; no tokens or comment bodies are added to logs.

- [ ] **Step 6: Commit documentation**

```bash
git add README.md
git commit -m "docs: document bot command permission requirements"
```

## Rollout and Acceptance Criteria

- Deploy first to a non-production Gitea instance with one owner, one write collaborator, and one read-only user.
- Verify the owner and writer can invoke `help`, question answering, `review`, `label`, `close`, and `open`.
- Verify the read-only user receives exactly `权限不足：此命令需要仓库写入权限。` and no LLM call, label change, state transition, or review submission occurs.
- Revoke the writer's permission and verify the next new event is denied, demonstrating that the cache does not survive across events.
- Temporarily make the permission endpoint return 404/500 and verify commands fail closed without exposing the upstream response body.
- Confirm a comment containing two commands causes only one permission lookup for the same sender and repository.
- Monitor warning logs for denied commands and permission lookup failures during the first deployment window; do not log comment contents or credentials.
