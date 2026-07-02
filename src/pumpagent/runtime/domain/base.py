"""Shared serialization helpers for runtime domain models."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class FrozenDict(Mapping):
    """Immutable mapping used for nested domain payloads."""

    def __init__(self, values: Mapping[Any, Any] | None = None) -> None:
        self._values = {
            key: freeze_value(item) for key, item in (values or {}).items()
        }

    def __getitem__(self, key: Any) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"FrozenDict({self._values!r})"


class SerializableMixin:
    """Mixin for converting immutable domain objects to plain primitives."""

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


def freeze_value(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, tuple):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, list):
        return tuple(freeze_value(item) for item in value)
    return value


def freeze_dataclass_fields(instance: Any) -> None:
    for field in fields(instance):
        value = getattr(instance, field.name)
        frozen = freeze_value(value)
        if frozen is not value:
            object.__setattr__(instance, field.name, frozen)


def to_primitive(value: Any) -> Any:
    if is_dataclass(value):
        return {
            field.name: to_primitive(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [to_primitive(item) for item in value]
    if isinstance(value, list):
        return [to_primitive(item) for item in value]
    return value
