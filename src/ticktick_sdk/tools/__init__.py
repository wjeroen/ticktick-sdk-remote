"""
TickTick SDK Tools Package.

This package provides all MCP tool definitions for the TickTick SDK.
Tools are organized into logical groups:
    - Task tools (create, read, update, delete, complete, move, etc.)
    - Project tools (CRUD, get with data)
    - Tag tools (CRUD, rename, merge)
    - Habit tools (CRUD, checkin, archive)
    - User tools (profile, status, statistics)
    - Focus tools (heatmap, distribution)
    - Sync tools (full state sync)
"""

from ticktick_sdk.tools.inputs import (
    ResponseFormat,
    # Task inputs - list-based for batch operations
    TaskCreateItem,
    CreateTasksInput,
    TaskGetInput,
    TaskUpdateItem,
    UpdateTasksInput,
    TaskIdentifier,
    CompleteTasksInput,
    AbandonTasksInput,
    DeleteTasksInput,
    TaskMoveItem,
    MoveTasksInput,
    TaskParentItem,
    SetTaskParentsInput,
    TaskUnparentItem,
    UnparentTasksInput,
    TaskPinItem,
    PinTasksInput,
    TaskListInput,
    SearchInput,
    # Project inputs
    ProjectCreateInput,
    ProjectGetInput,
    ProjectDeleteInput,
    ProjectUpdateInput,
    # Folder inputs
    FolderCreateInput,
    FolderDeleteInput,
    FolderRenameInput,
    # Column inputs
    ColumnListInput,
    ColumnCreateInput,
    ColumnUpdateInput,
    ColumnDeleteInput,
    # Tag inputs
    TagCreateInput,
    TagDeleteInput,
    TagMergeInput,
    TagUpdateInput,
    # Focus inputs
    FocusStatsInput,
    # Habit inputs
    HabitListInput,
    HabitGetInput,
    HabitCreateInput,
    HabitUpdateInput,
    HabitDeleteInput,
    HabitCheckinItem,
    CheckinHabitsInput,
    HabitCheckinsInput,
)

__all__ = [
    "ResponseFormat",
    # Task inputs
    "TaskCreateItem",
    "CreateTasksInput",
    "TaskGetInput",
    "TaskUpdateItem",
    "UpdateTasksInput",
    "TaskIdentifier",
    "CompleteTasksInput",
    "AbandonTasksInput",
    "DeleteTasksInput",
    "TaskMoveItem",
    "MoveTasksInput",
    "TaskParentItem",
    "SetTaskParentsInput",
    "TaskUnparentItem",
    "UnparentTasksInput",
    "TaskPinItem",
    "PinTasksInput",
    "TaskListInput",
    "SearchInput",
    # Project inputs
    "ProjectCreateInput",
    "ProjectGetInput",
    "ProjectDeleteInput",
    "ProjectUpdateInput",
    # Folder inputs
    "FolderCreateInput",
    "FolderDeleteInput",
    "FolderRenameInput",
    # Column inputs
    "ColumnListInput",
    "ColumnCreateInput",
    "ColumnUpdateInput",
    "ColumnDeleteInput",
    # Tag inputs
    "TagCreateInput",
    "TagDeleteInput",
    "TagMergeInput",
    "TagUpdateInput",
    # Focus inputs
    "FocusStatsInput",
    # Habit inputs
    "HabitListInput",
    "HabitGetInput",
    "HabitCreateInput",
    "HabitUpdateInput",
    "HabitDeleteInput",
    "HabitCheckinItem",
    "CheckinHabitsInput",
    "HabitCheckinsInput",
]
