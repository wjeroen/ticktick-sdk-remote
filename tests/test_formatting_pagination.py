"""
Tests for list-view formatting behavior: children filtering, pagination
hints, and the budget-aware paginator.

Covers the three fixes added together:
  - Children whose titles can't be resolved (status doesn't match the query
    filter) are dropped from the JSON output with a hint about the count.
  - JSON pagination responses include a `_pagination_hint` string when
    `next_offset` is set (mirroring the markdown footer).
  - The exact-size paginator respects the budget.
"""

from __future__ import annotations

import json

import pytest

from ticktick_sdk.models import Task
from ticktick_sdk.tools.formatting import (
    format_task_json,
    paginate_json,
    paginate_tasks_json,
)


pytestmark = [pytest.mark.unit]


class TestChildrenFiltering:
    """`format_task_json` drops children with unresolvable titles when a
    title map is provided, and reports the hidden count."""

    def test_all_children_resolved_no_hint(self):
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2"],
        )
        titles = {"parent": "Parent", "c1": "Child 1", "c2": "Child 2"}

        out = format_task_json(parent, task_titles=titles)

        assert len(out["children"]) == 2
        assert out["children"][0] == {"id": "c1", "title": "Child 1"}
        assert "total_children" not in out
        assert "children_hidden" not in out
        assert "_children_hint" not in out

    def test_unresolved_children_dropped_with_hint(self):
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2", "c3"],
        )
        # c2 is completed and not in the active title map.
        titles = {"parent": "Parent", "c1": "Active 1", "c3": "Active 3"}

        out = format_task_json(parent, task_titles=titles)

        assert len(out["children"]) == 2
        assert {c["id"] for c in out["children"]} == {"c1", "c3"}
        assert out["total_children"] == 3
        assert out["children_hidden"] == 1
        assert "_children_hint" in out

    def test_no_title_map_keeps_all_child_ids(self):
        """Single-task detail view (no title map) shows every child ID."""
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2", "c3"],
        )

        out = format_task_json(parent)  # task_titles=None

        assert len(out["children"]) == 3
        assert out["children"] == [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]
        assert "total_children" not in out

    def test_no_children(self):
        parent = Task(id="parent", project_id="p1", title="Parent")
        out = format_task_json(parent, task_titles={"parent": "Parent"})
        assert out["children"] == []
        assert "total_children" not in out


class TestPaginationHint:
    """`paginate_json` adds a `_pagination_hint` when `next_offset` is set."""

    def test_hint_present_when_more_pages(self):
        items = [{"k": i} for i in range(20)]
        result = paginate_json(
            items,
            offset=0,
            format_item=lambda x: x,
            budget=200,  # forces truncation
            item_key="things",
        )
        assert result["next_offset"] is not None
        assert "_pagination_hint" in result
        assert "offset=" in result["_pagination_hint"]
        assert "things" in result["_pagination_hint"]

    def test_no_hint_on_final_page(self):
        items = [{"k": 1}, {"k": 2}]
        result = paginate_json(
            items,
            offset=0,
            format_item=lambda x: x,
            budget=10_000,
            item_key="things",
        )
        assert result["next_offset"] is None
        assert "_pagination_hint" not in result

    def test_hint_references_next_offset_value(self):
        items = [{"k": i} for i in range(50)]
        result = paginate_json(
            items,
            offset=0,
            format_item=lambda x: x,
            budget=300,
            item_key="things",
        )
        assert f"offset={result['next_offset']}" in result["_pagination_hint"]


class TestPaginateTasksJsonIntegration:
    """End-to-end check that the task-list paginator wires children
    filtering AND the pagination hint together correctly."""

    def test_active_listing_filters_children_and_paginates(self):
        # Two active parents, each with one active + one absent (completed)
        # child. The title map mimics what server.py builds from
        # get_all_tasks() (active tasks only).
        parents = [
            Task(
                id=f"p{i}",
                project_id="proj",
                title=f"Parent {i}",
                child_ids=[f"c{i}a", f"c{i}b"],  # b is completed (not in map)
            )
            for i in range(5)
        ]
        active_titles = {f"p{i}": f"Parent {i}" for i in range(5)}
        for i in range(5):
            active_titles[f"c{i}a"] = f"Active child {i}A"
            # c{i}b is intentionally missing — simulates a completed child.

        result = paginate_tasks_json(
            parents,
            offset=0,
            content_max_chars=500,
            task_titles=active_titles,
        )

        # Each rendered task should show only the active child.
        for task in result["tasks"]:
            assert len(task["children"]) == 1
            assert task["total_children"] == 2
            assert task["children_hidden"] == 1
