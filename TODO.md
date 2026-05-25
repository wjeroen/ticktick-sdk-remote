# Wallacast - Task List

> **Instructions for Claude Code:** This is a general task list for development. Mark tasks done by changing `[ ]` to `[x]`. Add new tasks as they come up. Keep it organized and actionable. If you notice a to-do has already been completed by the user or a previous Claude instance but it it hasn't been marked yet, ask the user whether you can mark it done.

## Current Sprint

> **Priority Key:** 1 = Highest priority (do first, saves money!), 2 = High priority, 3 = Medium priority, 4+ = Lower priority (do later)

### Features to Implement
- [ ] Test deployment on Railway and verify Claude.ai connector works
- [ ] Monitor for TickTick session expiry issues in production

## Completed Recently ✅

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
