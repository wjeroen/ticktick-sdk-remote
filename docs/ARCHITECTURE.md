# Architecture & Internals

> **Audience:** developers and maintainers of this repo.
> **Companion:** [`README.md`](../README.md) is the user/operator guide (Railway
> deploy steps, the full env-var table, the 44-tool table, response-format and
> pagination behavior as seen by an MCP client, example conversations, and
> troubleshooting). This document is the **internals reference** — the *why* and
> *how* behind the code. Where the README already covers something well (env
> vars, the tool list, deploy steps), this doc points at it instead of
> repeating it and goes deeper on the mechanics.

This is a single-user **remote TickTick MCP server**. It runs as an HTTP
(streamable-http) MCP server — designed to live on Railway — and exposes
TickTick to Claude.ai / Claude mobile / any MCP client. Under the hood it drives
TickTick through **two** different APIs and presents them as one clean toolset.

The code began life as a general-purpose TickTick SDK, but everything here is
now treated as first-class, owned code. There is no separate "library" to defer
to; the "SDK layer" (`TickTickClient` / `UnifiedTickTickAPI` / the V1+V2
clients) is simply the engine the MCP server is built on.

---

## Table of contents

1. [Overview](#1-overview)
2. [Codebase map](#2-codebase-map)
3. [Architecture: the layers and data flow](#3-architecture-the-layers-and-data-flow)
4. [Authentication & resilience](#4-authentication--resilience)
5. [How the code picks V1 vs V2](#5-how-the-code-picks-v1-vs-v2)
6. [Data models](#6-data-models)
7. [API internals (V1 & V2 endpoints, batch semantics)](#7-api-internals)
8. [TickTick API quirks](#8-ticktick-api-quirks)
9. [MCP server & tools](#9-mcp-server--tools)
10. [Configuration, constants & exceptions](#10-configuration-constants--exceptions)
11. [Using the SDK directly (Python API)](#11-using-the-sdk-directly-python-api)

---

## 1. Overview

### What this project is

A remote MCP server for TickTick (and Dida365 / 滴答清单). It combines:

- **V1 — the official OAuth2 "Open API"** (`/open/v1`). Documented, stable, but
  feature-poor: basic task/project CRUD only.
- **V2 — the unofficial, reverse-engineered session API** (`/api/v2`) used by
  TickTick's own web/mobile apps. Session-cookie auth, undocumented, but it has
  *everything*: tags, folders, kanban columns, habits, focus/pomodoro, subtasks,
  user stats, "all tasks" listing, completed/abandoned/trash listing, etc.

A unified layer hides the V1/V2 split behind one set of Pydantic models and one
async API surface, and the MCP server turns that surface into 44 tools.

### Entry point & runtime

- Console script: `ticktick-sdk = "ticktick_sdk.cli:cli_main"` (see
  `pyproject.toml`).
- Server is started with `python -m ticktick_sdk` (`__main__.py` →
  `server.main()`) or `ticktick-sdk` / `ticktick-sdk server`.
- Transport: **FastMCP** over **streamable-http**, mounted at `/mcp`, plus a
  plain `/health` route. Railway sets `PORT`; the server binds `0.0.0.0`.
- Single-user by design: one TickTick account, credentials supplied via env
  vars. There is no per-request user identity.

### Big-picture flow

```
Claude.ai / Claude mobile / MCP client
        │  streamable-http  (POST /mcp)
        ▼
FastMCP server (server.py)            ── 44 @mcp.tool functions, /health, optional bearer-auth middleware
        │  one async TickTickClient, created at lifespan startup
        ▼
TickTickClient (client/client.py)     ── friendly facade: convenience methods, string→enum coercion
        │
        ▼
UnifiedTickTickAPI (unified/api.py)   ── routing (inline), model conversion, batch merge/error-checking
        │
   ┌────┴─────────────┐
   ▼                  ▼
TickTickV1Client   TickTickV2Client   ── HTTP clients (api/v1, api/v2) over a shared BaseTickTickClient
   │  OAuth2 token    │  session cookies + X-Device
   ▼                  ▼
api.ticktick.com/open/v1     api.ticktick.com/api/v2
```

---

## 2. Codebase map

Everything lives under `src/ticktick_sdk/`. (File sizes are deliberately *not*
listed here — they rot. Use your editor.)

```
src/ticktick_sdk/
├── __init__.py        Public Python surface (TickTickClient, models, exceptions, settings).
├── __main__.py        `python -m ticktick_sdk` → server.main().
├── server.py          The MCP server. All 44 @mcp.tool functions, /health, bearer-auth
│                      middleware, tool filtering, lifespan that builds the TickTickClient,
│                      and the list-sort keys used before pagination.
├── cli.py             Argument parsing & the `ticktick-sdk` command (server / auth subcommands,
│                      --host, --enabledModules, --enabledTools). Holds TOOL_MODULES.
├── auth_cli.py        The `ticktick-sdk auth` OAuth2 flow (browser + --manual modes) that
│                      mints the V1 access token. Prints the token for you to paste into Railway.
├── settings.py        TickTickSettings (pydantic-settings). Reads TICKTICK_* env vars,
│                      device-id validity checks, V2 cookie/token fallback fields.
├── constants.py       Enums (TaskStatus, TaskPriority, TaskKind, ProjectKind, ViewMode,
│                      SubtaskStatus, APIVersion), host-URL helpers, datetime formats, V2 headers.
├── exceptions.py      The TickTickError hierarchy.
│
├── client/
│   └── client.py      TickTickClient — the high-level facade.
│
├── unified/
│   ├── api.py         UnifiedTickTickAPI — the heart: initialize()/_authenticate_v2(),
│   │                  per-operation V1/V2 routing, model conversion, batch ops, get_auth_status().
│   └── router.py      APIRouter — holds the V1/V2 clients and reports availability. No routing table.
│
├── api/
│   ├── base.py        BaseTickTickClient — shared httpx plumbing, headers, error mapping.
│   ├── v1/
│   │   ├── client.py  TickTickV1Client — official OAuth2 REST endpoints.
│   │   ├── auth.py    OAuth2Handler + OAuth2Token (authorize-URL, code exchange).
│   │   └── types.py   V1 request/response TypedDicts.
│   └── v2/
│       ├── client.py  TickTickV2Client — reverse-engineered batch endpoints.
│       ├── auth.py    SessionHandler + SessionToken (password sign-on, device-id, cookies).
│       └── types.py   V2 request/response TypedDicts.
│
├── models/            Unified Pydantic models (the canonical shapes everything converts to/from).
│   ├── base.py        TickTickModel base (config, parse_datetime/format_datetime, to/from_v1/v2).
│   ├── task.py        Task, ChecklistItem, TaskReminder.
│   ├── project.py     Project, ProjectGroup (folder), Column, ProjectData, SortOption.
│   ├── tag.py         Tag.
│   ├── habit.py       Habit, HabitSection, HabitCheckin, HabitPreferences.
│   └── user.py        User, UserStatus, UserStatistics, TaskCount.
│
└── tools/             MCP-specific helpers (not used by the plain Python API).
    ├── inputs.py      Pydantic input models for every tool + the ResponseFormat enum.
    └── formatting.py  Markdown/JSON formatters and the budget-aware pagination helpers.
```

`docs/api-analysis/` holds the **raw reverse-engineered V2 reference** (captured
from HAR files): `tasks-pin-api.md`, `projects-folders-api.md`,
`kanban-columns-api.md`, `habits-api.md`. When you need the exact V2
request/response JSON for an endpoint, read those — this document summarizes V2
behavior but does not duplicate the raw captures.

---

## 3. Architecture: the layers and data flow

Three layers sit between the MCP tool and TickTick's servers. Each one has a
single, narrow job.

### Layer 1 — `TickTickClient` (the facade)

**File:** `client/client.py`

The friendly, well-documented entry point. It owns one `UnifiedTickTickAPI`
instance, manages the async lifecycle (`connect()`/`disconnect()` and the
`async with` protocol), and does small ergonomic conversions before delegating —
most importantly **string→int priority coercion** (`"high"` → `5` via
`{"none":0,"low":1,"medium":3,"high":5}`). It also provides convenience queries
that are just filters over a full fetch (`get_today_tasks`, `get_overdue_tasks`,
`search_tasks`, `get_tasks_by_tag`, `get_tasks_by_priority`).

Build it with `TickTickClient.from_settings()`, which reads `TickTickSettings`,
calls `validate_all_ready()` (raises `TickTickConfigurationError` if a required
credential is missing), and forwards everything (including the V2 cookie/token
fallbacks and device id) into the unified API.

Method count is large (single-item + batch variants of every operation). The
batch methods (`create_tasks`, `update_tasks`, `complete_tasks`, `delete_tasks`,
`move_tasks`, `make_subtasks`, `unparent_subtasks`, `pin_tasks`/`unpin_tasks`,
`checkin_habits`) are what the MCP server actually calls — the single-item
methods exist for direct Python use.

### Layer 2 — `UnifiedTickTickAPI` (routing + conversion + batch)

**File:** `unified/api.py`

This is the brain. Responsibilities:

- **Lifecycle / auth:** `initialize()` builds both HTTP clients, runs V2
  authentication (with the fallback chain — see [§4](#4-authentication--resilience)),
  builds the `APIRouter`, verifies both clients, and decides whether the server
  can run (and in what mode). It is the only place that knows about degraded
  mode.
- **Routing:** every method decides V1 vs V2 **inline** via
  `self._router.has_v2` / `has_v1`. There is no routing table (see [§5](#5-how-the-code-picks-v1-vs-v2)).
- **Model conversion:** translates unified models ⇄ V1/V2 wire dicts
  (`Task.to_v2_dict(...)`, `Task.from_v2(...)`, etc.).
- **Batch semantics:** builds the V2 `add`/`update`/`delete` payloads,
  **pre-fetches and merges** for sparse updates, and **checks `id2error`** in
  batch responses to raise proper exceptions.
- **Diagnostics:** `get_auth_status()` powers the `ticktick_auth_status` tool.

### Layer 3 — `TickTickV1Client` / `TickTickV2Client` (HTTP)

**Files:** `api/v1/client.py`, `api/v2/client.py`, both on `api/base.py`.

`BaseTickTickClient` (abstract) holds the shared httpx machinery: a lazily
created `httpx.AsyncClient` (base-url + timeout + `follow_redirects=True`), base
headers, the core `_request()` method, the `_get/_post/_put/_delete` and
`_get_json/_post_json` helpers, and `_handle_error_response()` (status-code and
`errorCode` → exception mapping). Subclasses implement four abstract members:
`api_version`, `base_url`, `is_authenticated`, `_get_auth_headers`. Both also
expose `verify_authentication()` (V1 pings `GET /project`; V2 pings the sync
endpoint) used by `APIRouter.verify_clients()`.

### Request/response walk-through (creating a task)

```
TickTickClient.create_task(title="Buy milk", priority="high", tags=["shopping"])
   │  "high" → 5
   ▼
UnifiedTickTickAPI.create_task(...)
   │  validate (recurrence ⇒ start_date), default project to inbox,
   │  format dates to the V2 wire format, REQUIRE V2 (raise if down)
   ▼
TickTickV2Client.create_task(...)  →  POST /batch/task  {"add":[{...}]}
   ▼
TickTick V2 server  →  {"id2etag": {"<newId>": "<etag>"}, "id2error": {}}
   ▲
UnifiedTickTickAPI: pull the new id from id2etag, (optionally set parent
   separately because V2 ignores parentId on create), then re-fetch the
   full task via get_task() and return a Task model.
   ▲
TickTickClient → Task → MCP tool formats it as markdown/JSON.
```

---

## 4. Authentication & resilience

This is the area most worth understanding, because it's what keeps the server
alive when TickTick's anti-bot system gets grumpy. The README's *"`need_captcha`
… (V2 anti-bot wall)"* and cookie-grabbing sections cover the operator steps;
here is the mechanism.

### V1 — OAuth2 bearer token

**Files:** `api/v1/auth.py` (`OAuth2Handler`, `OAuth2Token`), `auth_cli.py`.

- You mint an access token **once**, out-of-band, by running `ticktick-sdk auth`
  (browser flow, or `--manual` for SSH/headless). That command runs the
  authorize-URL → code → token exchange and prints the token for you to paste
  into `TICKTICK_ACCESS_TOKEN` in Railway.
- At runtime the server just **uses** that pre-obtained token: the V1 client is
  constructed with it via `OAuth2Handler.set_access_token()`, and every request
  carries `Authorization: Bearer <token>`.
- `OAuth2Handler` still implements the interactive flow methods
  (`get_authorization_url`, `exchange_code`) used by `auth_cli.py`.
- **Token lifetime ≈ 6 months.** There is no automatic refresh in the server
  path — when it expires you re-run `ticktick-sdk auth` and update the env var.
  `OAuth2Token.is_expired` exists (60-second buffer) but only matters if the
  token came with an `expires_in`.
- On a 401 / failed V1 verification, `initialize()` logs a *specific* message
  telling you to re-mint via `ticktick-sdk auth` and update
  `TICKTICK_ACCESS_TOKEN` — not a generic "auth failed".

### V2 — session sign-on

**Files:** `api/v2/auth.py` (`SessionHandler`, `SessionToken`),
`api/v2/client.py`.

V2 mimics the web app. Sign-on is `POST /api/v2/user/signon?wc=true&remember=true`
with `{username, password}` plus two headers that make the request look like a
browser:

- `User-Agent: Mozilla/5.0 (rv:145.0) Firefox/145.0`
- `X-Device: {"platform":"web","version":6430,"id":"<device_id>"}`

On success the server captures the response **cookies** (and adds the returned
`token` as the `t` cookie if absent). For all subsequent V2 calls, the **cookie
header is the real auth mechanism** — the `t` cookie carries the session token —
alongside `X-Device` and the browser-y `User-Agent`. The token typically lasts
months.

#### Device id (why it matters)

`X-Device.id` must look like a MongoDB ObjectId (24 lowercase-hex chars).
TickTick tracks the devices logging into your account. If you don't set
`TICKTICK_DEVICE_ID`, `TickTickSettings` auto-generates a **fresh** id per
process (`_generate_object_id()` = 4-byte time + 5 random + 3 counter). On
Railway that means *every redeploy looks like a brand-new device logging in with
your password* — exactly the pattern that trips the captcha wall. Two settings
properties exist for diagnostics:

- `device_id_is_ephemeral` — true when the id was auto-generated (not provided
  via env). The server logs a startup warning when this is true.
- `device_id_looks_valid` — true only for a 24-char lowercase-hex string. A
  malformed id (wrong length / non-hex / stray quotes) can make sign-on fail
  with a *misleading* error like `username_password_not_match`. The server logs
  a startup warning when this is false.

#### Two-factor accounts (detected, not yet completable)

Sign-on **detects** 2FA: if the response has `authId` but no `token`,
`SessionHandler.authenticate()` raises `TickTickSessionError(requires_2fa=True,
auth_id=...)`. A TOTP-completion method — `SessionHandler.authenticate_2fa(auth_id,
totp_code)`, which POSTs to `/user/sign/mfa/code/verify` — exists as
**scaffolding for the planned 2FA support** (see TODO.md) but is **not wired into
the sign-on flow**, and on a headless server there's nobody to type a code anyway
(a real TOTP path would need a `TICKTICK_TOTP_SECRET` to generate it). So in
practice a 2FA-enabled account still can't password-sign-on — use the cookie
fallback below, which sidesteps 2FA entirely.

### The V2 fallback chain & degraded mode

`UnifiedTickTickAPI._authenticate_v2()` is built to **never crash the server**.
It records a reason/cooldown and returns; `initialize()` then decides the run
mode. The chain:

1. **Password sign-on** (Step 1). If `TICKTICK_USERNAME` + `TICKTICK_PASSWORD`
   are set, try `signon`. On success, cache `inbox_id`, mark
   `_v2_auth_method = "password"`, done.
   On *any* failure (captcha, wrong password, 2FA, network), record the reason,
   set `_v2_unavailable_until = now + 6h` (`_V2_PASSWORD_COOLDOWN`), and log an
   actionable error naming `need_captcha`, `TICKTICK_DEVICE_ID`, and the cookie
   fallback. The cooldown is **in-memory only and informational** — a redeploy
   always resets it; the server only attempts sign-on at startup today.

2. **Pre-obtained session token fallback** (Step 2). If `TICKTICK_V2_COOKIES`
   (and/or `TICKTICK_V2_TOKEN`) is set, build a `SessionToken` from it instead
   of logging in — so it **cannot** trigger `need_captcha` (no login happens).
   The cookie header is parsed with `_parse_cookie_header()` (tolerant of
   whitespace, trailing `;`, bare names). The session token is the `t` cookie by
   default; `TICKTICK_V2_TOKEN` only overrides it. The token is also force-set as
   the `t` cookie. The fallback is then **verified by hitting `GET /user/status`**,
   which both proves the session is live and recovers the real `inbox_id` /
   `user_id` (the hand-built `SessionToken` didn't have them). On success, mark
   `_v2_auth_method = "cookie"`. If `/user/status` 401s, the cookie is stale —
   clear the partial session (so `router.has_v2` is false) and record the reason.

3. **No fallback configured** → record the password-failure reason and bail (V2
   stays unavailable).

`initialize()` then checks the router:

- **Neither V1 nor V2 usable** → raise `TickTickConfigurationError`; the server
  refuses to start (the only genuinely fatal state).
- **V1 + V2** → normal.
- **V1 only (V2 degraded)** → log a DEGRADED warning. V2-routed tools
  (tags, folders, habits, focus, subtasks, full task listings) raise a friendly
  `TickTickAPIUnavailableError`. **Reality check:** because `create_task` and
  **all** batch task ops hard-require V2 (see [§5](#5-how-the-code-picks-v1-vs-v2)),
  V1-only mode is quite limited — most writes the MCP server performs are
  V2-gated. It is close to read-mostly without V2.
- **V2 only (V1 degraded)** → log a DEGRADED warning; only `get_project_with_data`
  (the V1-only path) is affected.

### `get_auth_status()` — live diagnostics

`UnifiedTickTickAPI.get_auth_status()` powers the `ticktick_auth_status` tool. It
does two **live** read pings (V1 `GET /project`, V2 `GET /user/status`) so it
catches credentials that expired *after* startup, not just the boot-time state.
It returns booleans + derived facts only — `v1_ok`, `v2_ok`, `v2_auth_method`
(`"password"`/`"cookie"`/`None`), `v2_unavailable_reason`, `v2_cooldown_until`,
plus the error strings. It exposes **no** secret values. The tool layer adds
device-id validity flags and a masked device id, and writes a plain-English
verdict with the exact env var to fix.

---

## 5. How the code picks V1 vs V2

**There is no routing table.** `APIRouter` (`unified/router.py`) is just a
dataclass holding the two clients and reporting availability:

- `has_v1` / `has_v2` — client exists **and** `is_authenticated`.
- `is_fully_configured` — both available.
- `verify_clients()` — pings each client's `verify_authentication()`, caches the
  result, returns `{"v1": bool, "v2": bool}`.
- `get_status()` — `{v1_available, v2_available, v1_verified, v2_verified,
  fully_configured}` (no secrets).

> Historical note: there used to be an `OPERATION_ROUTING` dict, an
> `APIPreference` enum, and `get_routing` / `can_execute` / `get_primary_client`
> / `get_fallback_client` helpers. They were **deleted** — the table was never
> consulted and even *lied* (it advertised V1 fallbacks for task create/batch
> that don't exist). Do not reintroduce them or document them as present.

Each method in `unified/api.py` decides inline (`if self._router.has_v2: ... elif
self._router.has_v1: ...`). The authoritative per-operation behavior:

| Operation(s) | Behavior |
|---|---|
| Most reads/writes (default) | **V2 first.** V2 carries richer data (tags, subtask links, pinning, more fields). |
| `update_task`, `delete_task`, `complete_task` (single) | V2 when available, **fall back to V1** when not. |
| `create_task` | **V2 required** — raises `TickTickAPIUnavailableError` if V2 is down. No V1 fallback. |
| **All batch task ops** (`batch_create_tasks`, `batch_update_tasks`, `batch_delete_tasks`, `batch_complete_tasks`, `batch_move_tasks`, `batch_set_task_parents`, `batch_unparent_tasks`, `batch_pin_tasks`) | **V2 required** — raise if V2 is down. |
| `get_task` | V2 (no `project_id` needed). Falls back to V1 **only if** a `project_id` was supplied (V1 needs it); otherwise raises. |
| `get_project_with_data` | **V1-only** (one call returns project + tasks + columns). |
| List all / completed / abandoned / deleted tasks; move; subtasks (parent/child); tags; folders (project groups); kanban columns; habits; focus; user profile/status/statistics; sync | **V2-only** — no V1 equivalent. |

Net effect: when V2 is captcha-walled, the server can do very little. If you ever
want declarative routing, build it from the real `api.py` behavior above — and
keep the "V2 required for create + batch" rule.

---

## 6. Data models

**Files:** `models/*.py`.

All API data is normalized into one set of Pydantic models so callers never see
the V1/V2 difference. There are ~17 model classes across 6 files. Most inherit
`TickTickModel` (`models/base.py`); the habit models are the exception — they
inherit plain `pydantic.BaseModel`.

### `TickTickModel` base

Config: `populate_by_name=True` (accept field name *or* camelCase alias),
`use_enum_values=True`, `validate_assignment=True`, `extra="ignore"` (tolerate
unknown API fields). It provides the shared helpers:

- `parse_datetime(value)` — tries V2-with-millis, V1-with-offset, `Z`-suffix, and
  ISO formats in turn; normalizes `+0000` → `+00:00`; returns `None` on failure
  (never raises). Used by `@field_validator(..., mode="before")` on every
  datetime field.
- `format_datetime(value, for_api)` — V1 → `%Y-%m-%dT%H:%M:%S%z`; V2 →
  `%Y-%m-%dT%H:%M:%S.000+0000`. **Important:** the V2 format hardcodes the
  literal `+0000`, and `strftime` does *not* convert the timezone — so this
  method first does `value.astimezone(timezone.utc)`. Without that, `18:00+02:00`
  would serialize as `18:00.000+0000` and TickTick would read it as 20:00. Naive
  datetimes are assumed UTC. (This is the fix behind the README's "no longer
  drifts by +N hours" bug note.)
- `from_v1(data)` / `from_v2(data)` — thin `model_validate` wrappers; the richer
  models override them. (Serializing *back* to the wire isn't in the base —
  `Task.to_v2_dict()` handles tasks; other resources' V2 payloads are built
  inline in `unified/api.py`.)

### Wire-format cheat sheet

| | V1 | V2 |
|---|---|---|
| Field naming (responses) | mixed; aliases map camelCase → snake_case | camelCase → snake_case via aliases |
| Datetime (task dates) | `YYYY-MM-DDThh:mm:ss±hhmm` (offset) | `YYYY-MM-DDThh:mm:ss.000+0000` (literal UTC suffix) |
| Completed/abandoned query dates | n/a (V1 can't list these) | `YYYY-MM-DD hh:mm:ss` (space, no `T`) |
| Focus heatmap/dist dates | n/a | `YYYYMMDD` (path segments) |
| Habit check-in date | n/a | `checkinStamp` integer `YYYYMMDD` |

### Task (`models/task.py`)

The most important model. `Task` is a **superset** — V2 has every V1 field plus
more, so there are no V1-only fields. Highlights:

- **Identity/content:** `id`, `project_id` (`projectId`), `etag` (V2),
  `title`, `content` (notes), `desc` (checklist description), `kind`
  (`TEXT`/`NOTE`/`CHECKLIST`).
- **Status:** `status` (int — see `TaskStatus`), `priority` (int — see
  `TaskPriority`), `progress` (0-100, V2), `deleted` (0/1 soft-delete, V2).
- **Dates:** `start_date`, `due_date`, `created_time`, `modified_time`,
  `completed_time`, `pinned_time` (V2), `time_zone`, `is_all_day`, `is_floating`
  (V2).
- **Recurrence:** `repeat_flag` (iCalendar RRULE) plus the V2 **recurrence
  anchors** `repeat_from` (`repeatFrom`), `repeat_first_date`, `repeat_task_id`,
  `ex_date`. `repeat_from` has a dedicated validator that coerces TickTick's
  occasional empty-string to `None` (the README's "empty `repeatFrom` no longer
  fails validation" fix).
- **Reminders:** `reminders: list[TaskReminder]`. A `mode="before"` validator
  accepts both V1 (list of trigger strings) and V2 (list of
  `{id, trigger}` dicts).
- **Hierarchy (V2):** `parent_id`, `child_ids`.
- **Checklist:** `items: list[ChecklistItem]`.
- **Organization:** `tags` (V2), `column_id` (V2, kanban), `sort_order`.
- **Collaboration/attachments/focus (V2):** `assignee`, `creator`,
  `completed_user_id`, `comment_count`, `attachments`, `focus_summaries`,
  `pomodoro_summaries`.

Computed properties: `is_completed`, `is_pinned`.

**`to_v2_dict(for_update=False)`** is where the tricky write behavior lives:

- For **creates** (`for_update=False`), `None` date fields are *omitted*.
- For **updates** (`for_update=True`), `None` `start_date`/`due_date` are sent as
  `""` (explicitly clear), `None` `pinned_time` is sent as `None` (clear pinned),
  and empty `tags` is sent as `[]` (clear tags). This is what makes
  "clear the date" / "remove tags" possible given V2's replace-not-patch
  behavior.
- The **recurrence anchors** (`repeatFrom`/`repeatFirstDate`/`repeatTaskId`/
  `exDate`) are round-tripped whenever present. They must be, because V2's
  `/batch/task` resets any field absent from the body — drop the anchors and
  TickTick keeps the RRULE but **silently kills the chain** (no next occurrence)
  when a recurring task's due date is moved. (README bug note: "preserves
  recurrence-anchor fields".)

**`ChecklistItem`** (`items`): `id`, `title`, `status` (uses `SubtaskStatus` —
`0`=normal, `1`=completed, which is **different** from `TaskStatus.COMPLETED`=2),
`completed_time`, `start_date`, `time_zone`, `is_all_day`, `sort_order`.

**`TaskReminder`**: `id` (V2 only) + `trigger` (iCalendar `TRIGGER:-PT…`).

### Project models (`models/project.py`)

- **`Project`** — `id`, `etag` (V2), `name`, `color`, `kind`
  (`TASK`/`NOTE`), `group_id` (folder), `view_mode` (`list`/`kanban`/`timeline`,
  default `list`), `sort_option`/`sort_order`/`sort_type`, `modified_time`,
  `is_owner`/`user_count` (V2), `closed` (archived), `muted`, `permission`
  (a plain string — `read`/`write`/`comment`; there is no `Permission` enum),
  and V2 team fields (`team_id`, `open_to_team`). (To remove a project from its
  folder, `unified/api.py` sends `groupId: "NONE"` in the update payload.)
- **`ProjectGroup`** (folder, **V2-only**) — `id`, `etag`, `name`, view/sort
  fields, `deleted`, `show_all`, team fields. (V2 folder payloads carry
  `listType: "group"`, built inline in `unified/api.py`.)
- **`Column`** (kanban) — `id`, `project_id`, `name`, `sort_order`,
  `created_time`/`modified_time` (V2), `etag`.
- **`ProjectData`** — container for the V1 `get_project_with_data` response:
  `project: Project`, `tasks: list[Task]`, `columns: list[Column]`. `from_v1()`
  parses all three; `from_v2()` builds it from a project + task list (V2 supplies
  no column data, so `columns` is empty).
- **`SortOption`** — `group_by` (`groupBy`), `order_by` (`orderBy`). This is a
  **Pydantic model**, not an enum.

### Tag (`models/tag.py`, **V2-only**)

`name` (lowercase identifier used in API calls), `label` (display name, may have
spaces), `raw_name`, `etag`, `color`, `parent` (parent tag *name* for nesting),
sort fields, `type`. `Tag.create(label, ...)` auto-derives
`name = label.lower().replace(" ", "")` (the lowercased identifier used in API
calls).

### Habit models (`models/habit.py`, **V2-only**, plain `BaseModel`)

- **`Habit`** — `id`, `name`, `icon` (`iconRes`, default
  `"habit_daily_check_in"`), `color` (default `#97E38B`), `sort_order`, `status`
  (`0`=active, `2`=archived), `encouragement`, `total_checkins`
  (`totalCheckIns`), timestamps, `habit_type` (`"Boolean"` or `"Real"`), `goal`
  (1.0 for boolean), `step`, `unit`, `etag`, `repeat_rule` (RRULE), `reminders`
  (HH:MM strings), `record_enable`, `section_id`, `target_days`,
  `target_start_date` (YYYYMMDD int), `completed_cycles`, `ex_dates`,
  `current_streak`, `style`. Properties: `is_numeric`, `is_active`,
  `is_archived`. (There is **no** `best_streak` field.)
- **`HabitSection`** — `id`, `name` (`_morning`/`_afternoon`/`_night`),
  `sort_order`, timestamps, `etag`; `display_name` property maps the underscore
  names to "Morning"/"Afternoon"/"Night".
- **`HabitCheckin`** — `habit_id`, `checkin_stamp` (**int** `YYYYMMDD`, e.g.
  `20260115`), `checkin_time`, `value`, `goal`, `status` (`2` = completed).
- **`HabitPreferences`** — `show_in_calendar`, `show_in_today`, `enabled`,
  `default_section_order`.

### User models (`models/user.py`, **V2-only**)

- **`UserStatus`** — subscription/account info. Critically holds `inbox_id`
  (`inboxId`), the default project for task creation. Also `user_id`, `username`,
  `is_pro` (`pro`), pro dates, and team flags.
- **`User`** — profile: `username`, `display_name`, `name`, `picture`, `locale`,
  `verified_email`, `filled_password`, `email`, etc.
- **`UserStatistics`** — `score`, `level`, today/yesterday/total completed,
  `score_by_day`, `task_by_day`/`week`/`month` (each a `dict[str, TaskCount]`),
  and a full block of pomodoro counters/durations/goals and history.
  Properties: `total_pomo_duration_hours`, `today_pomo_duration_minutes`.
- **`TaskCount`** — `complete_count` + `not_complete_count`, with a `total`
  property.

---

## 7. API internals

### Shared HTTP behavior (`api/base.py`)

- `_request()` checks auth (if required), merges base+auth+custom headers, issues
  the httpx call, maps timeouts/network errors to `TickTickAPIError`, and on
  non-success calls `_handle_error_response()`.
- `_get_json()` enforces a **V1 quirk**: a `200 OK` with an empty body means
  "not found" → it raises `TickTickNotFoundError`.
- `_handle_error_response()` prefers the body's `errorCode` (TickTick often
  returns HTTP 500 with a semantic code) and falls back to the HTTP status:

  | HTTP | Exception |
  |---|---|
  | 401 | `TickTickAuthenticationError` |
  | 403 | `TickTickForbiddenError` |
  | 404 | `TickTickNotFoundError` |
  | 429 | `TickTickRateLimitError` (reads `Retry-After`) |
  | 5xx | `TickTickServerError` |

  Error-code frozensets recognized in the body include
  `task_not_found`/`project_not_found`/`tag_not_found`/`folder_not_found`/… (→
  NotFound), `forbidden`/`permission_denied`/… (→ Forbidden),
  `unauthorized`/`invalid_token`/`token_expired`/`username_password_not_match`/
  `incorrect_password_too_many_times` (→ Auth). A body-level
  `id2error == "EXCEED_QUOTA"` maps to `TickTickQuotaExceededError`.

### V1 endpoints (`api/v1/client.py`)

Base URL: `get_api_base_v1()` → `https://api.{host}/open/v1`. Auth: OAuth2 bearer.
Auth helpers present: `set_access_token`, `get_authorization_url`,
`verify_authentication` (pings `GET /project`). (The old
`authenticate_with_code` / `refresh_token` client methods were **removed**.)

| Method | HTTP + path |
|---|---|
| `get_task(project_id, task_id)` | `GET /project/{projectId}/task/{taskId}` |
| `create_task(title, project_id, …)` | `POST /task` |
| `update_task(task_id, project_id, …)` | `POST /task/{taskId}` (V1 uses POST, not PUT) |
| `complete_task(project_id, task_id)` | `POST /project/{projectId}/task/{taskId}/complete` |
| `delete_task(project_id, task_id)` | `DELETE /project/{projectId}/task/{taskId}` |
| `get_projects()` | `GET /project` |
| `get_project(project_id)` | `GET /project/{projectId}` |
| `get_project_with_data(project_id)` | `GET /project/{projectId}/data` (project + tasks + columns; **unique to V1**) |
| `create_project(name, …)` | `POST /project` |
| `update_project(project_id, …)` | `POST /project/{projectId}` |
| `delete_project(project_id)` | `DELETE /project/{projectId}` |

V1 can't do: tags, habits, focus, true subtasks, folders, or "list all tasks".

### V2 endpoints (`api/v2/client.py`)

Base URL: `get_api_base_v2()` → `https://api.{host}/api/v2`. Auth: session
cookies (`t` cookie = token) + `X-Device` + the V2 `User-Agent`. Most mutations
go through **batch** endpoints. `verify_authentication()` pings the sync endpoint.

| Area | Method | HTTP + path |
|---|---|---|
| Sync | `sync()` | `GET /batch/check/0` (full account state) |
| User | `get_user_status()` | `GET /user/status` |
| User | `get_user_profile()` | `GET /user/profile` |
| User | `get_user_preferences()` | `GET /user/preferences/settings?includeWeb=…` |
| User | `get_user_statistics()` | `GET /statistics/general` |
| Task | `get_task(id)` | `GET /task/{id}` (no `project_id` needed) |
| Task | `batch_tasks(add, update, delete)` | `POST /batch/task` |
| Task | `create_task` / `update_task` / `delete_task` | convenience wrappers over `POST /batch/task` |
| Task | `move_task(s)` | `POST /batch/taskProject` |
| Task | `set_task_parent` / `unset_task_parent` | `POST /batch/taskParent` |
| Task | `get_completed_tasks` / `get_abandoned_tasks` | `GET /project/all/closed?…&status=Completed\|Abandoned` (dates `YYYY-MM-DD hh:mm:ss`) |
| Task | `get_deleted_tasks(start, limit)` | `GET /project/all/trash/pagination` |
| Project | `batch_projects(add, update, delete)` | `POST /batch/project` |
| Folder | `batch_project_groups(add, update, delete)` | `POST /batch/projectGroup` |
| Column | `get_columns(project_id)` | `GET /column/project/{projectId}` |
| Column | `batch_columns(add, update, delete)` | `POST /column` |
| Tag | `batch_tags(add, update)` | `POST /batch/tag` |
| Tag | `rename_tag(old, new_label)` | `PUT /tag/rename` |
| Tag | `delete_tag(name)` | `DELETE /tag?name=…` |
| Tag | `merge_tags(source, target)` | `PUT /tag/merge` |
| Focus | `get_focus_heatmap(start, end)` | `GET /pomodoros/statistics/heatmap/{YYYYMMDD}/{YYYYMMDD}` |
| Focus | `get_focus_by_tag(start, end)` | `GET /pomodoros/statistics/dist/{YYYYMMDD}/{YYYYMMDD}` |
| Habit | `get_habits()` | `GET /habits` |
| Habit | `get_habit_sections()` | `GET /habitSections` |
| Habit | `get_habit_preferences()` | `GET /user/preferences/habit?platform=web` |
| Habit | `batch_habits(add, update, delete)` | `POST /habits/batch` (archive/unarchive = update with `status` 2/0) |
| Habit | `get_habit_checkins(habit_ids, after_stamp)` | `POST /habitCheckins/query` |
| Habit | `batch_habit_checkins(add, update, delete)` | `POST /habitCheckins/batch` (supports backdating via `checkinStamp`) |

For exact request/response JSON for any of these, see `docs/api-analysis/`.

#### Pin & column assignment

Both are properties of a task **update**, not separate endpoints:

- **Pin/unpin:** set `pinnedTime` in the update — an ISO timestamp to pin, empty
  string `""` to unpin.
- **Move to kanban column:** set `columnId` in the update — a column id to
  assign, empty string `""` to remove from any column.

### Batch semantics (the important part)

V2 batch endpoints (`/batch/task` etc.) have two behaviors the unified layer
must paper over:

1. **Partial failures still return HTTP 200.** The response shape is
   `{"id2etag": {id: etag}, "id2error": {id: message}}`. Success populates
   `id2etag`; failures populate `id2error`. So the unified layer calls
   `_check_batch_response_errors()` after every batch write. It maps:
   `TASK_NOT_FOUND`/`PROJECT_NOT_FOUND`/`TAG_NOT_FOUND`/`"task not exists"` →
   `TickTickNotFoundError`; `EXCEED_QUOTA` → `TickTickQuotaExceededError`;
   anything else → `TickTickAPIError`.

2. **Updates *replace*, they don't patch.** TickTick treats the update payload as
   the task's new representation — any omitted field resets to its default
   (`repeatFlag` → null, `isAllDay` → false, `timeZone` wiped, tags cleared, and
   crucially the recurrence anchors lost). So `batch_update_tasks` **pre-fetches
   each task, merges the caller's sparse delta onto it, and sends the full task
   back** (via `Task.to_v2_dict(for_update=True)`, which preserves the anchors).
   Callers can therefore safely send "just `start_date`" without nuking the rest
   of the task. `column_id` is passed through separately because `to_v2_dict`
   doesn't serialize it.

   V2 batch operations also **silently ignore** nonexistent resources on some
   paths, so a few single-item ops (e.g. `complete_task`) first do a
   `get_task()` existence check to produce a real `TickTickNotFoundError`.
   **Reparenting is the subtle one:** `set_parent` silently "succeeds" against a
   deleted *parent*, so `set_task_parent` / `batch_set_task_parents` verify both
   the child **and** the parent exist first (deduped, via `_verify_parent_exists`)
   — otherwise a subtask attached to a since-deleted parent looks like success
   but is silently orphaned.

---

## 8. TickTick API quirks

The gotchas the code works around. These are the authoritative descriptions;
they must not contradict the README's "TickTick API Quirks" list (which is the
short operator-facing version).

1. **Recurrence requires `start_date`.** Creating a recurring task without a
   start date makes TickTick **silently drop** the RRULE. `create_task` validates
   this and raises `TickTickConfigurationError`.

2. **`parent_id` is ignored on create.** Setting a parent during task creation
   does nothing. You must call the parent endpoint afterward — the unified
   `create_task` does this for you (creates, then `set_task_parent`); the MCP
   tool is `ticktick_set_task_parents`. That endpoint also **silently no-ops
   against a deleted parent** (returns an etag, orphans the child), so the
   unified layer verifies the parent exists first — see §7.

3. **Soft delete.** Deleting a task moves it to trash (`deleted=1`); it's still
   fetchable. Listing trash is `GET /project/all/trash/pagination`.

4. **Clearing a due date needs both dates cleared.** If you clear `due_date` but
   leave `start_date`, TickTick restores `due_date` from `start_date`. Clear
   both. (`to_v2_dict(for_update=True)` sends `""` for cleared dates.)

5. **Tag order is not preserved.** The API may return a task's tags in any order;
   nothing here relies on ordering.

6. **The inbox is special.** It can't be deleted. Its id (`inbox{userId}`) is
   cached from `UserStatus` at sign-on and used as the default project for
   creation.

7. **V2 batch updates replace, not patch.** (See [§7 batch semantics](#batch-semantics-the-important-part).)
   Handled transparently by the pre-fetch-and-merge in `batch_update_tasks`.

8. **V2 datetime wire format hardcodes `+0000`.** It's
   `YYYY-MM-DDThh:mm:ss.000+0000` with a literal UTC suffix. `format_datetime`
   converts any input to UTC *before* formatting so the wall-clock time matches
   the offset (otherwise a `+02:00` time would be mis-read by 2 hours).

9. **Recurrence anchors are part of the task body.** Moving a recurring task's
   due date without round-tripping `repeatFrom`/`repeatFirstDate`/`repeatTaskId`/
   `exDate` keeps the RRULE but kills the series. (See [§6 Task](#task-modelstaskpy).)

10. **Errors hide behind HTTP 200/500.** Empty-body 200 = not found (V1); 200
    with `id2error` = partial batch failure; 500 with `errorCode` = a semantic
    error. The base client and batch checker decode these into typed exceptions.

11. **Timezone for all-day tasks.** TickTick stores all-day dates as midnight in
    your local zone expressed as UTC, so without `TICKTICK_TIMEZONE` an all-day
    task can appear a day off. The MCP layer uses the configured timezone when
    rendering/filtering dates. (Operator detail lives in the README.)

---

## 9. MCP server & tools

**File:** `server.py` (with `tools/inputs.py` and `tools/formatting.py`).

### Server construction

A single `FastMCP("ticktick_sdk", lifespan=…, host="0.0.0.0", port=$PORT,
streamable_http_path="/mcp")` instance. A `@mcp.custom_route("/health", …)`
returns `{"status": "ok"}` for platform health checks (and is exempt from
bearer auth). The **lifespan** builds one `TickTickClient.from_settings()` at
startup, stashes it in the lifespan context (tools fetch it via a small
`get_client(ctx)` helper), and closes it on shutdown. Startup also logs the
device-id warnings (`device_id_looks_valid` / `device_id_is_ephemeral`).

### The 44 tools

All 44 are `@mcp.tool` functions in `server.py`. The newest is
`ticktick_auth_status`. Grouped (the README has the full table with
descriptions):

- **Tasks (11):** `create_tasks`, `get_task`, `list_tasks`, `update_tasks`,
  `complete_tasks`, `delete_tasks`, `move_tasks`, `set_task_parents`,
  `unparent_tasks`, `search_tasks`, `pin_tasks`.
- **Projects (5):** `list_projects`, `get_project`, `create_project`,
  `update_project`, `delete_project`.
- **Folders (4):** `list_folders`, `create_folder`, `rename_folder`,
  `delete_folder`.
- **Kanban columns (4):** `list_columns`, `create_column`, `update_column`,
  `delete_column`.
- **Tags (5):** `list_tags`, `create_tag`, `update_tag`, `delete_tag`,
  `merge_tags`.
- **Habits (8):** `habits`, `habit`, `habit_sections`, `create_habit`,
  `update_habit`, `delete_habit`, `checkin_habits`, `habit_checkins`.
- **User & analytics (6):** `get_profile`, `get_status`, `get_statistics`,
  `get_preferences`, `focus_heatmap`, `focus_by_tag`.
- **Auth (1):** `auth_status`.

(All tool names are prefixed `ticktick_`. 11+5+4+4+5+8+6+1 = 44.)

### Tool inputs & batch limits

`tools/inputs.py` defines one Pydantic input model per tool (e.g.
`CreateTasksInput`, `UpdateTasksInput`, …) and the `ResponseFormat` enum
(`MARKDOWN` / `JSON`). Batch tools take a list with explicit length bounds.
(There are **no** `TaskCreateInput`/`TaskUpdateInput` aliases — the batch element
specs are `TaskCreateItem`/`TaskUpdateItem`.) The README's tool table states the
user-facing limits; mechanically they're `min_length`/`max_length` constraints on
the list fields — creates cap lower than updates/deletes, consistent with the
README's "1-50 / 1-100" guidance.

### Tool filtering

You can run a subset of tools. `cli.py` holds `TOOL_MODULES` — the canonical
map of module name → tool names, with eight modules: `tasks`, `projects`,
`folders`, `columns`, `tags`, `habits`, `user`, `focus`. The CLI resolves
`--enabledTools` and `--enabledModules` into the `TICKTICK_ENABLED_TOOLS` env
var (a comma-separated allowlist). At startup the server registers everything,
then **removes** the tools not in the allowlist (via the FastMCP tool manager).
So filtering is post-registration removal, driven by that env var.

### Bearer auth (optional)

If `MCP_BEARER_TOKEN` is set, an ASGI middleware wraps the app and rejects any
request whose `Authorization` header isn't exactly `Bearer <token>` (401),
exempting `/health`. **Caveat (also in the README):** Claude.ai's custom
connector UI doesn't support bearer auth, so leave this unset for Claude.ai
compatibility.

### Output formatting & pagination

**File:** `tools/formatting.py`.

Every tool can return **markdown** (default, human-friendly, omits empty fields,
~400 chars/task) or **JSON** (verbose, every field explicit, ~700 chars/task),
chosen by each tool's `response_format` param. Both formats go through the same
per-task formatter, so the fields shown are identical between `list_tasks` and
`search_tasks`.

**Budget-aware pagination.** MCP responses must stay under
`CHARACTER_LIMIT = 25000`. Every list-returning tool accepts an `offset` and the
server computes how many items actually fit:

- Markdown: accumulate row lengths against the budget (minus small header/footer
  reserves); when more remain, append a footer telling the model to call again
  with the next `offset`.
- JSON: serialize the whole envelope after each added item and back off when it
  would exceed the budget (exact size-checking — no "zero items but truncated"
  responses). The envelope carries `count`, `total`, `offset`, `next_offset`
  (null when done), and a `_pagination_hint` (omitted when everything fits).

**Deterministic ordering before paging.** Because TickTick's list endpoints
don't guarantee a stable order, `server.py` sorts before paginating so different
offsets don't duplicate or skip items:

- `_active_sort_key`: active tasks by `due_date` ascending (undated last), then
  `id`.
- `_completed_sort_key`: completed/abandoned by `completed_time` descending
  (undated last), then `id`.
- `_id_sort_key`: fallback by `id`.

**Per-task content cap in list views.** Task notes can be huge, so JSON *list*
views truncate `content` to `LIST_CONTENT_MAX_CHARS = 500`, set
`content_truncated: true` on affected tasks, and add a top-level `_content_hint`
pointing at `ticktick_get_task`. The **detail** view (`get_task`) never
truncates.

**Subtask enrichment.** List views build a `{child_id: {title, priority}}` meta
map from the same fetch, so children render with title + priority and **no extra
API calls** — markdown as indented sub-bullets, JSON as
`{id, title, priority_label}`. The meta map is filtered by the query's status, so
children of a different status are dropped; when that happens JSON adds
`total_children`/`children_hidden`/`_children_hint` and markdown appends an
"N more subtasks hidden" suffix. The **detail** view instead fetches each child
concurrently (one `get_task` per child) so it can show the same enriched rows; a
failed child fetch degrades to a bare id.

**Task list row format (markdown).** Each row renders, omitting empty fields:

```
- [PRIORITY] [PINNED] [DONE|ABANDONED] [REPEATS] **Title** (`id`) | Project: Name | Due: YYYY-MM-DD | Tags: a, b | Child of: `parent_id` | N children
```

`[PRIORITY]` is `[HIGH]`/`[MEDIUM]`/`[LOW]`/`[NONE]`; `[PINNED]`/`[DONE]`/
`[ABANDONED]` appear only when applicable (active is the implicit default).
Recurrence flags `[DAILY]`/`[WEEKLY]`/`[MONTHLY]`/`[YEARLY]` are parsed from the
RRULE's `FREQ=`, falling back to `[REPEATS]` for anything unrecognized.
`Project: Name` is shown only when the rendered list spans more than one project.

> Note: the old `format_batch_*` helper functions were removed — batch tool
> results are formatted inline. Don't document them as present.

### `ticktick_auth_status`

Calls `client`'s `get_auth_status()` (the live V1+V2 pings from
[§4](#get_auth_status--live-diagnostics)) and renders a verdict: what's
authenticated, why V2 is down (and the cooldown), whether the device id is a
valid 24-char hex, the V2 auth method, and the exact env var to fix. It masks the
device id and **never** prints credential values. Use it first when tools start
failing with auth errors.

---

## 10. Configuration, constants & exceptions

### Settings (`settings.py`)

`TickTickSettings` (pydantic-settings) reads `TICKTICK_*` env vars (prefix
`TICKTICK_`, `.env` supported, case-insensitive, unknown vars ignored). Secrets
(`client_secret`, `password`, `access_token`, `v2_cookies`, `v2_token`) are
`SecretStr`. The **full env-var table with descriptions and which are required
lives in the README** — don't duplicate it. Internals worth knowing:

- Required for a normal boot: `client_id`, `client_secret`, `access_token`
  (V1) and `username`, `password` (V2). `validate_all_ready()` raises
  `TickTickConfigurationError` listing the missing var names. (`validate_v1_ready`
  / `validate_v2_ready` exist for per-API checks.)
- `v2_cookies` / `v2_token` are the V2 fallback (see [§4](#the-v2-fallback-chain--degraded-mode)).
- `device_id` auto-generates if unset; `device_id_is_ephemeral` and
  `device_id_looks_valid` drive the startup warnings.
- Helper accessors return the unwrapped secret values
  (`get_v1_access_token`, `get_v2_password`, `get_v2_token`, `get_v2_cookies`).
- `get_settings()` is a lazy global; `configure_settings(**kwargs)` overrides it
  (used in tests).

### Constants (`constants.py`)

Host selection is dynamic so Dida365 works:
`get_api_host()` (reads `TICKTICK_HOST`, defaults `ticktick.com`, validates
against `{ticktick.com, dida365.com}`), `get_api_base_v1/v2(host)`,
`get_oauth_base(host)`. Other constants: `DEFAULT_TIMEOUT = 30.0`,
`OAUTH_SCOPES = ["tasks:read", "tasks:write"]`, the datetime format strings
`DATETIME_FORMAT_V1`/`DATETIME_FORMAT_V2`, and the V2 header values
`V2_USER_AGENT` / `V2_DEVICE_VERSION` (`6430`). Enums: `TaskStatus`
(`ABANDONED=-1`, `ACTIVE=0`, `COMPLETED_ALT=1`, `COMPLETED=2` — both 1 and 2 mean
done, see `is_completed`/`is_closed`), `TaskPriority` (`NONE=0`, `LOW=1`,
`MEDIUM=3`, `HIGH=5` — 2 and 4 are invalid; `from_string`/`to_string` helpers),
`TaskKind`, `ProjectKind`, `ViewMode`, `SubtaskStatus` (`NORMAL=0`,
`COMPLETED=1`), and `APIVersion` (a plain `StrEnum` `v1`/`v2` — it has **no**
`base_url` member). There is intentionally no `RepeatFrom` enum, no `Permission`
enum, no `SortOption` enum, no legacy `TICKTICK_API_BASE_V1/V2` /
`TICKTICK_OAUTH_BASE` constants, and no `X_DEVICE_TEMPLATE` /
`DATE_FORMAT_STATS` / `DATETIME_FORMAT_V2_QUERY` — those were removed.

### Exceptions (`exceptions.py`)

```
TickTickError
├── TickTickAuthenticationError
│   ├── TickTickOAuthError        (oauth_error, oauth_error_description)        — V1
│   └── TickTickSessionError      (requires_2fa, auth_id)                       — V2
├── TickTickAPIError              (status_code, response_body, api_version, endpoint)
│   ├── TickTickRateLimitError    (retry_after)
│   ├── TickTickNotFoundError     (resource_type, resource_id)
│   ├── TickTickForbiddenError
│   ├── TickTickServerError
│   └── TickTickQuotaExceededError (quota_type)
├── TickTickValidationError       (field, value, expected)
├── TickTickConfigurationError    (missing_config)
└── TickTickAPIUnavailableError   (operation, v1_error, v2_error)
```

Every exception carries a `details` dict; `TickTickAPIUnavailableError` is what
V2-required operations raise in degraded mode, and it names the operation so the
MCP consumer gets an actionable message.

---

## 11. Using the SDK directly (Python API)

Everything above describes the engine the MCP server runs on. You can also drive
that engine **directly from Python** — without the MCP server in the loop — by
constructing a `TickTickClient` and calling its methods. This is the
`from_settings()` facade (see [§3 Layer 1](#layer-1--ticktickclient-the-facade)),
and the snippets below are the canonical usage examples for it.

### Tasks

#### Creating Tasks

```python
from datetime import datetime, timedelta
from ticktick_sdk import TickTickClient

async with TickTickClient.from_settings() as client:
    # Simple task
    task = await client.create_task(title="Buy groceries")

    # Task with due date and priority
    task = await client.create_task(
        title="Submit report",
        due_date=datetime.now() + timedelta(days=1),
        priority="high",  # none, low, medium, high
    )

    # Task with tags and content
    task = await client.create_task(
        title="Review PR #123",
        content="Check for:\n- Code style\n- Tests\n- Documentation",
        tags=["work", "code-review"],
    )

    # Recurring task (MUST include start_date!)
    task = await client.create_task(
        title="Daily standup",
        start_date=datetime(2025, 1, 20, 9, 0),
        recurrence="RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR",
    )

    # Task with reminder
    task = await client.create_task(
        title="Meeting with team",
        due_date=datetime(2025, 1, 20, 14, 0),
        reminders=["TRIGGER:-PT15M"],  # 15 minutes before
    )

    # All-day task
    task = await client.create_task(
        title="Project deadline",
        due_date=datetime(2025, 1, 31),
        all_day=True,
    )
```

#### Managing Tasks

```python
async with TickTickClient.from_settings() as client:
    # Get a specific task
    task = await client.get_task(task_id="...")

    # Update a task
    task.title = "Updated title"
    task.priority = 5  # high priority
    await client.update_task(task)

    # Complete a task
    await client.complete_task(task_id="...", project_id="...")

    # Delete a task (moves to trash)
    await client.delete_task(task_id="...", project_id="...")

    # Move task to another project
    await client.move_task(
        task_id="...",
        from_project_id="...",
        to_project_id="...",
    )
```

#### Subtasks

```python
async with TickTickClient.from_settings() as client:
    # Create parent task
    parent = await client.create_task(title="Main task")

    # Create child task
    child = await client.create_task(title="Subtask")

    # Make it a subtask (parent_id in create is ignored by API)
    await client.make_subtask(
        task_id=child.id,
        parent_id=parent.id,
        project_id=child.project_id,
    )

    # Remove parent relationship
    await client.unparent_subtask(
        task_id=child.id,
        project_id=child.project_id,
    )
```

#### Querying Tasks

```python
async with TickTickClient.from_settings() as client:
    # All active tasks
    all_tasks = await client.get_all_tasks()

    # Tasks due today
    today = await client.get_today_tasks()

    # Overdue tasks
    overdue = await client.get_overdue_tasks()

    # Tasks by tag
    work_tasks = await client.get_tasks_by_tag("work")

    # Tasks by priority
    urgent = await client.get_tasks_by_priority("high")

    # Search tasks
    results = await client.search_tasks("meeting")

    # Recently completed
    completed = await client.get_completed_tasks(days=7, limit=50)

    # Abandoned tasks ("won't do")
    abandoned = await client.get_abandoned_tasks(days=30)

    # Deleted tasks (in trash)
    deleted = await client.get_deleted_tasks(limit=50)
```

#### Filtering Active Tasks with `ticktick_list_tasks`

The `ticktick_list_tasks` MCP tool supports filters that the Python SDK doesn't expose as standalone methods. For date-range queries, use `due_before` and `due_after` — combine both to get tasks due in a range:

| Parameter | Type | Example | Effect |
|-----------|------|---------|--------|
| `due_before` | `string` (YYYY-MM-DD) | `"2026-03-16"` | Active tasks due **on or before** this date |
| `due_after` | `string` (YYYY-MM-DD) | `"2026-03-16"` | Active tasks due **on or after** this date (combine with `due_before` for a range) |
| `has_due_date` | `boolean` | `false` | `true` = only tasks with a due date, `false` = only tasks **without** one (unscheduled) |
| `due_today` | `boolean` | `true` | Only tasks due today |
| `overdue` | `boolean` | `true` | Only tasks past their due date |
| `priority` | `string` | `"high"` | Filter by priority: `none` / `low` / `medium` / `high` |
| `tag` | `string` | `"work"` | Filter by tag name |
| `project_id` | `string` | `"abc123"` | Filter to a specific project |
| `status` | `string` | `"active"` | `active` (default), `completed`, `abandoned`, `deleted` |
| `limit` | `integer` | `25` | Max results to return (default 50) |

Example parameter combinations:

```
# Tasks due in the next 3 days (assuming today is 2026-03-13):
status="active", due_before="2026-03-16"

# Tasks due from a specific date onwards:
status="active", due_after="2026-03-16"

# Tasks due in a date range (March 16-20 inclusive):
status="active", due_after="2026-03-16", due_before="2026-03-20"

# Unscheduled tasks (no due date set):
status="active", has_due_date=false

# Only tasks that have a due date (any date):
status="active", has_due_date=true

# High-priority tasks due this week:
status="active", due_before="2026-03-20", priority="high"

# Work tasks due before end of month:
status="active", due_before="2026-03-31", tag="work"

# Completed tasks from the last 14 days:
status="completed", days=14

# All active tasks in a specific project:
status="active", project_id="63563f0c24f4f791814f9308"
```

> **Note:** `due_before` and `due_after` use your configured `TICKTICK_TIMEZONE` for the date comparison, so "due before March 16" means before the end of March 16 in your local timezone, and "due after March 16" means starting at the beginning of March 16.

### Projects & Folders

#### Projects

```python
async with TickTickClient.from_settings() as client:
    # List all projects
    projects = await client.get_all_projects()
    for project in projects:
        print(f"{project.name} ({project.id})")

    # Get project with all its tasks
    project_data = await client.get_project_tasks(project_id="...")
    print(f"Project: {project_data.project.name}")
    print(f"Tasks: {len(project_data.tasks)}")

    # Create a project
    project = await client.create_project(
        name="Q1 Goals",
        color="#4A90D9",
        view_mode="kanban",  # list, kanban, timeline
    )

    # Update a project
    await client.update_project(
        project_id=project.id,
        name="Q1 Goals 2025",
        color="#FF5500",
    )

    # Delete a project
    await client.delete_project(project_id="...")
```

#### Folders (Project Groups)

```python
async with TickTickClient.from_settings() as client:
    # List all folders
    folders = await client.get_all_folders()

    # Create a folder
    folder = await client.create_folder(name="Work Projects")

    # Create project in folder
    project = await client.create_project(
        name="Client A",
        folder_id=folder.id,
    )

    # Rename a folder
    await client.rename_folder(folder_id=folder.id, name="Work")

    # Delete a folder
    await client.delete_folder(folder_id="...")
```

### Tags

Tags in TickTick support hierarchy (parent-child relationships) and custom colors.

```python
async with TickTickClient.from_settings() as client:
    # List all tags
    tags = await client.get_all_tags()
    for tag in tags:
        print(f"{tag.label} ({tag.name}) - {tag.color}")

    # Create a tag
    tag = await client.create_tag(
        name="urgent",
        color="#FF0000",
    )

    # Create nested tag
    child_tag = await client.create_tag(
        name="critical",
        parent="urgent",  # Parent tag name
    )

    # Rename a tag
    await client.rename_tag(old_name="urgent", new_name="priority")

    # Update tag color or parent
    await client.update_tag(
        name="priority",
        color="#FF5500",
    )

    # Merge tags (move all tasks from source to target)
    await client.merge_tags(source="old-tag", target="new-tag")

    # Delete a tag
    await client.delete_tag(name="obsolete")
```

### Habits

TickTick habits are recurring activities you want to track daily.

#### Habit Types

| Type | Description | Example |
|------|-------------|---------|
| `Boolean` | Simple yes/no | "Did you exercise today?" |
| `Real` | Numeric counter | "How many pages did you read?" |

#### Creating and Managing Habits

```python
async with TickTickClient.from_settings() as client:
    # List all habits
    habits = await client.get_all_habits()

    # Boolean habit (yes/no)
    exercise = await client.create_habit(
        name="Exercise",
        color="#4A90D9",
        reminders=["07:00", "19:00"],
        target_days=30,
        encouragement="Stay strong!",
    )

    # Numeric habit
    reading = await client.create_habit(
        name="Read",
        habit_type="Real",
        goal=30,           # 30 pages per day
        step=5,            # +5 button increment
        unit="Pages",
    )

    # Check in a habit (today)
    habit = await client.checkin_habit("habit_id")
    print(f"Streak: {habit.current_streak} days!")

    # Check in for a past date (backdate)
    from datetime import date
    habit = await client.checkin_habit("habit_id", checkin_date=date(2025, 12, 15))

    # Archive/unarchive
    await client.archive_habit("habit_id")
    await client.unarchive_habit("habit_id")
```

#### Habit Repeat Rules (RRULE Format)

| Schedule | RRULE |
|----------|-------|
| Daily (every day) | `RRULE:FREQ=WEEKLY;BYDAY=SU,MO,TU,WE,TH,FR,SA` |
| Weekdays only | `RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` |
| Weekends only | `RRULE:FREQ=WEEKLY;BYDAY=SA,SU` |
| X times per week | `RRULE:FREQ=WEEKLY;TT_TIMES=5` |
| Specific days | `RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR` |

### Focus/Pomodoro

```python
from datetime import date, timedelta

async with TickTickClient.from_settings() as client:
    # Focus heatmap (like GitHub contribution graph)
    heatmap = await client.get_focus_heatmap(
        start_date=date.today() - timedelta(days=90),
        end_date=date.today(),
    )

    # Focus time by tag
    by_tag = await client.get_focus_by_tag(days=30)
    for tag, seconds in sorted(by_tag.items(), key=lambda x: -x[1]):
        hours = seconds / 3600
        print(f"  {tag}: {hours:.1f} hours")
```

### User & Statistics

```python
async with TickTickClient.from_settings() as client:
    # User profile
    profile = await client.get_profile()
    print(f"Username: {profile.username}")

    # Account status
    status = await client.get_status()
    print(f"Pro User: {status.is_pro}")
    print(f"Inbox ID: {status.inbox_id}")

    # Productivity statistics
    stats = await client.get_statistics()
    print(f"Level: {stats.level}")
    print(f"Score: {stats.score}")
    print(f"Tasks completed today: {stats.today_completed}")
```

### Error Handling

```python
from ticktick_sdk import (
    TickTickClient,
    TickTickError,
    TickTickNotFoundError,
    TickTickAuthenticationError,
    TickTickRateLimitError,
    TickTickValidationError,
)

async with TickTickClient.from_settings() as client:
    try:
        task = await client.get_task("nonexistent-id")
    except TickTickNotFoundError as e:
        print(f"Task not found: {e}")
    except TickTickAuthenticationError:
        print("Authentication failed - check credentials")
    except TickTickRateLimitError:
        print("Rate limited - wait and retry")
    except TickTickValidationError as e:
        print(f"Invalid input: {e}")
    except TickTickError as e:
        print(f"TickTick error: {e}")
```
