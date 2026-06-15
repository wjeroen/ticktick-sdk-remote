"""
API Router for TickTick Unified API.

``APIRouter`` bundles the V1 and V2 clients and exposes lightweight
availability/verification helpers (``has_v1`` / ``has_v2`` /
``is_fully_configured`` / ``verify_clients`` / ``get_status``).

These are what ``UnifiedTickTickAPI`` actually uses: routing is decided
*inline* in each ``unified/api.py`` method via ``if self._router.has_v2: ...
elif self._router.has_v1: ...``. There is intentionally no declarative
routing table here. An earlier ``OPERATION_ROUTING`` table plus
``get_routing`` / ``can_execute`` / ``get_primary_client`` /
``get_fallback_client`` helpers were never called and had drifted out of sync
with the real behavior (e.g. they implied a V1 fallback for task creation
that does not actually exist), so they were removed. If declarative routing
is wanted later, build it fresh against the real ``api.py`` behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ticktick_sdk.api.v1 import TickTickV1Client
    from ticktick_sdk.api.v2 import TickTickV2Client

logger = logging.getLogger(__name__)


@dataclass
class APIRouter:
    """
    Holds the V1 and V2 clients and reports their availability.

    Despite the name, this does not contain a routing table — each
    ``UnifiedTickTickAPI`` method decides V1 vs V2 inline using the
    ``has_v1`` / ``has_v2`` properties below.
    """

    v1_client: TickTickV1Client | None = None
    v2_client: TickTickV2Client | None = None

    # Cached state
    _v1_verified: bool = field(default=False, repr=False)
    _v2_verified: bool = field(default=False, repr=False)

    @property
    def has_v1(self) -> bool:
        """Check if V1 client is available and authenticated."""
        return self.v1_client is not None and self.v1_client.is_authenticated

    @property
    def has_v2(self) -> bool:
        """Check if V2 client is available and authenticated."""
        return self.v2_client is not None and self.v2_client.is_authenticated

    @property
    def is_fully_configured(self) -> bool:
        """Check if both APIs are available."""
        return self.has_v1 and self.has_v2

    async def verify_clients(self) -> dict[str, bool]:
        """
        Verify that both clients are working.

        Returns:
            Dict with 'v1' and 'v2' keys indicating verification status
        """
        results: dict[str, bool] = {"v1": False, "v2": False}

        if self.v1_client and self.v1_client.is_authenticated:
            try:
                results["v1"] = await self.v1_client.verify_authentication()
                self._v1_verified = results["v1"]
            except Exception as e:
                logger.warning("V1 verification failed: %s", e)
                self._v1_verified = False

        if self.v2_client and self.v2_client.is_authenticated:
            try:
                results["v2"] = await self.v2_client.verify_authentication()
                self._v2_verified = results["v2"]
            except Exception as e:
                logger.warning("V2 verification failed: %s", e)
                self._v2_verified = False

        return results

    def get_status(self) -> dict[str, Any]:
        """Get the current status of the router."""
        return {
            "v1_available": self.has_v1,
            "v2_available": self.has_v2,
            "v1_verified": self._v1_verified,
            "v2_verified": self._v2_verified,
            "fully_configured": self.is_fully_configured,
        }
