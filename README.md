# TickTick Remote MCP Server

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A remote [MCP](https://modelcontextprotocol.io/) (Model Context Protocol) server for [TickTick](https://ticktick.com), designed to run on [Railway](https://railway.app) so you can use it from **Claude.ai**, **Claude Mobile** (iOS/Android), and any MCP-compatible client, no local setup needed. Prefer not to host anything? It also runs **locally** over stdio for Claude Desktop and Claude Code: see [Run locally](#run-locally-instead-claude-desktop-stdio).

Forked from [dev-mirzabicer/ticktick-sdk](https://github.com/dev-mirzabicer/ticktick-sdk) (local-only, no commits since Jan 2026) and substantially extended. The short version: remote HTTP deployment with auth, filters and honest pagination on every task status, trash visibility, all-day dates that match the TickTick app even while you travel, updates that no longer wipe fields, and graceful V1 fallback with clear diagnostics when TickTick blocks V2 login. Full list: [What this fork adds](#what-this-fork-adds). Includes full support for [Dida365 (滴答清单)](https://dida365.com).

> **Developers:** for how the internals work — architecture, V1/V2 routing, data models, API quirks, response formatting/pagination, and using the Python SDK directly — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Table of Contents

- [Quick Start (Deploy to Railway)](#quick-start-deploy-to-railway)
- [Run locally (Claude Desktop, stdio)](#run-locally-instead-claude-desktop-stdio)
- [Features](#features)
- [What this fork adds](#what-this-fork-adds)
- [Available MCP Tools (44 Total)](#available-mcp-tools-44-total)
- [Example Conversations](#example-conversations)
- [Health Check & Monitoring](#health-check--monitoring)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Quick Start (Deploy to Railway)

### Step 1: Register Your App at TickTick

1. Go to the [TickTick Developer Portal](https://developer.ticktick.com/manage)
2. Click **"Create App"**
3. Fill in:
   - **App Name**: e.g., "My TickTick MCP"
   - **Redirect URI**: `http://127.0.0.1:8080/callback`
4. Save your **Client ID** and **Client Secret**

### Step 2: Get Your OAuth2 Access Token

You need a computer (just once) to run this:

```bash
pip install ticktick-sdk
TICKTICK_CLIENT_ID=your_client_id \
TICKTICK_CLIENT_SECRET=your_client_secret \
ticktick-sdk auth
```

This opens your browser, you log into TickTick and authorize the app, and it prints your access token. Copy it — you'll paste it into Railway.

> **No computer available?** You can use [Google Colab](https://colab.research.google.com/) (free, runs in browser) or [Replit](https://replit.com/) to run the auth command.

> **SSH/Headless?** Add `--manual` flag for a text-based flow.

### Step 3: Configure Environment Variables

These are all the variables you'll set in Railway's dashboard. Required ones must be set or the server won't start.

| Variable | Required | Description |
|----------|:--------:|-------------|
| `TICKTICK_CLIENT_ID` | Yes | OAuth2 client ID from developer portal (Step 1) |
| `TICKTICK_CLIENT_SECRET` | Yes | OAuth2 client secret (Step 1) |
| `TICKTICK_ACCESS_TOKEN` | Yes | OAuth2 access token (Step 2) |
| `TICKTICK_USERNAME` | Yes | Your TickTick email address |
| `TICKTICK_PASSWORD` | Yes | Your TickTick password |
| `TICKTICK_TIMEZONE` | **Recommended** | The timezone you are in (default: `UTC`). It sets "today", the times of timed tasks, and how dates you send without a timezone are read. See the note below. |
| `TICKTICK_HOST` | No | API host: `ticktick.com` (default) or `dida365.com` (Chinese version) |
| `TICKTICK_TIMEOUT` | No | Request timeout in seconds (default: `30`) |
| `TICKTICK_DEVICE_ID` | **Strongly recommended** | Stable device id for V2 API (24-char hex). If unset, a fresh random id is generated every redeploy — see note below. |
| `TICKTICK_V2_COOKIES` | Recommended | Full Cookie header string from a logged-in TickTick browser tab. **Tried first** (before password sign-on) because it makes no login call and so can't trip TickTick's anti-bot. Strongly recommended on a server; password sign-on from a datacenter IP is unreliable. The session token (`t` cookie) is extracted from it automatically. See "If V2 sign-on gets captcha-walled" below. |
| `TICKTICK_V2_TOKEN` | No | Optional override for the session token — normally unnecessary, it's auto-extracted from the `t` cookie in `TICKTICK_V2_COOKIES`. |
| `TICKTICK_V2_IMPERSONATE` | No | Browser profile used for the V2 transport to get past TickTick's anti-bot (which 429s plain Python clients). Default `chrome`. Set to `off` to use plain httpx. Requires the `curl_cffi` dependency (included). |
| `MCP_BEARER_TOKEN` | No | Bearer token for server authentication — see note below |
| `MCP_SECRET_PATH` | **Strongly recommended** | Secret first path segment required on every request except `/health`. With it set, the MCP endpoint becomes `/<secret>/mcp` and the bare `/mcp` returns 404. This is the practical way to protect a public deployment, because Claude.ai stores the full URL but usually cannot send an auth header — see note below |
| `PORT` | No | Server port (default: `8000`, Railway sets this automatically) |

> **`TICKTICK_DEVICE_ID`:** TickTick tracks the devices logging into your account. Without this env var, every Railway redeploy invents a new random device id, so each redeploy looks like *"a stranger on a new device just logged in with your password"* — which can trigger TickTick's anti-bot CAPTCHA wall (`need_captcha`) and break V2 sign-on. Pick any stable 24-character hex string (e.g. the value printed in your first deploy's logs as `TICKTICK_DEVICE_ID is not set... auto-generated: <value>`) and paste it into Railway.

> **Timezone:** Set `TICKTICK_TIMEZONE` to your [IANA timezone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones), the "TZ identifier" column on that page. Common examples: `Europe/Brussels`, `Europe/London`, `America/New_York`, `America/Chicago`, `America/Los_Angeles`, `Asia/Tokyo`, `Asia/Shanghai`, `Australia/Sydney`. When you travel, change it to where you are, like your phone does.
>
> **All-day tasks are plain dates.** TickTick saves every date as an exact moment plus the task's own timezone, and the app shows an all-day task on its date in that zone, wherever your phone is. This server does the same: all-day tasks show as `2026-09-11` with no time, and they stay on the same day when you change `TICKTICK_TIMEZONE`. To set one, send a plain date like `2026-09-11`. A time without a timezone, like `2026-09-11T17:00:00`, means 17:00 in `TICKTICK_TIMEZONE`.

> ⚠️ **This server is single-user, so anyone who can reach `/mcp` acts as the account owner**, with full read, write, and delete access to their TickTick data. If you deploy it on a public URL, set `MCP_SECRET_PATH` (below). With neither `MCP_SECRET_PATH` nor `MCP_BEARER_TOKEN` set, the server is completely open and logs a warning saying so at startup. Full reasoning and alternatives: [`docs/SECURING_THE_SERVER.md`](docs/SECURING_THE_SERVER.md).

> **Note on MCP_BEARER_TOKEN**: if set, requests without the correct `Authorization: Bearer <token>` header are rejected (`/health` stays exempt). Claude.ai can only send such a header through its **Request headers** feature, which is in beta and rolled out gradually. Verified 2026-07-18: an account without that beta sees only Name, Remote MCP server URL, and OAuth Client ID/Secret in the "Add custom connector" dialog, with no way to add a header. So on Claude.ai this variable is usually unusable, which is exactly why `MCP_SECRET_PATH` exists. If your MCP client *can* send headers (Claude Desktop, Claude Code, Cursor), prefer the bearer token: it keeps the secret out of the URL. The two can be combined.

> **Note on MCP_SECRET_PATH**: generate one with `python -c "import secrets; print(secrets.token_urlsafe(24))"`. It must be a single path segment with no `/` inside it, and the server warns if it is shorter than 16 characters. Changing it invalidates the old URL, so you must update the connector afterwards. Honest limitation: a secret in a URL is weaker than a header, because URLs get recorded in proxy and server logs. It is a large improvement over an open endpoint, not a perfect one.

### Step 4: Deploy to Railway

1. **Fork this repo** on GitHub
2. **Create a Railway account** at [railway.app](https://railway.app)
3. **Create a new project** → **Deploy from GitHub repo** → select your fork
4. **Add environment variables** in Railway's dashboard (from Step 3 above)
5. **Generate a public domain** in Settings → Networking → Public Networking → "Generate Domain"
6. **Set the healthcheck path** in Settings → Deploy → Healthcheck Path → `/health`
7. **Wait for deployment** to finish (green status)
8. **Note your URL** — something like `https://your-app-production.up.railway.app`

### Step 5: Connect to Claude

#### Claude.ai (Web)

1. Go to **claude.ai** → **Customize** → **Connectors** (older accounts: **Settings** → **Connectors**)
2. Click **"Add custom connector"**
3. Enter a name (e.g., "TickTick")
4. Enter the URL, and don't forget the `/mcp` at the end:
   - with `MCP_SECRET_PATH` set (recommended): `https://your-app-production.up.railway.app/<your-secret>/mcp`
   - without it: `https://your-app-production.up.railway.app/mcp`
5. Click **"Add"**

> **On the OAuth Client ID / Client Secret fields:** this server has no OAuth endpoints of its own, so it never reads them. `TICKTICK_CLIENT_ID` / `TICKTICK_CLIENT_SECRET` authenticate *this server to TickTick*, which is the opposite direction, and pasting them into the connector achieves nothing. You can leave both fields blank.

> **Changing `MCP_SECRET_PATH` later** changes the URL, and a connector's URL generally cannot be edited in place, so you will need to remove the connector and add it again.

### Run locally instead (Claude Desktop, stdio)

You can also run this server **on your own machine** over stdio, which Claude
Desktop launches directly. This is useful when TickTick's V2 anti-bot is
throttling your datacenter/Railway IP: requests from a residential IP are not
throttled. (See "Debugging V2 auth" in `docs/ARCHITECTURE.md` §4.)

1. Install [uv](https://docs.astral.sh/uv/) (handles Python + deps).
2. Get this repo's code on your machine (clone, or download the ZIP and extract).
3. Create a `.env` file in the repo folder with your `TICKTICK_*` variables (the
   same ones from the env-var table above, including `TICKTICK_V2_COOKIES`).
4. In Claude Desktop: **Settings → Developer → Edit Config**, and add:

   ```json
   {
     "mcpServers": {
       "ticktick-local": {
         "command": "uv",
         "args": ["run", "--directory", "C:\\full\\path\\to\\this\\repo", "ticktick-sdk", "stdio"]
       }
     }
   }
   ```

   Use the full path to `uv` if Claude Desktop can't find it on PATH, and double
   backslashes on Windows. Then fully quit and reopen Claude Desktop.

5. For **Claude Code** instead of Claude Desktop, one command does the same:

   ```bash
   claude mcp add ticktick-local -- uv run --directory /full/path/to/this/repo ticktick-sdk stdio
   ```

The `ticktick-sdk stdio` subcommand (or `python -m ticktick_sdk` still serves
HTTP for Railway) runs the same 44 tools over stdio; logs go to stderr so stdout
stays clean for the protocol.

---

## Features

- **44 MCP Tools**: Tasks, projects, folders, kanban columns, tags, habits, focus, user analytics, auth diagnostics
- **Batch Operations**: All mutations accept lists (1-100 items) for bulk operations
- **Remote Access**: Runs as an HTTP server with streamable-http transport
- **Health Check**: `/health` endpoint for deployment platform monitoring
- **Dual Output**: Markdown for humans, JSON for machines
- **Dida365 Support**: Works with both ticktick.com and dida365.com

---

## What this fork adds

Summarized changes since [dev-mirzabicer/ticktick-sdk](https://github.com/dev-mirzabicer/ticktick-sdk). Most items are explained in more detail in the sections below.

**Deployment & hosting**
- [x] Remote HTTP server (streamable-http) for Railway deployment. Upstream was local-stdio-only; local stdio still works here too ([Run locally](#run-locally-instead-claude-desktop-stdio))
- [x] Bearer token authentication for the HTTP transport
- [x] `MCP_SECRET_PATH` secret-URL middleware to protect a public deployment (Claude.ai usually cannot send auth headers), plus per-tool-call logging
- [x] `/health` endpoint for platform monitoring
- [x] Railway deployment files (Procfile, Dockerfile)

**Auth resilience**
- [x] Graceful V2 degradation — server keeps V1 working (degraded mode) instead of crash-looping when V2 sign-on fails (e.g. `need_captcha`); V2-only tools return a friendly "V2 unavailable" error
- [x] Pre-obtained V2 session token fallback (`TICKTICK_V2_TOKEN` + `TICKTICK_V2_COOKIES`) — automatically used when password sign-on fails; bypasses `/user/signon` entirely
- [x] Startup warnings when `TICKTICK_DEVICE_ID` is unset **or not a valid 24-char hex** (a malformed device id can break V2 sign-on)
- [x] V1 OAuth 401 → specific log + error message pointing at `ticktick-sdk auth` token refresh, instead of generic "Authentication failed"
- [x] Auth-failure errors are self-explanatory to the MCP consumer (name the exact env var to refresh) so a model/person can fix it without repo or log access
- [x] `ticktick_auth_status` tool — live V1/V2 health check with a plain-English verdict and the exact fix, exposing **no** secret values

**Task filtering** (all on `ticktick_list_tasks`)
- [x] `due_before` filter — active tasks due on or before a date
- [x] `due_after` filter — active tasks due on or after a date (combine with `due_before` for ranges)
- [x] `has_due_date` filter — find scheduled or unscheduled tasks
- [x] `kind` filter — accepts one kind or a list, e.g. `kind=["TEXT","CHECKLIST"]` to drop notes from a listing. Applies to every status
- [x] `from_date`/`to_date` now honored for completed/abandoned status (previously silently ignored)
- [x] `project_id`, `tag`, and `priority` filters now apply to **every** status, completed/abandoned/deleted included (previously silently ignored outside `status="active"`, so a per-project completed query returned all projects' tasks). When any of these filters is active, the tool over-fetches from TickTick (at least 500, capped at 1000) so matches aren't crowded out by other projects' tasks, and the fetch window always covers the requested page (`limit + offset`), fixing empty second pages on completed/abandoned/deleted listings. One extra task is always probed past the window so a saturated window keeps `next_offset` non-null and paging converges on the true end (found live: a full window used to report itself as the complete result). Contract enforced by a filter x status test matrix (`tests/test_list_filter_contract.py`)

**Task content & description**
- [x] Checklist `description` is now readable and writable end to end: `ticktick_get_task` shows it (JSON `description` field, markdown "Description" section), list views include it capped like `content`, and `ticktick_update_tasks` accepts a `description` field. Previously it was create-only and no view ever displayed it, so anything written there was invisible through the tools
- [x] `content` and `description` accept up to 60,000 chars. This cap is this server's own validation rule, chosen by the operator; TickTick's own limit is 164,130 chars, the same for task content, note content, and checklist descriptions (operator-tested in the app, 2026-08-19; API probes confirmed storage far beyond the old 10k/5k caps with no truncation). `description` is a checklist feature: the apps show it only on checklist-kind tasks, while on other kinds the API stores the field but nothing displays it

**Pagination & response sizing**
- [x] Budget-aware pagination across **all** list-returning tools (`list_tasks`, `search_tasks`, `list_projects`, `list_folders`, `list_tags`, `list_columns`, `habits`) — pass `offset`, response surfaces `next_offset`
- [x] `total` always reports the **true match count**, independent of `limit`, and `next_offset` is non-null whenever more results remain. (Previously a small `limit` made `search_tasks`/`list_tasks` pre-slice the list, so `total` echoed the page size and `next_offset` went null — a false "this is everything." `limit` is now the page size, enforced inside the paginator, not a cap on the count.)
- [x] Per-task `content` and `description` capped at 1000 chars in JSON list views (with `content_truncated` / `description_truncated` flags + `_content_hint` pointing at `ticktick_get_task` for the full text)
- [x] Exact size-checking — no more zero-task truncated responses (and a single over-budget item is still emitted one-per-page so paging can't stall)
- [x] **Compact JSON** output (no pretty-print whitespace) and a **40,000-char** budget (was 25k), so far more fits per response. The char budget is well under the strictest documented client limit (Claude Code's 25k-**token** cap); see `docs/ARCHITECTURE.md` §9
- [x] **`list_tasks`/`search_tasks` JSON omits default-valued fields** to save space (absent = default; the convention is spelled out in each tool's description). `ticktick_get_task` stays full-fidelity as the escape hatch. Always present: `id`, `project_id`, `title`, `priority` (+label), `status` (+label), `time_zone`
- [x] **Trash visibility (`in_trash`)** — a trashed task keeps status "Active" in TickTick (trash is a separate flag), which used to make it indistinguishable from a live task. `ticktick_get_task` JSON now returns `in_trash: true` for a binned task (confirmed live 2026-07-20); `ticktick_update_tasks` JSON adds `in_trash: true` to any updated task that was in the trash at edit time (the pre-edit state, computed for free from the update's own pre-fetch); and `list_tasks(status="deleted")` flags binned rows. Markdown shows an "In trash" line / a `[TRASH]` row flag. The field is **only present when a task is trashed** (blank/absent otherwise, everywhere, no `in_trash: false` clutter). Trashed tasks still never appear under `status="active"` or in `search_tasks`; `status="deleted"` is the only way to list them, and updates on trashed tasks still succeed and leave the task in the trash rather than restoring it (tested 2026-07-20: a trashed task, updated, stayed binned)

**Task search** (`ticktick_search_tasks`)
- [x] Newest-first by default (`sort=created_desc`) plus a `sort` param (`created_*`, `modified_*`, `due_*`, `priority_desc`, `title_asc`) — previously results were oldest-first, which truncated the newest matches away under a limit
- [x] Structured filters: `project_id`, `kind` (TEXT/NOTE/CHECKLIST, one or a list like `["TEXT","CHECKLIST"]`), `tag`, `priority`, and `due_before`/`due_after`/`created_before`/`created_after`
- [x] `query` is now optional — omit it for a pure filter lookup (e.g. "latest NOTE in project X" via `project_id` + `kind=NOTE` + `limit=1`)
- [x] Optional `sort` on `ticktick_list_tasks` too (defaults to the existing per-status order)
- [x] `limit` accepts up to 500, matching `list_tasks` (was 100, which rejected a uniform `limit=200`)

**Task list & detail rendering**
- [x] `[HIGH]` / `[MEDIUM]` / `[LOW]` / `[NONE]` priority labels visible in markdown list rows
- [x] `[PINNED]` / `[DONE]` / `[ABANDONED]` status flags in list rows, plus the full recurrence rule (`[FREQ=DAILY;INTERVAL=8]`) so cadences that differ never look alike
- [x] Parent/children relationships shown inline (`Child of: <id>`, `N children`)
- [x] Project name (not just ID) shown in multi-project list views and in detail view
- [x] Recurrence rule, all-day flag, and non-default time zone surfaced in detail view
- [x] `is_pinned` exposed in JSON output
- [x] Child IDs listed in detail view (matching JSON's `child_ids`)

**Bug fixes**
- [x] All-day tasks show on the same day as in the TickTick app, even while you travel (read in each task's own zone), and plain dates you send land on that exact day in every timezone
- [x] `pin_tasks` no longer wipes dates and other fields. The V2 endpoint replaces the whole task, so pin/unpin now re-sends the full task, the same way TickTick's own web client does
- [x] Batch operations validate that target task and parent IDs exist instead of silently reporting success on wrong IDs
- [x] `batch_update_tasks` no longer wipes `repeat_flag` / `is_all_day` / `time_zone` on sparse partial updates
- [x] `batch_update_tasks` also preserves recurrence-anchor fields (`repeatFrom`, `repeatFirstDate`, `repeatTaskId`, `exDate`) — without these, TickTick keeps the RRULE but silently kills the chain (no next occurrence) when a recurring task's due date is moved
- [x] V2 wire-format datetime conversion no longer drifts by +N hours when input has a non-UTC tzinfo
- [x] Empty `repeatFrom` from V2 no longer fails Pydantic validation

**Project conventions**
- [x] `CLAUDE.md` with project instructions for Claude Code sessions
- [x] `TODO.md` for cross-session task tracking
- [x] Roughly 1,400 lines of upstream dead code removed (including an unused routing table), and the test suite grew from 309 to 472 test functions

---

## Available MCP Tools (44 Total)

All mutation tools accept lists for batch operations (1-100 items).

### Task Tools (Batch-Capable)
| Tool | Description |
|------|-------------|
| `ticktick_create_tasks` | Create 1-50 tasks with titles, dates, tags, etc. |
| `ticktick_get_task` | Get task details by ID |
| `ticktick_list_tasks` | List tasks (active/completed/abandoned/deleted via status filter; `project_id` / `tag` / `priority` / `kind` filters work on **every** status; `due_before` / `due_after` for date ranges, combine both for a range; optional `sort`). **Paginated**: pass `offset` to continue; `total` is the true count. |
| `ticktick_update_tasks` | Update 1-100 tasks (includes column assignment) |
| `ticktick_complete_tasks` | Complete 1-100 tasks |
| `ticktick_delete_tasks` | Delete 1-100 tasks (moves to trash) |
| `ticktick_move_tasks` | Move 1-50 tasks between projects |
| `ticktick_set_task_parents` | Set parent-child relationships for 1-50 tasks |
| `ticktick_unparent_tasks` | Remove parent relationships from 1-50 tasks |
| `ticktick_search_tasks` | Search **active** tasks by text and/or filters (`project_id`, `kind` — one or a list, `tag`, `priority`, due/created date ranges). Optional `query`; newest-first by default with a `sort` param. **Paginated** — pass `offset` to continue; `total` is the true count. |
| `ticktick_pin_tasks` | Pin or unpin 1-100 tasks |

### Project Tools
| Tool | Description |
|------|-------------|
| `ticktick_list_projects` | List all projects. **Paginated** — pass `offset` to continue. |
| `ticktick_get_project` | Get project details with tasks |
| `ticktick_create_project` | Create a new project |
| `ticktick_update_project` | Update project properties |
| `ticktick_delete_project` | Delete a project |

### Folder Tools
| Tool | Description |
|------|-------------|
| `ticktick_list_folders` | List all folders. **Paginated** — pass `offset` to continue. |
| `ticktick_create_folder` | Create a folder |
| `ticktick_rename_folder` | Rename a folder |
| `ticktick_delete_folder` | Delete a folder |

### Kanban Column Tools
| Tool | Description |
|------|-------------|
| `ticktick_list_columns` | List columns for a kanban project. **Paginated** — pass `offset` to continue. |
| `ticktick_create_column` | Create a kanban column |
| `ticktick_update_column` | Update column name or order |
| `ticktick_delete_column` | Delete a kanban column |

### Tag Tools
| Tool | Description |
|------|-------------|
| `ticktick_list_tags` | List all tags. **Paginated** — pass `offset` to continue. |
| `ticktick_create_tag` | Create a tag with color |
| `ticktick_update_tag` | Update tag properties (includes rename via label) |
| `ticktick_delete_tag` | Delete a tag |
| `ticktick_merge_tags` | Merge two tags |

### Habit Tools (Batch-Capable)
| Tool | Description |
|------|-------------|
| `ticktick_habits` | List all habits. **Paginated** — pass `offset` to continue. |
| `ticktick_habit` | Get habit details |
| `ticktick_habit_sections` | List sections (morning/afternoon/night) |
| `ticktick_create_habit` | Create a new habit |
| `ticktick_update_habit` | Update habit properties (includes archive/unarchive) |
| `ticktick_delete_habit` | Delete a habit |
| `ticktick_checkin_habits` | Check in 1-50 habits (supports backdating) |
| `ticktick_habit_checkins` | Get check-in history |

### User & Analytics Tools
| Tool | Description |
|------|-------------|
| `ticktick_get_profile` | Get user profile |
| `ticktick_get_status` | Get account status |
| `ticktick_get_statistics` | Get productivity stats |
| `ticktick_get_preferences` | Get user preferences |
| `ticktick_focus_heatmap` | Get focus heatmap data |
| `ticktick_focus_by_tag` | Get focus time by tag |
| `ticktick_auth_status` | Diagnose V1/V2 auth health (live check, no secrets) — use when tools fail with auth errors |

---

## Example Conversations

Once connected, you can ask Claude things like:

- "What tasks do I have due today?"
- "Show me everything due in the next 3 days"
- "Create a task to call John tomorrow at 2pm"
- "Show me my high priority tasks"
- "Which tasks are pinned?"
- "Mark the grocery shopping task as complete"
- "What's my current streak for the Exercise habit?"
- "Check in my meditation habit for today"
- "Create a new habit to drink 8 glasses of water daily"

---

## Health Check & Monitoring

The server exposes a `/health` endpoint:

```bash
curl https://your-app-production.up.railway.app/health
# {"status": "ok"}
```

Set this as the **Healthcheck Path** in Railway settings to ensure deployments are verified before going live.

### Test with MCP Inspector

```bash
npx @anthropic-ai/inspector https://your-app-production.up.railway.app/mcp
```

---

## Troubleshooting

### "Token exchange failed"
- Verify your Client ID and Client Secret are correct
- Ensure the Redirect URI matches exactly (including trailing slashes)
- Check that you're using the correct TickTick developer portal

### "Authentication failed"
- Check your TickTick username (email) and password
- Try logging into ticktick.com to verify credentials

### "V2 initialization failed"
- Your password may contain special characters — try changing it
- Check for 2FA/MFA (not currently supported)

### `need_captcha` from `/api/v2/user/signon` (V2 anti-bot wall)

If you see this in Railway logs:

```
V2 password sign-on failed: Authentication failed: {"errorCode":"need_captcha", ...}
V2 password sign-on failed. From a datacenter IP this is usually TickTick's anti-bot,
not a wrong password (the error code can be need_captcha / username_password_not_match
/ 429 even with correct credentials). Most reliable fix: set TICKTICK_V2_COOKIES ...
```

TickTick's anti-bot system has flagged your password login (usually because too many login attempts came from your Railway datacenter IP in a short window — e.g. a crash loop, or many redeploys in a row). The server keeps running in **V1-only degraded mode** — task/project tools still work, but tags/folders/habits/focus/subtasks return a "V2 unavailable" error.

> **Note:** the same anti-bot throttle can also show up as **`HTTP 429 Too Many Requests`** on `/user/signon` (instead of `need_captcha`), and when it does it **also** throttles the cookie fallback's `/user/status` check, so even a fresh `TICKTICK_V2_COOKIES` can fail to verify. If `ticktick_auth_status` says "rate-limited (HTTP 429)", treat it as the same problem as `need_captcha`: a 429 is a throttle, so refreshing the cookie usually won't help while it's active. Stopping the repeated sign-ons and letting it clear tends to help more (see the "least techy" steps below and the restart-cause investigation).

> **Tip:** call the **`ticktick_auth_status`** tool any time to get a live, plain-English read on what's authenticated, why V2 is down, whether your device id is valid, and the exact env var to fix — without exposing any secrets.

**What to do, in order of "least techy" → "most reliable":**

1. **Wait it out + set `TICKTICK_DEVICE_ID`.** Stop redeploying for several hours (the flag usually clears on its own). Then set `TICKTICK_DEVICE_ID` to a stable 24-char hex string in Railway so future redeploys don't look like new devices, and redeploy once.

2. **Best fix: set the V2 session cookie** (`TICKTICK_V2_COOKIES`). The server tries this **first**, before any password login, so when it's set and valid the server skips `/user/signon` entirely and reuses a session you've already established in your browser. It can't trigger `need_captcha` or a 429 because no login happens. See the next section for how to grab it.

### Grabbing `TICKTICK_V2_COOKIES` from a browser

You only need to do this if `need_captcha` is blocking the normal password login. The cookie string is sensitive — treat it like your password (paste only into Railway env vars, never into screenshots or chats). You only need **one** env var, `TICKTICK_V2_COOKIES`; the session token is extracted from it automatically.

**On a desktop browser (Chrome / Edge / Firefox):**

1. Open https://ticktick.com and sign in normally.
2. Open the browser **DevTools** (F12, or right-click → *Inspect*).
3. Go to the **Network** tab. In the filter box type `batch/check` (this is the V2 sync endpoint the app polls).
4. Click around in TickTick (e.g. switch to "Today") so a request appears.
5. Click any `batch/check/0` (or similar V2) request. In the right pane, look at **Request Headers**.
6. Find the **`Cookie:`** header (under *Request* Headers — what the browser *sends*, not `Set-Cookie` under Response Headers). Copy its **full value verbatim**. The order of the pieces is arbitrary — it may start with `tt_distid=`, `_ga=`, or anything else, and it will contain many entries (`tt_distid`, `_ga`, `t`, `__stripe_mid`, `SESSION`, `ap_user_id`, `AWSALB`, …). Copy the **whole string as-is** and paste it into `TICKTICK_V2_COOKIES` — that's the only env var you need. The server reads each `key=value` pair individually (order doesn't matter, analytics entries are harmless) and extracts the session token from the `t` cookie automatically.
7. That's it — no need to isolate the token by hand. (The cookie string **must** contain a `t=...` entry; that's the session token. If for some reason it doesn't, you can set `TICKTICK_V2_TOKEN` separately to override.)
8. In Railway, set both env vars and redeploy. Logs should show `V2 authenticated via pre-obtained session token (fallback)`.

The token typically lasts months. If you ever see `V2 token fallback also failed` in the logs, check **why** before re-grabbing the cookie:

- **`... rate-limited (HTTP 429) ...`** means TickTick is **throttling** you, not that the cookie is stale. Re-grabbing the cookie will **not** help while throttled. This usually means the server is restarting too often and re-running sign-on each time (Railway app-sleeping, a crash loop, or repeated redeploys). Stop redeploying, fix the restart cause, and let the throttle clear (can take hours). Run `ticktick_auth_status` to confirm the verdict.
- **`... 401 ...` / "probably stale"** means the session really has expired. Repeat the steps above to get a fresh cookie.

### "V1 OAuth token expired or invalid"
- Your `TICKTICK_ACCESS_TOKEN` has expired (TickTick OAuth tokens last ~6 months) or been revoked
- Run `ticktick-sdk auth` again to mint a fresh token (same Step 2 from setup)
- Update `TICKTICK_ACCESS_TOKEN` in Railway and redeploy

### "Configuration incomplete"
- Make sure all 5 required environment variables are set in Railway
- Check for typos in variable names

### Railway deployment fails
- Check the build logs in Railway dashboard
- Make sure the repo has the `Dockerfile` in the root

---

## License

MIT License — see [LICENSE](LICENSE) for details.

Based on [ticktick-sdk](https://github.com/dev-mirzabicer/ticktick-sdk) by dev-mirzabicer.
