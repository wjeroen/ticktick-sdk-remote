"""
Tests for list-view formatting behavior: children rendering with titles +
priority, pagination hints, and the budget-aware paginator.

Covers:
  - JSON children get `{id, title, priority_label}` from a child_meta map.
  - Markdown list rows append children as indented sub-bullets with
    `[PRIORITY] title (id)`.
  - Markdown detail view shows children with priority + title when meta
    is provided, falls back to bare IDs otherwise.
  - Children whose meta can't be resolved (status mismatch with the
    query filter) are dropped, with counts surfaced.
  - JSON pagination responses include a `_pagination_hint` when
    `next_offset` is set.
"""

from __future__ import annotations

import json

import pytest

from ticktick_sdk.models import Task
from ticktick_sdk.tools.formatting import (
    format_task_json,
    format_task_markdown,
    format_task_row_markdown,
    paginate_json,
    paginate_tasks_json,
    paginate_tasks_markdown,
)


pytestmark = [pytest.mark.unit]


# Helper for tests
def _meta(**children) -> dict[str, dict]:
    """Build a child_meta dict from kwargs like c1=("Title", 5)."""
    return {cid: {"title": title, "priority": prio} for cid, (title, prio) in children.items()}


class TestChildrenJsonRendering:
    """JSON children format: {id, title, priority_label}."""

    def test_resolved_children_show_priority_label(self):
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2"],
        )
        meta = _meta(c1=("Active 1", 5), c2=("Active 2", 3))

        out = format_task_json(parent, child_meta=meta)

        assert out["children"] == [
            {"id": "c1", "title": "Active 1", "priority_label": "High"},
            {"id": "c2", "title": "Active 2", "priority_label": "Medium"},
        ]
        assert "total_children" not in out

    def test_unresolved_children_dropped_with_hint(self):
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2", "c3"],
        )
        meta = _meta(c1=("Active 1", 5), c3=("Active 3", 1))

        out = format_task_json(parent, child_meta=meta)

        assert {c["id"] for c in out["children"]} == {"c1", "c3"}
        assert out["total_children"] == 3
        assert out["children_hidden"] == 1
        assert "_children_hint" in out

    def test_no_meta_falls_back_to_bare_ids(self):
        """Detail view without a meta map shows child IDs only."""
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2"],
        )

        out = format_task_json(parent)  # child_meta=None

        assert out["children"] == [{"id": "c1"}, {"id": "c2"}]


class TestChildrenMarkdownListRow:
    """Markdown list row appends children as indented sub-bullets."""

    def test_children_rendered_as_sub_bullets(self):
        parent = Task(
            id="parent",
            project_id="p1",
            title="Pay rent",
            priority=5,
            child_ids=["c1", "c2"],
        )
        meta = _meta(c1=("Pay landlord", 5), c2=("Pay utilities", 0))

        out = format_task_row_markdown(parent, child_meta=meta)

        lines = out.split("\n")
        assert lines[0].startswith("- [HIGH]")
        assert "Pay rent" in lines[0]
        assert "| 2 children" not in lines[0]  # suffix is replaced
        assert lines[1] == "  - [HIGH] Pay landlord (`c1`)"
        assert lines[2] == "  - [NONE] Pay utilities (`c2`)"

    def test_count_only_when_no_meta(self):
        """Without child_meta, falls back to plain `| N children` suffix."""
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2", "c3"],
        )

        out = format_task_row_markdown(parent)
        assert "\n" not in out
        assert "| 3 children" in out

    def test_all_hidden_shown_as_suffix(self):
        """When every child is filtered out, single-line row with hidden count."""
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2"],
        )

        out = format_task_row_markdown(parent, child_meta={})  # empty map = all hidden

        assert "\n" not in out
        assert "2 subtasks (not in this filter)" in out

    def test_partial_hidden_appended_to_visible(self):
        """When some children resolve and some don't, show the visible ones
        plus a 'N more hidden' suffix."""
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2", "c3"],
        )
        meta = _meta(c1=("Visible", 3))

        out = format_task_row_markdown(parent, child_meta=meta)

        assert "2 more subtasks hidden" in out
        assert "[MEDIUM] Visible (`c1`)" in out

    def test_no_children_no_extra_lines(self):
        parent = Task(id="parent", project_id="p1", title="Solo")
        out = format_task_row_markdown(parent, child_meta={})
        assert "\n" not in out


class TestChildrenMarkdownDetailView:
    """Markdown detail view shows children with priority + title when meta provided."""

    def test_detail_children_enriched(self):
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            priority=3,
            child_ids=["c1", "c2"],
        )
        meta = _meta(c1=("First sub", 5), c2=("Second sub", 1))

        out = format_task_markdown(parent, child_meta=meta)

        assert "- **Children**:" in out
        assert "  - [HIGH] First sub (`c1`)" in out
        assert "  - [LOW] Second sub (`c2`)" in out

    def test_detail_falls_back_to_bare_id_when_meta_missing(self):
        """If a child fetch failed (not in meta), the row shows just the ID."""
        parent = Task(
            id="parent",
            project_id="p1",
            title="Parent",
            child_ids=["c1", "c2"],
        )
        meta = _meta(c1=("Got it", 5))  # c2 missing — fetch failed

        out = format_task_markdown(parent, child_meta=meta)

        assert "  - [HIGH] Got it (`c1`)" in out
        assert "  - `c2`" in out  # bare ID fallback


class TestPaginationHint:
    """`paginate_json` adds a `_pagination_hint` when `next_offset` is set."""

    def test_hint_present_when_more_pages(self):
        items = [{"k": i} for i in range(20)]
        result = paginate_json(items, offset=0, format_item=lambda x: x,
                               budget=200, item_key="things")
        assert result["next_offset"] is not None
        assert "_pagination_hint" in result

    def test_no_hint_on_final_page(self):
        items = [{"k": 1}, {"k": 2}]
        result = paginate_json(items, offset=0, format_item=lambda x: x,
                               budget=10_000, item_key="things")
        assert result["next_offset"] is None
        assert "_pagination_hint" not in result


class TestPaginateTasksIntegration:
    """End-to-end: list paginators correctly thread child_meta through."""

    def test_json_listing_enriches_children(self):
        parents = [
            Task(id=f"p{i}", project_id="proj", title=f"Parent {i}",
                 child_ids=[f"c{i}a", f"c{i}b"])
            for i in range(3)
        ]
        meta = {}
        for i in range(3):
            meta[f"c{i}a"] = {"title": f"Child {i}A", "priority": 5}
            # c{i}b intentionally missing — simulates completed children.

        result = paginate_tasks_json(parents, offset=0, content_max_chars=500,
                                     child_meta=meta)

        for task in result["tasks"]:
            assert len(task["children"]) == 1
            assert task["children"][0]["priority_label"] == "High"
            assert task["total_children"] == 2
            assert task["children_hidden"] == 1

    def test_markdown_listing_renders_nested_bullets(self):
        parents = [
            Task(id="p0", project_id="proj", title="Pay rent",
                 priority=5, child_ids=["c1"]),
        ]
        meta = {"c1": {"title": "Pay landlord", "priority": 5}}

        result = paginate_tasks_markdown(parents, title="Tasks", offset=0,
                                         child_meta=meta)

        assert "[HIGH] Pay landlord (`c1`)" in result
        assert "Pay rent" in result
