"""Common contract for Live Data sources."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pumpagent.live_data.domain import LiveDataResult


@runtime_checkable
class LiveDataSource(Protocol):
    """Source boundary for loading normalized Live Data inputs.

    Sources return LiveDataResult only. They must not create Runtime objects,
    invoke Runtime modules, or perform market reasoning.
    """

    def load(self, *args: Any, **kwargs: Any) -> LiveDataResult:
        """Return normalized Live Data or a structured LiveDataError."""
