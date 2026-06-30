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

from datetime import datetime, timezone

import pytest

from ticktick_sdk.models import Task
from ticktick_sdk.tools.formatting import (
    format_task_json,
    format_task_markdown,
    format_task_row_markdown,
    paginate_json,
    paginate_markdown,
    paginate_tasks_json,
    paginate_tasks_markdown,
    task_sort_key,
)

UTC = timezone.utc


def _tasks(n: int, **kw) -> list[Task]:
    """n minimal tasks with stable, unique ids."""
    return [Task(id=f"{i:024x}", project_id="p", title=f"Task {i}", **kw) for i in range(n)]


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


class TestLimitTotalNextOffset:
    """Regression for the search/list pagination bug.

    A small `limit` must NOT shrink `total` or null out `next_offset`. This
    mirrors the live repro where `search "Daily brief", limit=5` falsely
    returned `total: 5, next_offset: null` while 47 tasks actually matched.
    These assertions are RED against the pre-fix code (which pre-sliced the
    list to `offset + limit` before counting) and GREEN after.
    """

    def test_json_total_independent_of_small_limit(self):
        page = paginate_tasks_json(_tasks(47), offset=0, limit=5, budget=100_000)
        assert page["total"] == 47          # pre-fix: 5
        assert page["count"] == 5
        assert page["next_offset"] == 5     # pre-fix: None

    def test_json_large_limit_returns_everything(self):
        page = paginate_tasks_json(_tasks(47), offset=0, limit=50, budget=100_000)
        assert page["total"] == 47
        assert page["count"] == 47
        assert page["next_offset"] is None

    def test_json_budget_can_cap_below_limit_but_total_honest(self):
        # Tiny budget: fewer than `limit` fit, yet total + next_offset stay true.
        page = paginate_tasks_json(_tasks(47), offset=0, limit=50, budget=1500)
        assert page["total"] == 47
        assert 0 < page["count"] < 50
        assert page["next_offset"] == page["count"]

    def test_json_paging_walks_every_item_once(self):
        seen, offset, guard = [], 0, 0
        while offset is not None and guard < 100:
            page = paginate_tasks_json(_tasks(47), offset=offset, limit=5, budget=100_000)
            seen.extend(t["id"] for t in page["tasks"])
            offset = page["next_offset"]
            guard += 1
        assert len(seen) == 47
        assert len(set(seen)) == 47          # no duplicates, no gaps

    def test_limit_none_pages_by_budget_only(self):
        page = paginate_tasks_json(_tasks(47), offset=0, limit=None, budget=100_000)
        assert page["total"] == 47
        assert page["count"] == 47

    def test_markdown_total_independent_of_small_limit(self):
        out = paginate_tasks_markdown(_tasks(47), title="X", offset=0, limit=5, budget=100_000)
        assert "Showing tasks 1 to 5 of 47 total" in out  # pre-fix: "Found 5 tasks"
        assert "–" not in out           # plain "to", no en-dash
        assert "offset=5" in out             # footer points to the next page


class TestEmptyPageGuard:
    """A single item larger than the budget must still be emitted (one per
    page) so paging advances instead of stalling with next_offset == offset."""

    def test_json_oversized_items_advance_one_per_page(self):
        big = [{"k": "x" * 500} for _ in range(3)]
        offsets, offset, guard = [], 0, 0
        page = None
        while offset is not None and guard < 10:
            page = paginate_json(big, offset=offset, format_item=lambda x: x,
                                 budget=200, item_key="things")
            assert page["count"] == 1            # never zero
            assert page["next_offset"] != offset  # always advances
            offsets.append(offset)
            offset = page["next_offset"]
            guard += 1
        assert offsets == [0, 1, 2]
        assert page["total"] == 3

    def test_markdown_oversized_row_still_rendered(self):
        giant = "y" * 50_000
        out = paginate_markdown([giant], title="T", offset=0,
                                format_item=lambda x: x, budget=200)
        assert giant in out


class TestTaskSortKey:
    """`task_sort_key` ordering: newest-first default plus the other modes,
    with missing dates always sorting last."""

    def _t(self, id, *, created=None, modified=None, due=None, priority=0, title=""):
        return Task(id=id, project_id="p", title=title, created_time=created,
                    modified_time=modified, due_date=due, priority=priority)

    def test_created_desc_newest_first(self):
        a = self._t("a", created=datetime(2026, 1, 1, tzinfo=UTC))
        b = self._t("b", created=datetime(2026, 6, 1, tzinfo=UTC))
        c = self._t("c", created=datetime(2026, 3, 1, tzinfo=UTC))
        out = sorted([a, b, c], key=task_sort_key("created_desc"))
        assert [t.id for t in out] == ["b", "c", "a"]

    def test_created_asc_oldest_first(self):
        a = self._t("a", created=datetime(2026, 1, 1, tzinfo=UTC))
        b = self._t("b", created=datetime(2026, 6, 1, tzinfo=UTC))
        c = self._t("c", created=datetime(2026, 3, 1, tzinfo=UTC))
        out = sorted([a, b, c], key=task_sort_key("created_asc"))
        assert [t.id for t in out] == ["a", "c", "b"]

    def test_due_desc_and_asc(self):
        a = self._t("a", due=datetime(2026, 1, 1, tzinfo=UTC))
        b = self._t("b", due=datetime(2026, 9, 1, tzinfo=UTC))
        assert [t.id for t in sorted([a, b], key=task_sort_key("due_desc"))] == ["b", "a"]
        assert [t.id for t in sorted([a, b], key=task_sort_key("due_asc"))] == ["a", "b"]

    def test_priority_desc(self):
        tasks = [self._t("none", priority=0), self._t("high", priority=5),
                 self._t("med", priority=3), self._t("low", priority=1)]
        out = sorted(tasks, key=task_sort_key("priority_desc"))
        assert [t.id for t in out] == ["high", "med", "low", "none"]

    def test_title_asc(self):
        tasks = [self._t("c", title="Charlie"), self._t("a", title="alpha"),
                 self._t("b", title="Bravo")]
        out = sorted(tasks, key=task_sort_key("title_asc"))
        assert [t.id for t in out] == ["a", "b", "c"]  # case-insensitive

    def test_missing_dates_sort_last_in_both_directions(self):
        have = self._t("have", created=datetime(2026, 1, 1, tzinfo=UTC))
        missing = self._t("missing", created=None)
        assert [t.id for t in sorted([missing, have], key=task_sort_key("created_desc"))] == ["have", "missing"]
        assert [t.id for t in sorted([missing, have], key=task_sort_key("created_asc"))] == ["have", "missing"]

    def test_unknown_sort_defaults_to_created_desc(self):
        a = self._t("a", created=datetime(2026, 1, 1, tzinfo=UTC))
        b = self._t("b", created=datetime(2026, 6, 1, tzinfo=UTC))
        out = sorted([a, b], key=task_sort_key("bogus_value"))
        assert [t.id for t in out] == ["b", "a"]

    def test_accepts_enum_member(self):
        from ticktick_sdk.tools.inputs import TaskSort
        a = self._t("a", created=datetime(2026, 1, 1, tzinfo=UTC))
        b = self._t("b", created=datetime(2026, 6, 1, tzinfo=UTC))
        out = sorted([a, b], key=task_sort_key(TaskSort.CREATED_ASC))
        assert [t.id for t in out] == ["a", "b"]
