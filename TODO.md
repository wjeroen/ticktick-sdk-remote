# Wallacast - Task List

> **Instructions for Claude Code:** This is a general task list for development. Mark tasks done by changing `[ ]` to `[x]`. Add new tasks as they come up. Keep it organized and actionable. If you notice a to-do has already been completed by the user or a previous Claude instance but it it hasn't been marked yet, ask the user whether you can mark it done.

## Current Sprint

> **Priority Key:** 1 = Highest priority (do first, saves money!), 2 = High priority, 3 = Medium priority, 4+ = Lower priority (do later)

### High Priority — V2 login broken with `need_captcha` (1)
- [ ] **Root cause (2026-06-15):** TickTick's `POST /api/v2/user/signon` returns HTTP 500 `errorCode: need_captcha` — an anti-bot **captcha wall on the password login**. V1 (OAuth) still returns 200, so the *account* isn't banned; only the password-login path is blocked. It's worsened by a **crash→retry storm**: when V2 auth fails, `initialize()` raises → `lifespan` crashes → every incoming MCP request re-runs lifespan and re-attempts signon (logs show bursts every few seconds), which keeps the captcha flag hot. NOT 2FA (`requires_2fa: False`), NOT 6-month expiry (server is ~2 months old).
- [ ] **Fix 1 (safe, helps everyone): stop the signon storm.** Cache the V2 session so signon runs once, not per request; on failure, back off with a cooldown and fail gracefully instead of crash-looping. This is the corrected version of the originally-approved "B" (a blind relogin-on-401 would *add* signon attempts and make captcha worse).
- [ ] **Fix 2 (A): set a stable `TICKTICK_DEVICE_ID`** env var so every login looks like the same device instead of a fresh random one each redeploy.
- [ ] **Fix 3 (opt-in, most reliable): inject a pre-obtained V2 session token + cookies** via env var so the server skips `/user/signon` entirely → can't hit `need_captcha`. Keep password login as the default so other users aren't forced into the browser-token flow.
- [ ] **Graceful degradation:** run V1-only when V2 is captcha-blocked instead of crashing the whole server.
- [ ] **2FA handling (for other users):** detect TickTick two-step verification at signon and surface a clear message / support a TOTP or token path. (This user has no 2FA, but others might.)
- [ ] **Persistent session cache (C):** optionally persist the V2 session across restarts (e.g. Railway Volume) so signon happens ~twice a year instead of on every redeploy. Document the cross-user setup implications.

### Features to Implement
- [ ] Test deployment on Railway and verify Claude.ai connector works (⚠️ do NOT redeploy until the signon-storm fix lands — see High Priority above)

## Completed Recently ✅

