from __future__ import annotations

from dataclasses import dataclass

from qdrant_client.models import Condition, FieldCondition, Filter, MatchAny, MatchValue

from app.models.knowledge import SecurityLevel, WorkflowState

SECURITY_LEVEL_ORDER: tuple[SecurityLevel, ...] = (
    SecurityLevel.PUBLIC,
    SecurityLevel.INTERNAL,
    SecurityLevel.RESTRICTED,
)

DEFAULT_MAX_SECURITY_LEVEL = SecurityLevel.INTERNAL
DEFAULT_WORKFLOW_STATES: tuple[WorkflowState, ...] = (WorkflowState.PUBLISHED,)


StrOrList = str | list[str] | None


@dataclass
class MetadataFilterBuilder:
    category: StrOrList | None = None
    service: StrOrList | None = None
    workflow_state: list[WorkflowState] | None = None
    version: StrOrList | None = None
    max_security_level: SecurityLevel = DEFAULT_MAX_SECURITY_LEVEL


def _allowed_security_levels(max_level: SecurityLevel) -> list[str]:
    """Every level at or below ``max_level``, as payload strings."""
    cutoff = SECURITY_LEVEL_ORDER.index(max_level)
    return [level.value for level in SECURITY_LEVEL_ORDER[: cutoff + 1]]


def _match_condition(key: str, value: str | list[str]) -> FieldCondition:
    """A single-value or multi-value equality condition on a keyword field."""
    if isinstance(value, list):
        if not value:
            raise ValueError(f"filter value list for {key!r} must not be empty")
        return FieldCondition(key=key, match=MatchAny(any=value))
    return FieldCondition(key=key, match=MatchValue(value=value))


def build_metadata_filter(
    metadata: MetadataFilterBuilder | None = None,
    extra: Filter | None = None,
) -> Filter:
    metadata = metadata or MetadataFilterBuilder()

    states = (
        metadata.workflow_state
        if metadata.workflow_state is not None
        else [WorkflowState.PUBLISHED]
    )
    if not states:
        raise ValueError("workflow_states must not be an empty list")

    mandatory: list[Condition] = [
        FieldCondition(
            key="workflow_state",
            match=MatchAny(any=[state.value for state in states]),
        ),
        FieldCondition(
            key="security_level",
            match=MatchAny(any=_allowed_security_levels(metadata.max_security_level)),
        ),
    ]

    if metadata.category is not None:
        mandatory.append(_match_condition("category", metadata.category))
    if metadata.service is not None:
        mandatory.append(_match_condition("service", metadata.service))
    if metadata.version is not None:
        mandatory.append(_match_condition("version", metadata.version))

    if extra is None:
        return Filter(must=mandatory)
    return Filter(must=[*mandatory, extra])
