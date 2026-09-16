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


class MetadataFilterBuilder:
    category: StrOrList | None = None
    service: StrOrList | None = None
    worflow_state: list[WorkflowState]
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
    metadataFilterBuilder: MetadataFilterBuilder | None = None,
    extra: Filter | None = None,
) -> Filter:
    states = metadataFilterBuilder.worflow_state or [WorkflowState.PUBLISHED]
    if not states:
        raise ValueError("workflow_states must not be an empty list")

    mandatory: list[Condition] = [
        FieldCondition(
            key="workflow_state",
            match=MatchValue(value="published"),
        ),
        FieldCondition(
            key="security_level",
            match=MatchAny(any=_allowed_security_levels(metadataFilterBuilder.max_security_level)),
        ),
    ]

    if metadataFilterBuilder.category is not None:
        mandatory.append(_match_condition("category", metadataFilterBuilder.category))
    if metadataFilterBuilder.service is not None:
        mandatory.append(_match_condition("service", metadataFilterBuilder.service))
    if metadataFilterBuilder.version is not None:
        mandatory.append(_match_condition("version", metadataFilterBuilder.version))

    if extra is None:
        return Filter(must=mandatory)
    return Filter(must=[*mandatory, extra])
