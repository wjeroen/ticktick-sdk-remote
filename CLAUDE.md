## Codebase map

This project's codebase map lives in **`docs/ARCHITECTURE.md` §2 ("Codebase map")**. Check it alongside `README.md` and `TODO.md` before making changes. It tells you EXACTLY which file handles what functionality.

Detailed API analysis notes live in `docs/api-analysis/`. This SDK has a V1 (OAuth) path and a V2 (session) path. See `docs/ARCHITECTURE.md` for how V1/V2 routing, auth, and the MCP server/formatting internals work, and update that file if you change any of them.

## Live TickTick MCP tools (great for testing)

Claude Code sessions, including remote/web sessions, usually have this project's own TickTick MCP connected as live tools (`mcp__TickTick__*`). You can call them directly to test changes end-to-end against a real TickTick account, no extra setup needed. **Caveat:** those tools run against whatever is _deployed_, and the branch you're working on may not be set up for deployment yet. Once a branch is set up it often **autodeploys** on push, but the user may also turn autodeploy off, so don't assume your in-progress branch code is what the live tools are executing. Check with the user what's actually deployed before trusting the live tools to verify your branch's changes.
