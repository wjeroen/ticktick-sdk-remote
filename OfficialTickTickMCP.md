# Crucial context

These are instructions for the [official TickTick MCP](https://help.ticktick.com/articles/7438129581631995904), NOT the instructions for our own remote TickTick MCP. I am adding this to our repo purely for inspiration, to compare notes, to see what we might be lacking.

# Official TickTick MCP

## [What Is MCP?](https://help.ticktick.com/articles/7438129581631995904#what-is-mcp%3F)

MCP (Model Context Protocol) is an open protocol that allows MCP-compatible AI clients, such as Claude, ChatGPT, Cursor, and VS Code, to interact directly with external services. Simply put, MCP allows AI to perform actions directly within your apps.

By connecting to TickTick MCP, your AI assistant can read, create, and manage your tasks directly, becoming your personal task manager. You do not need to switch between apps — you can manage your tasks right from the AI chat interface.

For example, you can say to your AI assistant:

- What tasks do I have today?
    
- Create a task for me: Prepare meeting materials tomorrow at 3:00 PM, high priority.
    
- Mark "Buy milk" as completed.
    

The AI will automatically call the appropriate TickTick MCP tools to complete the action.

## [Connect to TickTick MCP](https://help.ticktick.com/articles/7438129581631995904#connect-to-ticktick-mcp)

TickTick MCP supports the Streamable HTTP transport protocol and supports both OAuth and Bearer Token for authentication.

You can connect using the following URL:

```
https://mcp.ticktick.com
```

You can connect the TickTick MCP to different AI tools. Below are configuration instructions for common AI clients.

**How to get a Bearer Token?**

Log in to the TickTick web app, click your avatar in the top-left corner, and go to **Settings > Account > API Token** to create and copy a token.

### [Claude Desktop](https://help.ticktick.com/articles/7438129581631995904#claude-desktop)

> Available on Free, Pro, Max, Team, and Enterprise plans. Free users are limited to one custom connector.

1. Open Claude Desktop and navigate to **Customize** > **Connectors**.
    
2. Click "+" and select **Add Connector**.
    
3. Enter the MCP server URL: `https://mcp.ticktick.com`.
    
4. Click Connect after saving, then follow the on-screen prompts to complete OAuth sign-in and authorization.
    

### [ChatGPT](https://help.ticktick.com/articles/7438129581631995904#chatgpt)

> Available in beta to Plus, Pro, Business, Enterprise and Education accounts on the web.

1. Open ChatGPT and go to **Settings** > **Apps** > **Advanced Settings**.
    
2. Turn on **Developer Mode**.
    
3. Click **Create App**.
    
4. Enter the MCP server URL: `https://mcp.ticktick.com`
    
5. Save the configuration and follow the prompts to complete OAuth sign-in and authorization.
    

### [Claude Code](https://help.ticktick.com/articles/7438129581631995904#claude-code)

1. Run the following command in your terminal:

```
claude mcp add --transport http ticktick https://mcp.ticktick.com/
```

2. In your Claude Code session, run `/mcp` and follow the prompts to complete the OAuth authorization flow.
    
3. To use a Bearer Token instead, add the `--header` flag:
    

```
claude mcp add --transport http ticktick https://mcp.ticktick.com/ --header "Authorization: Bearer YOUR_TOKEN_HERE"
```

### [Cursor](https://help.ticktick.com/articles/7438129581631995904#cursor)

1. Open Cursor and go to **Cursor Settings**.
    
2. In the left sidebar, select **Tools & MCP**, then click **Add Custom MCP**.
    
3. Edit the `.cursor/mcp.json` file and add the following content:
    

```
{
  "mcpServers": {
    "ticktick": {
      "url": "https://mcp.ticktick.com"
    }
  }
}
```

4. Save the configuration, then reopen **Tools & MCP**.
    
5. Find TickTick in the installed MCP services list, click **Connect**, and complete OAuth sign-in and authorization in your browser.
    
6. To use a Bearer Token, add the `headers` field to the configuration instead:
    

```
{
  "mcpServers": {
    "ticktick": {
      "url": "https://mcp.ticktick.com",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}
```

### [VS Code](https://help.ticktick.com/articles/7438129581631995904#vs-code)

1. Create or edit the `.vscode/mcp.json` file in your workspace and add the following content:

```
{
 "servers": {
    "ticktick": {
      "type": "http",
      "url": "https://mcp.ticktick.com"
    }
  }
}
```

2. You can also open the Command Palette (Ctrl+Shift+P / Cmd+Shift+P), run **Add Server**, choose **HTTP (HTTP or Server-Sent Events)**, enter the MCP server URL `https://mcp.ticktick.com` and an ID, then choose whether to apply it to the workspace or globally.
    
3. Save the configuration and follow the prompts to complete OAuth sign-in and authorization in your browser.
    
4. To use a Bearer Token, add the `headers` field to the configuration instead:
    

```
{
  "servers": {
    "ticktick": {
      "type": "http",
      "url": "https://mcp.ticktick.com",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}
```

### [Codex](https://help.ticktick.com/articles/7438129581631995904#codex)

**Via the Codex App**

1. Open the Codex App and go to **Settings > MCP Servers**.
    
2. Click **Add Server**.
    
3. Select **Streamable HTTP**.
    
4. Enter the MCP server URL: `https://mcp.ticktick.com`
    
5. Click **Authenticate** after saving, then follow the on-screen prompts to complete OAuth sign-in and authorization.
    

**Via the CLI**

1. Run the following command in your terminal:

```
codex mcp add ticktick --url https://mcp.ticktick.com
```

2. You will be automatically prompted to complete OAuth sign-in and authorization in your browser.

### [TRAE](https://help.ticktick.com/articles/7438129581631995904#trae)

1. Open TRAE and go to **Settings**.
    
2. In the left sidebar, select **MCP**, then click **Add > Add Manually**.
    
3. Add the following configuration:
    

```
{
  "mcpServers": {
    "ticktick": {
      "url": "https://mcp.ticktick.com",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}
```

## [Available Tools](https://help.ticktick.com/articles/7438129581631995904#available-tools)

When you make task-related requests in an AI conversation, the AI will automatically call the tools provided by TickTick MCP to complete the operation. These tools act like a set of capabilities that allow the AI to query tasks, create tasks, or update task statuses.

> 💡 In most cases, you do not need to remember these tool names. Just describe what you want in natural language, and the AI will choose the appropriate tool automatically.

The following tools are provided by TickTick MCP:

## Task Queries

|**Tool**|**Description**|**Notes**|
|---|---|---|
|**search_task**|Search for tasks by keyword and return task IDs, titles, links, and more||
|**get_task_by_id**|Retrieve the full content of a task by task ID||
|**list_undone_tasks_by_time_query**|List undone tasks within a predefined time range; by default, it returns today's undone tasks|Supported values: today, last24hour, last7day, tomorrow, next24hour, next7day|
|**list_undone_tasks_by_date**|List undone tasks within a specified date range|Maximum date range: 14 days|
|**list_completed_tasks_by_date**|List completed tasks in a specified list within a date range||
|**filter_tasks**|Filter tasks by multiple conditions such as date, list, priority, tag, type, and status||

## List Management

|**Tool**|**Description**|**Notes**|
|---|---|---|
|**list_projects**|Get all lists in the current account||
|**create_project**|Create a new list||
|**update_project**|Update the settings of a list||
|**get_project_by_id**|Get detailed information for a specific list by list ID||
|**get_project_with_undone_tasks**|Get list details together with all undone tasks in that list||
|**get_task_in_project**|Get a specific task within a list||
|**list_columns**|Get all sections within a specified list||
|**create_column**|Create a new section within a specified list||
|**update_column**|Rename a section within a specified list||
|**list_project_groups**|Get all folders||
|**create_project_group**|Create a new folder||
|**update_project_group**|Rename a folder||
|**delete_project_group**|Dissolve a folder and ungroup all lists within it||

## Task Management

|**Tool**|**Description**|**Notes**|
|---|---|---|
|**create_task**|Create a task with properties such as title, description, date, priority, list, and tags||
|**batch_add_tasks**|Create multiple tasks in batch and set fields for each task||
|**complete_task**|Mark a specified task as completed||
|**complete_tasks_in_project**|Mark multiple tasks in a specified list as completed|Up to 20 tasks per request|
|**update_task**|Update task properties such as title, description, date, and priority||
|**move_task**|Move a task to another list||
|**batch_update_tasks**|Update properties for multiple tasks in batch||
|**delete_task**|Move a task to the Trash||
|**get_comment**|Get all comments on a specified task||
|**add_comment**|Add a comment to a specified task||
|**delete_comment**|Delete a specified comment||
|**list_tags**|Get all tags||
|**create_tag**|Create a new tag||

## Habit Management

|**Tool**|**Description**|**Notes**|
|---|---|---|
|**list_habits**|Get all habits in the current account||
|**list_habit_sections**|Get all habit sections in the current account||
|**create_habit**|Create a new habit||
|**update_habit**|Update the settings of a habit||
|**get_habit**|Get detailed information for a specific habit||
|**get_habit_checkins**|Get check-in records for a specified habit||
|**upsert_habit_checkins**|Create or update check-in records for a specified habit|Supports check-ins within the past 90 days|
|**batch_update_tasks**|Update properties for multiple tasks in batch||

## Focus Record Management

|**Tool**|**Description**|**Notes**|
|---|---|---|
|**get_focuses_by_time**|Query focus records by time range|Supports querying up to one month of focus records at a time|
|**get_focus**|Get a specific focus record||
|**create_focus**|Add a focus record||
|**delete_focus**|Delete a specific focus record||

## Countdown

| **Tool**            | **Description**    | **Notes** |
| ------------------- | ------------------ | --------- |
| **list_countdowns** | Get all countdowns |           |
## [How to Use](https://help.ticktick.com/articles/7438129581631995904#how-to-use)

Once the connection is complete, you can simply use natural language to make requests in your AI conversation. The AI will automatically call TickTick MCP capabilities based on your instructions to query, create, or update tasks.

Below are some example prompts to help you understand how you can interact with the AI.

### [Task Review and Search](https://help.ticktick.com/articles/7438129581631995904#task-review-and-search)

For example:

> "What tasks do I have today?"

> "List all my high-priority tasks for this week."

> "What tasks did I complete last week?"

> "What unfinished tasks are left in my exam prep list?"

The AI will return the relevant task information based on your request, making it easier to review your plans or reflect on your progress.

### [Task Creation and Management](https://help.ticktick.com/articles/7438129581631995904#task-creation-and-management)

For example:

> "Create a task: Finish the presentation deck, today at 3 PM, high priority."

> "Buy some plants today, visit the park this weekend to see flowers, and celebrate my friend's birthday next Tuesday. Add all of these to my Life list."

> "Complete the tasks 'Buy milk' and 'Buy daily essentials' in my Personal list."

> "Move 'Go camping' to my Outdoor Activities list."

The AI will automatically understand the task content, extract the relevant details, and perform the corresponding actions—such as creating tasks, updating task status, or organizing tasks into lists.

## [FAQ](https://help.ticktick.com/articles/7438129581631995904#faq)

**Can I connect if my client does not support Streamable HTTP?**

At the moment, TickTick MCP only supports the Streamable HTTP protocol and does not support SSE. If your client only supports SSE, it cannot connect directly for now.

**What if the action does not work as expected?**

There may be several reasons:

- The AI model may have misunderstood your request.
- The wrong tool may have been selected.
- The parameters passed to the tool may be incorrect.

You can try the following:

- Use a more specific description, such as the project name, exact date, or priority.
- Break a complex request into multiple steps, for example: first ask the AI to find the task, then confirm and perform the action.

**Does TickTick MCP support all TickTick features?**

At present, TickTick MCP mainly supports basic task, list, habit, focus records and countdown operations. Other advanced features are not supported yet.

**Do I need to sign in again every time I restart the Al client?**

No. OAuth authorization supports automatic token refresh, so under normal circumstances, you do not need to sign in again. You only need to reauthorize if the token has expired after a long period of inactivity or if you manually revoke access.
