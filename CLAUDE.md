## Codebase map

This project's codebase map lives in **`docs/ARCHITECTURE.md` §2 ("Codebase map")**. Check it alongside `README.md` and `TODO.md` before making changes. It tells you EXACTLY which file handles what functionality.

Detailed API analysis notes live in `docs/api-analysis/`. This SDK has a V1 (OAuth) path and a V2 (session) path. See `docs/ARCHITECTURE.md` for how V1/V2 routing, auth, and the MCP server/formatting internals work, and update that file if you change any of them.

## Live TickTick MCP tools (great for testing)

Claude Code sessions, including remote/web sessions, usually have this project's own TickTick MCP connected as live tools (`mcp__TickTick__*`). You can call them directly to test changes end-to-end against a real TickTick account, no extra setup needed. **Caveat:** those tools run against whatever is _deployed_, and the branch you're working on may not be set up for deployment yet. Once a branch is set up it often **autodeploys** on push, but the user may also turn autodeploy off, so don't assume your in-progress branch code is what the live tools are executing. Check with the user what's actually deployed before trusting the live tools to verify your branch's changes.

## Debugging TickTick V2 auth / Railway (lessons learned, read this FIRST when V2 is down)

When V2 tools fail (`search_tasks`, `list_tasks`, `get_status`, tags, folders, habits, focus, subtasks), don't guess. Work this checklist, it would have saved us two debugging sessions:

1. **Ask the user for the Railway logs immediately.** The user is on mobile with no devtools, so logs are the primary tool, and they ARE the fastest path. You want two kinds, name them explicitly: the **deploy/app logs** (the `ticktick_sdk.*` lines, sign-on attempts, 429/500s) and the **service config** (the railway.json block with `sleepApplication`, `numReplicas`, `restartPolicyType`). Don't theorize for long before you have these.
2. **Run `ticktick_auth_status` first.** It's the one tool that works in V1-only degraded mode (it's tagged `[API: diagnostic]`). Its verdict already distinguishes the cases below.
3. **`429` is NOT `401`.** A `429`/"rate limit" is a *throttle*: wait it out, do NOT refresh the cookie (it's probably fine). A `401`/"expired"/"stale" is a genuinely dead cookie: refresh `TICKTICK_V2_COOKIES`. The code and verdict classify these via `_is_rate_limit_error()`.
4. **Watch whether the error code MUTATES over time.** `need_captcha` → `username_password_not_match` → `429` across runs means TickTick's **anti-bot**, not your credentials. In particular `username_password_not_match` does **NOT** mean the password is wrong, the cookie working on the same account proves the account is fine. Password sign-on from a datacenter IP (Railway) is unreliable by design, the cookie is the real auth path.
5. **Repeated "Initializing TickTick MCP Server" with no new "Starting Container" = the lifespan runs per MCP session, not per process.** So the client is rebuilt and V2 auth re-runs on **every connection**. That multiplier turned a flaky login into a ban. (See `docs/ARCHITECTURE.md` §4; fix tracked in `TODO.md`.)
6. **`cooldown_until` is our own self-imposed timer (`now + 6h`), NOT TickTick's real throttle window.** Never quote it as "access returns at X". The real window is unknown.
7. **Railway facts:** the live `mcp__TickTick__*` tools run the **deployed** branch (confirm which branch + whether autodeploy is on before trusting them); every redeploy/restart re-runs startup (historically a sign-on); `sleepApplication: false` + `numReplicas: 1` means it is NOT sleeping or scaling to zero, so repeated re-inits are per-connection, not restarts.
