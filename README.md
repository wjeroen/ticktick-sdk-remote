# TickTick Remote MCP Server

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A remote [MCP](https://modelcontextprotocol.io/) (Model Context Protocol) server for [TickTick](https://ticktick.com), designed to run on [Railway](https://railway.app) so you can use it from **Claude.ai**, **Claude Mobile** (iOS/Android), and any MCP-compatible client — no local setup needed.

Forked from [dev-mirzabicer/ticktick-sdk](https://github.com/dev-mirzabicer/ticktick-sdk). Includes full support for [Dida365 (滴答清单)](https://dida365.com).

> **Developers:** for how the internals work — architecture, V1/V2 routing, data models, API quirks, response formatting/pagination, and using the Python SDK directly — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Table of Contents

- [Quick Start (Deploy to Railway)](#quick-start-deploy-to-railway)
- [Features](#features)
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
| `TICKTICK_TIMEZONE` | **Recommended** | Your local timezone for correct date display (default: `UTC`). Without this, all-day tasks may show the wrong date — see note below. |
| `TICKTICK_HOST` | No | API host: `ticktick.com` (default) or `dida365.com` (Chinese version) |
| `TICKTICK_TIMEOUT` | No | Request timeout in seconds (default: `30`) |
| `TICKTICK_DEVICE_ID` | **Strongly recommended** | Stable device id for V2 API (24-char hex). If unset, a fresh random id is generated every redeploy — see note below. |
| `TICKTICK_V2_COOKIES` | Recommended | Full Cookie header string from a logged-in TickTick browser tab. **Tried first** (before password sign-on) because it makes no login call and so can't trip TickTick's anti-bot. Strongly recommended on a server; password sign-on from a datacenter IP is unreliable. The session token (`t` cookie) is extracted from it automatically. See "If V2 sign-on gets captcha-walled" below. |
| `TICKTICK_V2_TOKEN` | No | Optional override for the session token — normally unnecessary, it's auto-extracted from the `t` cookie in `TICKTICK_V2_COOKIES`. |
| `TICKTICK_V2_IMPERSONATE` | No | Browser profile used for the V2 transport to get past TickTick's anti-bot (which 429s plain Python clients). Default `chrome`. Set to `off` to use plain httpx. Requires the `curl_cffi` dependency (included). |
| `MCP_BEARER_TOKEN` | No | Bearer token for server authentication — see note below |
| `PORT` | No | Server port (default: `8000`, Railway sets this automatically) |

> **`TICKTICK_DEVICE_ID`:** TickTick tracks the devices logging into your account. Without this env var, every Railway redeploy invents a new random device id, so each redeploy looks like *"a stranger on a new device just logged in with your password"* — which can trigger TickTick's anti-bot CAPTCHA wall (`need_captcha`) and break V2 sign-on. Pick any stable 24-character hex string (e.g. the value printed in your first deploy's logs as `TICKTICK_DEVICE_ID is not set... auto-generated: <value>`) and paste it into Railway.

> **Timezone:** TickTick stores all-day task dates as midnight in your local timezone, expressed as UTC. Without `TICKTICK_TIMEZONE`, a task due March 14 in Brussels appears as March 13. Set this to your [IANA timezone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) — the "TZ identifier" column on that page. Common examples: `Europe/Brussels`, `Europe/London`, `America/New_York`, `America/Chicago`, `America/Los_Angeles`, `Asia/Tokyo`, `Asia/Shanghai`, `Australia/Sydney`.

> **Note on MCP_BEARER_TOKEN**: Claude.ai's custom connector UI does not currently support bearer token auth. If you set this variable, requests without the correct `Authorization: Bearer <token>` header will be rejected. Leave it unset for Claude.ai compatibility.

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

1. Go to **claude.ai** → **Customize** → **Connectors**
2. Click **"Add custom connector"**
3. Enter a name (e.g., "TickTick")
4. Enter URL: `https://your-app-production.up.railway.app/mcp` (Don't forget /mcp!)
5. Enter TICKTICK_CLIENT_ID and TICKTICK_CLIENT_SECRET
6. Click **"Add"**

#### Claude Desktop / Claude Code (Local Alternative)

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
- [x] Remote HTTP server (streamable-http) for Railway deployment, replacing upstream's stdio-only local MCP
- [x] Bearer token authentication for the HTTP transport
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
- [x] `from_date`/`to_date` now honored for completed/abandoned status (previously silently ignored)

**Pagination & response sizing**
- [x] Budget-aware pagination across **all** list-returning tools (`list_tasks`, `search_tasks`, `list_projects`, `list_folders`, `list_tags`, `list_columns`, `habits`) — pass `offset`, response surfaces `next_offset`
- [x] `total` always reports the **true match count**, independent of `limit`, and `next_offset` is non-null whenever more results remain. (Previously a small `limit` made `search_tasks`/`list_tasks` pre-slice the list, so `total` echoed the page size and `next_offset` went null — a false "this is everything." `limit` is now the page size, enforced inside the paginator, not a cap on the count.)
- [x] Per-task `content` capped at 500 chars in JSON list views (with `content_truncated` flag + `_content_hint` pointing at `ticktick_get_task` for the full text)
- [x] Exact size-checking — no more zero-task truncated responses (and a single over-budget item is still emitted one-per-page so paging can't stall)

**Task search** (`ticktick_search_tasks`)
- [x] Newest-first by default (`sort=created_desc`) plus a `sort` param (`created_*`, `modified_*`, `due_*`, `priority_desc`, `title_asc`) — previously results were oldest-first, which truncated the newest matches away under a limit
- [x] Structured filters: `project_id`, `kind` (TEXT/NOTE/CHECKLIST), `tag`, `priority`, and `due_before`/`due_after`/`created_before`/`created_after`
- [x] `query` is now optional — omit it for a pure filter lookup (e.g. "latest NOTE in project X" via `project_id` + `kind=NOTE` + `limit=1`)
- [x] Optional `sort` on `ticktick_list_tasks` too (defaults to the existing per-status order)

**Task list & detail rendering**
- [x] `[HIGH]` / `[MEDIUM]` / `[LOW]` / `[NONE]` priority labels visible in markdown list rows
- [x] `[PINNED]` / `[DONE]` / `[ABANDONED]` / `[DAILY|WEEKLY|MONTHLY|YEARLY|REPEATS]` status flags in list rows
- [x] Parent/children relationships shown inline (`Child of: <id>`, `N children`)
- [x] Project name (not just ID) shown in multi-project list views and in detail view
- [x] Recurrence rule, all-day flag, and non-default time zone surfaced in detail view
- [x] `is_pinned` exposed in JSON output
- [x] Child IDs listed in detail view (matching JSON's `child_ids`)

**Bug fixes**
- [x] Timezone handling: all-day tasks no longer off by one day (uses `TICKTICK_TIMEZONE`)
- [x] `batch_update_tasks` no longer wipes `repeat_flag` / `is_all_day` / `time_zone` on sparse partial updates
- [x] `batch_update_tasks` also preserves recurrence-anchor fields (`repeatFrom`, `repeatFirstDate`, `repeatTaskId`, `exDate`) — without these, TickTick keeps the RRULE but silently kills the chain (no next occurrence) when a recurring task's due date is moved
- [x] V2 wire-format datetime conversion no longer drifts by +N hours when input has a non-UTC tzinfo
- [x] Empty `repeatFrom` from V2 no longer fails Pydantic validation

**Project conventions**
- [x] `CLAUDE.md` with project instructions for Claude Code sessions
- [x] `TODO.md` for cross-session task tracking

---

## Available MCP Tools (44 Total)

All mutation tools accept lists for batch operations (1-100 items).

### Task Tools (Batch-Capable)
| Tool | Description |
|------|-------------|
| `ticktick_create_tasks` | Create 1-50 tasks with titles, dates, tags, etc. |
| `ticktick_get_task` | Get task details by ID |
| `ticktick_list_tasks` | List tasks (active/completed/abandoned/deleted via status filter; supports `due_before` / `due_after` for date-range filtering — combine both for a range; optional `sort`). **Paginated** — pass `offset` to continue; `total` is the true count. |
| `ticktick_update_tasks` | Update 1-100 tasks (includes column assignment) |
| `ticktick_complete_tasks` | Complete 1-100 tasks |
| `ticktick_delete_tasks` | Delete 1-100 tasks (moves to trash) |
| `ticktick_move_tasks` | Move 1-50 tasks between projects |
| `ticktick_set_task_parents` | Set parent-child relationships for 1-50 tasks |
| `ticktick_unparent_tasks` | Remove parent relationships from 1-50 tasks |
| `ticktick_search_tasks` | Search **active** tasks by text and/or filters (`project_id`, `kind`, `tag`, `priority`, due/created date ranges). Optional `query`; newest-first by default with a `sort` param. **Paginated** — pass `offset` to continue; `total` is the true count. |
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
