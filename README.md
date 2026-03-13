# TickTick Remote MCP Server

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A remote [MCP](https://modelcontextprotocol.io/) (Model Context Protocol) server for [TickTick](https://ticktick.com), designed to run on [Railway](https://railway.app) so you can use it from **Claude.ai**, **Claude Mobile** (iOS/Android), and any MCP-compatible client — no local setup needed.

Forked from [dev-mirzabicer/ticktick-sdk](https://github.com/dev-mirzabicer/ticktick-sdk). Includes full support for [Dida365 (滴答清单)](https://dida365.com).

## Table of Contents

- [Quick Start (Deploy to Railway)](#quick-start-deploy-to-railway)
- [Connect to Claude](#connect-to-claude)
- [Features](#features)
- [Available MCP Tools (43 Total)](#available-mcp-tools-43-total)
- [Example Conversations](#example-conversations)
- [Getting Your Credentials](#getting-your-credentials)
- [Environment Variables](#environment-variables)
- [Health Check & Monitoring](#health-check--monitoring)
- [Architecture](#architecture)
- [TickTick API Quirks](#important-ticktick-api-quirks)
- [Python Library Reference](#python-library-reference)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Quick Start (Deploy to Railway)

### 1. Get Your TickTick Credentials

You need 5 things from TickTick (see [Getting Your Credentials](#getting-your-credentials) for details):

| Credential | Where to get it |
|-----------|----------------|
| `TICKTICK_CLIENT_ID` | [TickTick Developer Portal](https://developer.ticktick.com/manage) → Create App |
| `TICKTICK_CLIENT_SECRET` | Same as above |
| `TICKTICK_ACCESS_TOKEN` | Run the OAuth2 flow (see below) |
| `TICKTICK_USERNAME` | Your TickTick email |
| `TICKTICK_PASSWORD` | Your TickTick password |

### 2. Deploy to Railway

1. **Fork this repo** on GitHub
2. **Create a Railway account** at [railway.app](https://railway.app)
3. **Create a new project** → **Deploy from GitHub repo** → select your fork
4. **Add environment variables** in Railway's dashboard (the 5 credentials above)
5. **Generate a public domain** in Settings → Networking → Public Networking → "Generate Domain"
6. **Set the healthcheck path** in Settings → Deploy → Healthcheck Path → `/health`
7. **Wait for deployment** to finish (green status)
8. **Note your URL** — something like `https://your-app-production.up.railway.app`

### 3. Connect to Claude

See [Connect to Claude](#connect-to-claude) below.

---

## Connect to Claude

### Claude.ai (Web)

1. Go to **claude.ai** → **Settings** → **Connectors**
2. Click **"Add custom connector"**
3. Enter a name (e.g., "ticktick")
4. Enter URL: `https://your-app-production.up.railway.app/mcp`
5. Click **"Add"**

### Claude Mobile (iOS/Android)

Once added via claude.ai, the connector automatically appears in the Claude mobile app. You cannot add new connectors from mobile — only use ones already configured on the web.

### Claude Desktop / Claude Code (Local Alternative)

If you want to run the server locally instead, see the [original repo](https://github.com/dev-mirzabicer/ticktick-sdk) which supports stdio transport for local MCP usage.

---

## Features

- **43 MCP Tools**: Tasks, projects, folders, kanban columns, tags, habits, focus, user analytics
- **Batch Operations**: All mutations accept lists (1-100 items) for bulk operations
- **Remote Access**: Runs as an HTTP server with streamable-http transport
- **Health Check**: `/health` endpoint for deployment platform monitoring
- **Dual Output**: Markdown for humans, JSON for machines
- **Dida365 Support**: Works with both ticktick.com and dida365.com

---

## Available MCP Tools (43 Total)

All mutation tools accept lists for batch operations (1-100 items).

### Task Tools (Batch-Capable)
| Tool | Description |
|------|-------------|
| `ticktick_create_tasks` | Create 1-50 tasks with titles, dates, tags, etc. |
| `ticktick_get_task` | Get task details by ID |
| `ticktick_list_tasks` | List tasks (active/completed/abandoned/deleted via status filter; supports `due_before` for date-range filtering) |
| `ticktick_update_tasks` | Update 1-100 tasks (includes column assignment) |
| `ticktick_complete_tasks` | Complete 1-100 tasks |
| `ticktick_delete_tasks` | Delete 1-100 tasks (moves to trash) |
| `ticktick_move_tasks` | Move 1-50 tasks between projects |
| `ticktick_set_task_parents` | Set parent-child relationships for 1-50 tasks |
| `ticktick_unparent_tasks` | Remove parent relationships from 1-50 tasks |
| `ticktick_search_tasks` | Search tasks by text |
| `ticktick_pin_tasks` | Pin or unpin 1-100 tasks |

### Project Tools
| Tool | Description |
|------|-------------|
| `ticktick_list_projects` | List all projects |
| `ticktick_get_project` | Get project details with tasks |
| `ticktick_create_project` | Create a new project |
| `ticktick_update_project` | Update project properties |
| `ticktick_delete_project` | Delete a project |

### Folder Tools
| Tool | Description |
|------|-------------|
| `ticktick_list_folders` | List all folders |
| `ticktick_create_folder` | Create a folder |
| `ticktick_rename_folder` | Rename a folder |
| `ticktick_delete_folder` | Delete a folder |

### Kanban Column Tools
| Tool | Description |
|------|-------------|
| `ticktick_list_columns` | List columns for a kanban project |
| `ticktick_create_column` | Create a kanban column |
| `ticktick_update_column` | Update column name or order |
| `ticktick_delete_column` | Delete a kanban column |

### Tag Tools
| Tool | Description |
|------|-------------|
| `ticktick_list_tags` | List all tags |
| `ticktick_create_tag` | Create a tag with color |
| `ticktick_update_tag` | Update tag properties (includes rename via label) |
| `ticktick_delete_tag` | Delete a tag |
| `ticktick_merge_tags` | Merge two tags |

### Habit Tools (Batch-Capable)
| Tool | Description |
|------|-------------|
| `ticktick_habits` | List all habits |
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

## Getting Your Credentials

### Step 1: Register Your App at TickTick

1. Go to the [TickTick Developer Portal](https://developer.ticktick.com/manage)
2. Click **"Create App"**
3. Fill in:
   - **App Name**: e.g., "My TickTick MCP"
   - **Redirect URI**: `http://127.0.0.1:8080/callback`
4. Save your **Client ID** and **Client Secret**

### Step 2: Get OAuth2 Access Token

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

---

## Environment Variables

| Variable | Required | Description |
|----------|:--------:|-------------|
| `TICKTICK_CLIENT_ID` | Yes | OAuth2 client ID from developer portal |
| `TICKTICK_CLIENT_SECRET` | Yes | OAuth2 client secret |
| `TICKTICK_ACCESS_TOKEN` | Yes | OAuth2 access token (from auth command) |
| `TICKTICK_USERNAME` | Yes | Your TickTick email |
| `TICKTICK_PASSWORD` | Yes | Your TickTick password |
| `TICKTICK_HOST` | No | API host: `ticktick.com` (default) or `dida365.com` (Chinese) |
| `TICKTICK_TIMEOUT` | No | Request timeout in seconds (default: `30`) |
| `TICKTICK_TIMEZONE` | No | Your local timezone for correct date display (default: `UTC`). Set to your timezone, e.g. `Europe/Brussels`, `America/New_York`, `Asia/Tokyo`. Without this, all-day tasks may show the wrong date. |
| `TICKTICK_DEVICE_ID` | No | Device ID for V2 API (auto-generated) |
| `MCP_BEARER_TOKEN` | No | Bearer token for server authentication (see note) |
| `PORT` | No | Server port (default: `8000`, Railway sets this automatically) |

> **Note on MCP_BEARER_TOKEN**: Claude.ai's custom connector UI does not currently support bearer token auth. If you set this variable, requests without the correct `Authorization: Bearer <token>` header will be rejected. Leave it unset for Claude.ai compatibility.

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

## Architecture

This server combines TickTick's two different APIs:

| API | Type | What It Handles |
|-----|------|----------------|
| **V1 (OAuth2)** | Official, documented | Project with all tasks, basic operations |
| **V2 (Session)** | Unofficial, reverse-engineered | Tags, folders, habits, focus, subtasks, and more |

```
┌─────────────────────────────────────────────────────────────┐
│              Claude.ai / Claude Mobile / MCP Client          │
└─────────────────────────┬───────────────────────────────────┘
                          │ streamable-http
┌─────────────────────────▼───────────────────────────────────┐
│              FastMCP Server (Railway)                         │
│              43 tools, /health endpoint                      │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                    TickTickClient                             │
│            Unified async API layer                           │
└─────────────────────────┬───────────────────────────────────┘
                          │
           ┌──────────────┴──────────────┐
           ▼                             ▼
┌──────────────────────┐      ┌──────────────────────┐
│      V1 API          │      │      V2 API          │
│     (OAuth2)         │      │     (Session)        │
│ • Project with tasks │      │ • Tags, folders      │
│ • Limited features   │      │ • Habits, focus      │
│                      │      │ • Full subtasks      │
└──────────────────────┘      └──────────────────────┘
```

---

## Important: TickTick API Quirks

### 1. Recurrence Requires start_date
If you create a recurring task without a start_date, TickTick **silently ignores** the recurrence rule.

### 2. Subtasks Require Separate Call
Setting `parent_id` during task creation is **ignored** by the API. Use the `ticktick_set_task_parents` tool after creating the task.

### 3. Soft Delete
Deleting tasks moves them to trash rather than permanently removing them.

### 4. Date Clearing
To clear a task's `due_date`, you must also clear `start_date` (TickTick restores due_date from start_date otherwise).

### 5. Tag Order Not Preserved
The API does not preserve tag order — tags may be returned in any order.

### 6. Inbox is Special
The inbox is a special project that cannot be deleted.

### 7. Timezone: All-Day Tasks
TickTick stores all-day task dates as midnight in the user's timezone, expressed as UTC. For example, a task due March 14 in Brussels (CET, UTC+1) is stored as `2026-03-13T23:00:00+00:00`. Without the `TICKTICK_TIMEZONE` setting, these dates display one day early. Set `TICKTICK_TIMEZONE=Europe/Brussels` (or your timezone) to fix this.

---

## Python Library Reference

This repo also includes a full async Python SDK. For library usage examples (tasks, projects, tags, habits, focus, etc.), see the [original repo's documentation](https://github.com/dev-mirzabicer/ticktick-sdk#python-library-setup--usage).

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
