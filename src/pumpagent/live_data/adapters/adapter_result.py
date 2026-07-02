"""Lightweight adapter acquisition result before normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pumpagent.live_data.adapters.adapter_errors import AdapterError
from pumpagent.live_data.domain.base import SerializableMixin, freeze_dataclass_fields


@dataclass(frozen=True)
class AdapterResult(SerializableMixin):
    """Raw adapter acquisition result.

    Public adapter source methods still return LiveDataResult. This contract is
    reserved for future internal acquisition steps before normalization.
    """

    success: bool
    raw_payload: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: AdapterError | None = None

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError("Successful AdapterResult cannot include error.")
        if not self.success and self.error is None:
            raise ValueError("Failed AdapterResult requires error.")
        if not self.success and self.raw_payload is not None:
            raise ValueError("Failed AdapterResult cannot include raw_payload.")

        freeze_dataclass_fields(self)
