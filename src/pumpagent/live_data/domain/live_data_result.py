"""Live Data success/failure result contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pumpagent.live_data.domain.base import SerializableMixin
from pumpagent.live_data.domain.live_data_error import LiveDataError
from pumpagent.live_data.domain.normalized_market_data_input import (
    NormalizedMarketDataInput,
)


@dataclass(frozen=True)
class LiveDataResult(SerializableMixin):
    success: bool
    data: NormalizedMarketDataInput | None = None
    raw_data: Any | None = None
    error: LiveDataError | None = None

    def __post_init__(self) -> None:
        if self.success and self.data is None and self.raw_data is None:
            raise ValueError("Successful LiveDataResult requires data or raw_data.")

        if self.success and self.data is not None and self.raw_data is not None:
            raise ValueError("Successful LiveDataResult cannot include both data and raw_data.")

        if self.success and self.error is not None:
            raise ValueError("Successful LiveDataResult cannot include error.")

        if not self.success and self.error is None:
            raise ValueError("Failed LiveDataResult requires error.")

        if not self.success and self.data is not None:
            raise ValueError("Failed LiveDataResult cannot include data.")

        if not self.success and self.raw_data is not None:
            raise ValueError("Failed LiveDataResult cannot include raw_data.")
