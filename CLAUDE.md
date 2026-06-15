# Claude Code Instructions

## TL;DR
The user is a coding noob. ELI5 (Explain Like I'm 5) frequently when discussing technical concepts, code changes, and tradeoffs. Always make sure to update the README.md and TODO.md when making changes. Always consider at least 3 possible causes when something isn't working.

## How to Ask the User Questions
**Do NOT use the AskUserQuestion poll/multiple-choice UI — the user dislikes it.** When you need to clarify requirements or have the user choose between approaches, ask in **regular chat text** instead. Lay out the options and tradeoffs in prose (ELI5) and let the user reply normally. Asking questions is encouraged — just do it conversationally, not through the poll widget.

## Pull Requests
**Do NOT subscribe to / watch PRs for activity.** The user does not want PR-activity subscriptions (CI babysitting, auto-fixing failures, watching review comments). Create the PR and keep its description current, but never call `subscribe_pr_activity` and don't offer to watch the PR — unless the user explicitly asks you to in a specific case.

## User's Working Environment
The user primarily works on **mobile** and deploys via Railway, so they **do not have access to browser developer tools**. When debugging:
- Add `print()` or `logging` statements to the Python code — these show up in Railway logs
- Railway logs are the primary debugging tool

**Live TickTick MCP tools (great for testing):** Claude Code sessions — including remote/web sessions — usually have this project's own TickTick MCP connected as live tools (`mcp__TickTick__*`). You can call them directly to test changes end-to-end against a real TickTick account, no extra setup needed. **Caveat:** those tools run against whatever is *deployed*, and the branch you're working on may not be set up for deployment yet. Once a branch is set up it often **autodeploys** on push — but the user may also turn autodeploy off — so don't assume your in-progress branch code is what the live tools are executing. Check with the user what's actually deployed before trusting the live tools to verify your branch's changes.

## After Every Prompt, Before Making Any Changes
Ex. When Solving a Bug or Implementing a Feature

**CRITICAL WORKFLOW - FOLLOW THIS ORDER:**

1. **Check README.md Quick Reference first** (lines 44-59)
   - This table tells you EXACTLY which file handles what functionality
   - **DO NOT GREP until you've checked this table**

2. **Read the relevant service/component descriptions in README.md**
   - This gives you the BIG PICTURE before diving into code

3. **Only THEN read the actual files**
   - Now that you know where to look, read the specific files IN THEIR ENTIRETY, not just a few lines

4. **Generate at least 3 possible hypotheses and approaches** based on what you read
5. **Briefly explain the tradeoffs** of each (ELI5)
6. **Ask the user which approach they prefer** before writing code

**Why this order matters:** Grepping without context leads to local fixes that miss the big picture and create new bugs. README.md is your map - use it!

## Flag Sloppy Code (Don't Silently Fix It)

While working, if you come across code that looks messy, buggy, sloppy, or just off — **even if it has nothing to do with the task you're currently on** — bring it to the user's attention. Briefly say what you noticed and why it looks wrong, and **offer** to fix or improve it. Do NOT silently fix unrelated code yourself; let the user decide. Any sloppiness you notice should be surfaced rather than ignored.

## After Making Changes
Update README.md if you changed:
   - File structure or added new files
   - Database schema
   - Environment variables
   - Processing flows
   - API endpoints

**Keep the open pull request's description current.** As you push commits that add or change features, refresh the PR description so it reflects the whole branch — don't leave it describing only the first commit. Treat it like the README and TODO.md: keeping it up to date is part of finishing a change.

## Task Management: TODO.md vs TodoWrite Tool

There are TWO different task tracking systems. **Use the right one for the job:**

### TODO.md (Project-Level Tasks)
**This is the PRIMARY task list** for development. It persists across all sessions and gives the user visibility into project progress.

- **File location**: `/home/user/ticktick-sdk-remote/TODO.md`
- **Scope**: Project-wide tasks, bugs, features, roadmap
- **Persistence**: Survives across all Claude sessions (it's a file in the repo)
- **When to use**: When tracking work that relates to the overall project
- **How to update**: Edit the TODO.md file directly using the Read/Edit tools

### TodoWrite Tool (Session-Level Tasks)
A **temporary** task tracker for the current conversation only.

- **Scope**: Breaking down work within THIS conversation/session
- **Persistence**: Only lasts for this conversation (disappears after session ends)
- **When to use**: Planning multi-step work within a single session (e.g., "I need to do X, Y, Z in this session")
- **How to update**: Use the TodoWrite tool

**IMPORTANT:** When doing project work, always check and update TODO.md first. The TodoWrite tool is just for organizing your thoughts within a conversation.

### When to Update TODO.md

**Always update TODO.md when:**
1. **Starting a new task** - Mark it as in progress (change `[ ]` to current task)
2. **Completing a task** - Mark it done by changing `[ ]` to `[x]`
3. **Discovering new work** - Add new tasks to the appropriate section
4. **Encountering bugs** - Add them to "Bug Fixes" section
5. **Planning features** - Add to "Features to Implement" or "Future Ideas"

### Structure of TODO.md

- **Current Sprint**: Active work organized by priority
  - High Priority - Urgent tasks
  - Features to Implement - New functionality
  - Bug Fixes - Things that are broken
  - Performance & Optimization - Speed/efficiency improvements
  - Documentation - Guides, API docs, troubleshooting
  - Testing - Things to verify/test
- **Completed Recently**: Recent wins with dates (keep last ~10-15 items)
- **Future Ideas**: Nice-to-have features for later
- **Reference**: Links to other documentation

### Task Format

Use checkbox format with clear, actionable descriptions:
```markdown
- [ ] Fix recurring task series dying when due date is moved via batch_update_tasks
- [ ] Add due_after filter to ticktick_list_tasks (combine with due_before for ranges)
```

When completed, add date:
```markdown
- [x] Fix timezone: all-day tasks no longer off by one day (2026-03-13)
```

### Best Practices

1. **Be specific**: "Fix priority labels not showing in markdown list view" not "Fix UI issues"
2. **Include context**: Add parenthetical notes for clarity
3. **Keep it fresh**: Move old completed items to archive periodically
4. **No duplicates**: If a task is already listed, don't add it again
5. **Link related docs**: Reference README.md for implementation details

### What NOT to Put in TODO.md

Don't clutter TODO.md with:
- Implementation details (those go in README.md or code comments)
- API specifications / endpoint details (those go in `docs/ARCHITECTURE.md` or the `docs/api-analysis/` notes)
- Architecture & design docs (those go in `docs/ARCHITECTURE.md` or the README)
- Long-term vision/roadmap (use "Future Ideas" sparingly)

TODO.md is for **actionable tasks**, not documentation.
