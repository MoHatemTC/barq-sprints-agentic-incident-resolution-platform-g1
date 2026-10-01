"""Resolve model citation aliases against the exact retrieved evidence only."""

from agent.state import EvidenceItem


def resolve_article_id(value: str, hits: list[EvidenceItem]) -> str | None:
    value = value.strip()
    exact = {hit.article_id for hit in hits if hit.article_id == value}
    if exact:
        return value
    # Base article numbers are safe only when they identify one retrieved version.
    candidates = {hit.article_id for hit in hits if hit.article_number == value}
    return next(iter(candidates)) if len(candidates) == 1 else None


def resolve_section(value: str, article_id: str, hits: list[EvidenceItem]) -> str | None:
    sections = {hit.section for hit in hits if hit.article_id == article_id}
    normalized = " ".join(value.split()).casefold()
    exact = {section for section in sections if " ".join(section.split()).casefold() == normalized}
    if len(exact) == 1:
        return next(iter(exact))
    aliases = {"resolution steps": "resolution", "remediation": "resolution"}
    target = aliases.get(normalized)
    candidates = {section for section in sections if section.casefold() == target}
    return next(iter(candidates)) if len(candidates) == 1 else None
