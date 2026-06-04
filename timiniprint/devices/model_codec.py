from __future__ import annotations

from collections.abc import Mapping as MappingABC, Sequence as SequenceABC
from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from types import UnionType
from typing import Any, TypeVar, Union, get_args, get_origin, get_type_hints

T = TypeVar("T")
_UNION_ORIGINS = {Union, UnionType}


def model_from_json(model_type: type[T], payload: object, *, path: str = "$") -> T:
    """Build a dataclass/enum model from JSON-shaped data."""
    return _convert_value(model_type, payload, path=path)


def model_to_json(value: object) -> Any:
    """Serialize model objects into JSON-shaped data."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: model_to_json(getattr(value, field.name))
            for field in fields(value)
            if field.init
        }
    if isinstance(value, tuple):
        return [model_to_json(item) for item in value]
    if isinstance(value, list):
        return [model_to_json(item) for item in value]
    if isinstance(value, MappingABC):
        return {str(key): model_to_json(item) for key, item in value.items()}
    return value


def _convert_value(target_type: Any, value: object, *, path: str) -> Any:
    origin = get_origin(target_type)
    args = get_args(target_type)

    if origin in _UNION_ORIGINS:
        if value is None and type(None) in args:
            return None
        errors: list[str] = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _convert_value(option, value, path=path)
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
        raise ValueError(f"{path} does not match any allowed type: {'; '.join(errors)}")

    if value is None:
        raise ValueError(f"{path} must not be null")

    if isinstance(target_type, type) and is_dataclass(target_type):
        return _convert_dataclass(target_type, value, path=path)

    if isinstance(target_type, type) and issubclass(target_type, Enum):
        try:
            return target_type(value)
        except ValueError as exc:
            raise ValueError(f"{path} has unsupported {target_type.__name__} value {value!r}") from exc

    if origin is tuple:
        if not isinstance(value, SequenceABC) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError(f"{path} must be an array")
        item_type = args[0] if args else Any
        return tuple(
            _convert_value(item_type, item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )

    if origin is list:
        if not isinstance(value, SequenceABC) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError(f"{path} must be an array")
        item_type = args[0] if args else Any
        return [
            _convert_value(item_type, item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]

    if target_type is Any:
        return value
    if target_type is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{path} must be a boolean")
        return value
    if target_type is int:
        if isinstance(value, bool):
            raise ValueError(f"{path} must be an integer")
        if not isinstance(value, int):
            raise ValueError(f"{path} must be an integer")
        return value
    if target_type is str:
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
        return value

    return value


def _convert_dataclass(model_type: type[T], payload: object, *, path: str) -> T:
    if not isinstance(payload, MappingABC):
        raise ValueError(f"{path} must be an object")
    type_hints = get_type_hints(model_type)
    model_fields = [field for field in fields(model_type) if field.init]
    field_names = {field.name for field in model_fields}
    unknown = sorted(set(payload) - field_names)
    if unknown:
        raise ValueError(
            f"{path} has unknown {model_type.__name__} field(s): "
            + ", ".join(str(key) for key in unknown)
        )

    kwargs: dict[str, object] = {}
    for field in model_fields:
        if field.name not in payload:
            if field.default is not MISSING or field.default_factory is not MISSING:
                continue
            raise ValueError(f"{path}.{field.name} is required")
        kwargs[field.name] = _convert_value(
            type_hints[field.name],
            payload[field.name],
            path=f"{path}.{field.name}",
        )
    return model_type(**kwargs)
