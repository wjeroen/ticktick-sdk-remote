"""
Regression tests for the reparent "silent-success trap".

V2's ``set_parent`` endpoint silently "succeeds" (returns an etag, no error)
when the child OR the parent task no longer exists. That means attaching a
subtask to a parent that was deleted out from under us *looks* like success but
does nothing — the child ends up orphaned. (This is what happened to the real
"BlueDot" subtasks: a duplicate parent was deleted by TickTick's app-side
de-duplication, and a later run cheerfully re-attached children to the dead ID.)

``UnifiedTickTickAPI.set_task_parent`` / ``.batch_set_task_parents`` guard
against this by verifying every referenced child AND parent exists first,
raising ``TickTickNotFoundError`` (a clean 404) when one is missing.

These tests pin that behavior against the REAL ``UnifiedTickTickAPI`` (wired to
a mocked V2 client), plus the public ``client.set_task_parents`` path through
the mock API.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ticktick_sdk.exceptions import TickTickNotFoundError
from ticktick_sdk.unified.api import UnifiedTickTickAPI
from ticktick_sdk.unified.router import APIRouter


pytestmark = [pytest.mark.unit, pytest.mark.tasks]


# =============================================================================
# Real UnifiedTickTickAPI wired to a mocked V2 client
# =============================================================================


def _make_api(existing_ids: set[str]) -> tuple[UnifiedTickTickAPI, MagicMock]:
    """Build a real ``UnifiedTickTickAPI`` wired to a mocked V2 client.

    The mocked V2 client's ``get_task`` raises ``TickTickNotFoundError`` for any
    id not in ``existing_ids`` (mirroring a real 404) and returns a minimal task
    dict otherwise. ``set_task_parent`` just records the call and returns an
    etag dict, so we can assert whether reparenting was actually attempted.
    """
    v2 = MagicMock()
    v2.is_authenticated = True  # so APIRouter.has_v2 is True

    async def _get_task(task_id: str):
        if task_id not in existing_ids:
            raise TickTickNotFoundError(f"Task not found: {task_id}")
        return {"id": task_id, "projectId": "proj"}

    v2.get_task = AsyncMock(side_effect=_get_task)
    v2.set_task_parent = AsyncMock(return_value={"id2etag": {}, "id2error": {}})

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
    )
    # Bypass network init: hand it the mocked V2 client directly.
    api._v2_client = v2
    api._router = APIRouter(v1_client=None, v2_client=v2)
    api._initialized = True
    return api, v2


class TestSetTaskParentValidation:
    """Singular ``UnifiedTickTickAPI.set_task_parent``."""

    async def test_raises_when_parent_missing(self):
        """The core trap: child exists, parent was deleted -> 404, no silent OK."""
        api, v2 = _make_api(existing_ids={"child"})

        with pytest.raises(TickTickNotFoundError) as exc:
            await api.set_task_parent("child", "proj", "ghost_parent")

        # Error names the *parent* so the caller knows which side vanished,
        # and we never actually attempted the reparent.
        assert "ghost_parent" in str(exc.value)
        v2.set_task_parent.assert_not_called()

    async def test_raises_when_child_missing(self):
        """A nonexistent child is just as much a silent-success trap."""
        api, v2 = _make_api(existing_ids={"parent"})

        with pytest.raises(TickTickNotFoundError):
            await api.set_task_parent("ghost_child", "proj", "parent")

        v2.set_task_parent.assert_not_called()

    async def test_succeeds_when_both_exist(self):
        """Happy path: both exist -> reparent is actually performed."""
        api, v2 = _make_api(existing_ids={"child", "parent"})

        await api.set_task_parent("child", "proj", "parent")

        v2.set_task_parent.assert_awaited_once_with("child", "proj", "parent")


class TestBatchSetTaskParentsValidation:
    """Batch ``UnifiedTickTickAPI.batch_set_task_parents``."""

    async def test_raises_when_a_parent_missing(self):
        """The BlueDot case: children exist, the shared parent is gone."""
        api, v2 = _make_api(existing_ids={"c1", "c2"})
        assignments = [
            {"task_id": "c1", "project_id": "proj", "parent_id": "ghost"},
            {"task_id": "c2", "project_id": "proj", "parent_id": "ghost"},
        ]

        with pytest.raises(TickTickNotFoundError) as exc:
            await api.batch_set_task_parents(assignments)

        assert "ghost" in str(exc.value)
        # Fail fast: nothing was reparented (no partial application).
        v2.set_task_parent.assert_not_called()

    async def test_raises_when_a_child_missing(self):
        api, v2 = _make_api(existing_ids={"c1", "parent"})
        assignments = [
            {"task_id": "c1", "project_id": "proj", "parent_id": "parent"},
            {"task_id": "ghost_child", "project_id": "proj", "parent_id": "parent"},
        ]

        with pytest.raises(TickTickNotFoundError):
            await api.batch_set_task_parents(assignments)

        v2.set_task_parent.assert_not_called()

    async def test_dedupes_shared_parent_existence_check(self):
        """3 children sharing 1 parent -> parent is fetched ONCE, not 3x."""
        api, v2 = _make_api(existing_ids={"c1", "c2", "c3", "parent"})
        assignments = [
            {"task_id": "c1", "project_id": "proj", "parent_id": "parent"},
            {"task_id": "c2", "project_id": "proj", "parent_id": "parent"},
            {"task_id": "c3", "project_id": "proj", "parent_id": "parent"},
        ]

        await api.batch_set_task_parents(assignments)

        fetched = [call.args[0] for call in v2.get_task.await_args_list]
        assert fetched.count("parent") == 1  # deduped
        assert set(fetched) == {"c1", "c2", "c3", "parent"}
        assert v2.set_task_parent.await_count == 3  # all three reparented

    async def test_succeeds_when_all_exist(self):
        api, v2 = _make_api(existing_ids={"c1", "parent"})
        assignments = [
            {"task_id": "c1", "project_id": "proj", "parent_id": "parent"},
        ]

        results = await api.batch_set_task_parents(assignments)

        assert len(results) == 1
        v2.set_task_parent.assert_awaited_once()

    async def test_empty_assignments_is_a_noop(self):
        api, v2 = _make_api(existing_ids=set())

        results = await api.batch_set_task_parents([])

        assert results == []
        v2.get_task.assert_not_called()
        v2.set_task_parent.assert_not_called()


# =============================================================================
# Public client path (through the mock API)
# =============================================================================


class TestClientReparentValidation:
    """``client.set_task_parents`` -> ``MockUnifiedAPI.batch_set_task_parents``."""

    async def test_set_task_parents_raises_on_missing_parent(self, client, mock_api):
        child = await client.create_task(title="child")

        with pytest.raises(TickTickNotFoundError):
            await client.set_task_parents([
                {
                    "task_id": child.id,
                    "project_id": child.project_id,
                    "parent_id": "deadparent",
                },
            ])

    async def test_set_task_parents_success(self, client, mock_api):
        parent = await client.create_task(title="parent")
        child = await client.create_task(title="child")

        await client.set_task_parents([
            {
                "task_id": child.id,
                "project_id": child.project_id,
                "parent_id": parent.id,
            },
        ])

        refreshed = await client.get_task(child.id)
        assert refreshed.parent_id == parent.id