- [x] Children in task views now show priority alongside title. List/search views build a `{id: {title, priority}}` map from the unfiltered task list and render each child as `{id, title, priority_label}` (JSON) or indented sub-bullets `[PRIORITY] title (\`id\`)` (markdown). The markdown list row drops the `| N children` suffix in favor of the visible inline list; hidden subtasks (different status than the filter) are summarized as `| N more subtasks hidden`. `ticktick_get_task` fetches each child in parallel via `asyncio.gather` so the detail view shows the same enriched rendering — N extra API calls but they run concurrently. Failed fetches fall back to bare IDs (2026-06-02)
- [x] Fix paginated list_tasks/search_tasks to use a deterministic order so paging is stable across calls (TickTick's list endpoints don't guarantee one). Active tasks now sort by `due_date` asc (None last) then `id`; completed/abandoned by `completed_time` desc then `id`; deleted by `id`. Without this, calling page 2 could return tasks that overlapped with — or skipped past — page 1 (2026-06-01)
- [x] Add `_pagination_hint` to paginated JSON responses (e.g. "More tasks available — call this tool again with offset=23 to fetch the next page (showing 23 of 73)"). Mirrors the markdown footer; helps LLMs not miss the bare `next_offset` field deep in a long response (2026-06-01)
- [x] In list/search JSON views, drop children whose title can't be resolved from the query's status pool (e.g. completed subtasks under an active-filtered parent) — they showed as `{"id": "...", "title": null}` clutter before. When some are dropped, the task includes `total_children`, `children_hidden`, and a `_children_hint`. Detail view (`get_task`) keeps every child ID (2026-06-01)
- [x] Fix `tasks[:limit]` slicing bug in `list_tasks` / `search_tasks` — with `limit=50, offset=60` the slice threw away the very tasks the offset was asking for and returned 0 results. Now `tasks[:offset+limit]` so the offset can always reach into the requested window (2026-06-01)
- [x] Enrich child task references in `list_tasks` JSON: `children` array contains `{id, title}` pairs instead of bare IDs. For active queries, titles are resolved from the full unfiltered `get_all_tasks()` result so subtasks without due dates still show their title even when the parent's filter excludes them. Zero extra API calls (2026-06-01)
- [x] Surface task progress percentage (0-100) in JSON (always), markdown detail view (`Progress: X%` when > 0), and markdown list row (` | X%` when 1-99) (2026-06-01)
- [x] Markdown list view now flags non-default task states: `[DONE]` for completed, `[ABANDONED]` for won't-do, `[REPEATS]` for recurring tasks (placed between `[PINNED]` and the title) (2026-05-18)
- [x] Add `is_pinned` (bool) to JSON output for both single-task detail and list views — was previously surfaced only in markdown (2026-05-18)
- [x] Detail-view markdown shows project as `Name (\`id\`)` instead of just the ID. `format_task_markdown` now accepts an optional `project_names` map; `ticktick_get_task`, single-task `ticktick_create_tasks`, and single-task `ticktick_pin_tasks` fetch via new `build_project_name_for_task` helper (1 extra API call per detail-view render, fails benignly) (2026-05-18)
- [x] Fix recurring-task series silently dying when a recurring task's due date is moved via `batch_update_tasks`. Cause: `Task.to_v2_dict(for_update=True)` was sending `repeatFlag` but omitting the anchor fields (`repeatFrom`, `repeatFirstDate`, `repeatTaskId`, `exDate`); since V2 `/batch/task` resets any field absent from the payload, the RRULE survived but the chain anchor was wiped — so TickTick couldn't compute the next occurrence. Fix preserves all four on round-trip. Regression test added in `test_batch_update_tasks.py` (2026-05-19)
- [x] Markdown list row recurrence flag now shows the cadence (`[DAILY]` / `[WEEKLY]` / `[MONTHLY]` / `[YEARLY]`) parsed from the RRULE's FREQ= component, instead of a flat `[REPEATS]`. Falls back to `[REPEATS]` for rules without a recognizable FREQ (2026-05-19)
- [x] Add budget-aware pagination to all list-returning tools (list_tasks, search_tasks, list_projects, list_folders, list_tags, list_columns, habits). Each tool accepts `offset` (default 0); responses include `next_offset` (JSON) or a markdown footer when more items remain. When everything fits on one page, pagination is invisible. Generic helpers `paginate_markdown` / `paginate_json` in formatting.py do exact size-checking (serialize after each item, back off if over) so we never overflow the ~25,000-char MCP budget (2026-05-19)
- [x] Cap task `content` to 500 chars in JSON list views; per-task `content_truncated: true` flag + top-level `_content_hint` pointing at `ticktick_get_task` for the full text. Detail view (`get_task`) keeps full content. Applied uniformly to list_tasks, search_tasks, get_project(include_tasks), create_tasks batch result, pin_tasks batch result — anywhere tasks appear in a multi-item context (2026-05-19)
- [x] Remove misleading "Use response_format='json' for more compact output" truncation hint — JSON list views are empirically ~75% bigger per task than markdown (every field explicit, both label and numeric, raw ISO timestamps). Replaced with a hint about due_before/due_after filters (2026-05-19)
- [x] Add conditional `| Project: <name>` suffix to list-view task rows when the rendered list spans more than one project (search, cross-project list_tasks, multi-task pin results) — single-project lists omit the badge to avoid noise. Server fetches `project_id → name` map via `get_all_projects()` only when needed (2026-05-18)
- [x] Detail-view markdown now lists each child ID under `**Children**:` (nested bullets), matching what JSON exposes via `child_ids` and the parent's `**Parent**: <id>` line. Replaces the previous count-only line (2026-05-18)
- [x] Show parent/child relationships in task views with consistent "parent" / "children" vocabulary: list view shows `| Child of: <parent_id>` suffix and `| N children` suffix; detail view shows `Parent` / `Children` count in key details; mis-labelled `### Subtasks` section (which was actually checklist items) renamed to `### Checklist`; child IDs no longer listed individually (count is enough) (2026-05-13)
- [x] Fix `from_date`/`to_date` being silently ignored on `ticktick_list_tasks` with `status="completed"` or `"abandoned"`: dates are now passed through to `client.get_completed_tasks` / `get_abandoned_tasks` and interpreted as full days in `TICKTICK_TIMEZONE` (2026-05-13)
- [x] Enrich single-task markdown detail view: show recurrence rule, all-day flag, and time zone (when it differs from user's TZ) (2026-05-13)
- [x] Add `has_due_date` filter to `ticktick_list_tasks` (true = only scheduled, false = only unscheduled) (2026-05-13)
- [x] Add `due_after` filter to `ticktick_list_tasks` (mirrors `due_before`; combine both for date-range queries) (2026-05-13)
- [x] Fix `batch_update_tasks` wiping `repeat_flag` / `is_all_day` / `time_zone` on sparse updates: now pre-fetches each task and merges the delta before sending to V2 `/batch/task` (2026-05-13)
- [x] Fix `Task.format_datetime` for V2: convert datetime to UTC before applying the hardcoded `+0000` suffix — prevents +N hour offset when input had a non-UTC tzinfo (2026-05-13)
- [x] Fix timezone: all dates now convert via TICKTICK_TIMEZONE env var (Europe/Brussels etc.) — all-day tasks no longer off by one day (2026-03-13)
- [x] Fix priority display: markdown list view now shows [HIGH]/[MEDIUM]/[LOW]/[NONE] labels instead of blank (2026-03-13)
- [x] Add pinned indicator: markdown list view shows [PINNED] prefix; detail view shows "Pinned: Yes" line (2026-03-13)
- [x] Add due_before filter to ticktick_list_tasks: filter active tasks due on or before a given date, e.g. "show tasks due in next 3 days" (2026-03-13)
- [x] Convert MCP server from stdio to streamable-http for remote deployment (2026-03-12)
- [x] Add bearer token authentication for remote server security (2026-03-12)
- [x] Add health check endpoint at /health (2026-03-12)
- [x] Add Railway deployment files (Procfile, Dockerfile) (2026-03-12)
- [x] Fix FastMCP Context injection bug by pinning mcp>=1.26.0 (2026-03-12)
