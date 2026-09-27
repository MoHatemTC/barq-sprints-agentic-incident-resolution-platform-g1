"""The lifecycle-aware manifest: reviewed mapping from manual sections to
publication units.

The manifest is the append-only source of truth for identity and lifecycle
decisions (plan step 5). It is *data*, reviewed by a named human KB owner;
this module owns its schema, preflight validation, and the shared KB2xxx
allocation policy the publisher later reuses:

- every extracted section is covered by exactly one unit, and a section's
  content hash pins the reviewed extraction — drift forces an explicit
  manifest update, never a silent one;
- ``alias`` units mirror existing corpus articles (metadata is read from the
  corpus at adaptation time, never copied into the manifest);
- restricted content cannot be downgraded, and retired instructions can only
  live in historical/reference/warning units that carry a warning;
- new numbers live in a bounded range (KB2001–KB2999, KB2000 unallocated),
  allocated once, never renumbered and never recycled.

Validation collects *all* violations before failing, so a review pass fixes
everything in one round.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.knowledge import (
    ARTICLE_NUMBER_PATTERN,
    SLUG_PATTERN,
    VERSION_PATTERN,
    Article,
    SecurityLevel,
    WorkflowState,
)
from app.models.knowledge_provenance import ContentPurpose
from app.models.manual_section import ManualSection


class ManifestPreflightError(ValueError):
    """All preflight violations found in one pass; ``errors`` lists each one."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("manifest preflight failed:\n- " + "\n- ".join(errors))


class UnitKind(StrEnum):
    """How a unit relates to the article corpus.

    ``alias`` mirrors an existing corpus article at a pinned version (Section
    6's KB reproductions). ``index_alias`` lists existing articles without
    producing one. ``new`` builds an article from section text (or, when
    platform-authored, from the manifest's own body).
    """

    ALIAS = "alias"
    INDEX_ALIAS = "index_alias"
    NEW = "new"


class ManifestSource(BaseModel):
    """The exact source edition the manifest was reviewed against."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    pdf_path: str
    pdf_sha256: str
    sections_artifact: str


class IdentityPolicy(BaseModel):
    """The bounded KB2xxx allocation policy, shared with the publisher."""

    model_config = ConfigDict(extra="forbid")

    new_range_min: int = Field(..., ge=0, le=9999)
    new_range_max: int = Field(..., ge=0, le=9999)
    never_allocated: list[str] = Field(default_factory=list)
    deallocated: list[str] = Field(
        default_factory=list,
        description="Numbers burned by removed units; never reissued by the allocator",
    )
    rule: str

    def check_new_number(self, article_number: str) -> None:
        """Guard one number claimed as a new allocation; raises ``ValueError``."""
        if not ARTICLE_NUMBER_PATTERN.match(article_number):
            raise ValueError(f"{article_number!r} is not a KB article number")
        if article_number in self.never_allocated:
            raise ValueError(f"{article_number} is never allocatable (explicitly unallocated)")
        if article_number in self.deallocated:
            raise ValueError(
                f"{article_number} was deallocated and can never be reissued (append-only identity)"
            )
        serial = int(article_number[2:])
        if not self.new_range_min <= serial <= self.new_range_max:
            raise ValueError(
                f"{article_number} is outside the new-article range "
                f"KB{self.new_range_min:04d}-KB{self.new_range_max:04d}"
            )


class Vocabulary(BaseModel):
    """Declared category/service slugs; undeclared values fail preflight."""

    model_config = ConfigDict(extra="forbid")

    categories: dict[str, str]
    services: dict[str, str]

    def check(self, category: str, service: str) -> list[str]:
        errors = []
        for label, value, declared in (
            ("category", category, self.categories),
            ("service", service, self.services),
        ):
            if not SLUG_PATTERN.match(value):
                errors.append(f"{label} {value!r} is not a lowercase slug")
            elif value not in declared:
                errors.append(f"{label} {value!r} is not declared in the manifest vocabulary")
        return errors


class ManifestUnit(BaseModel):
    """One publication decision.

    Exactly what each field means depends on ``kind``; ``validate_manifest``
    enforces which combinations are legal (aliases may not copy metadata,
    new units must declare full lifecycle metadata, and so on).
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(..., description="Stable logical identity; append-only key")
    source_sections: tuple[str, ...] = Field(
        default=(),
        description="Section numbers this unit covers; empty only when platform-authored",
    )
    grounded_in_sections: tuple[str, ...] = Field(
        default=(), description="Sections grounding a platform-authored unit's text"
    )
    kind: UnitKind
    article_number: str | None = None
    version: str | None = None
    alias_targets: tuple[str, ...] = Field(
        default=(), description="index_alias only: the existing articles this unit indexes"
    )
    content_purpose: ContentPurpose
    content_sha256: str | None = Field(
        default=None,
        description=(
            "SHA-256 of the covered sections' bodies (or of ``body`` when platform-authored)"
        ),
    )
    body: str | None = Field(
        default=None,
        description="Platform-authored units only; the reviewed article body",
    )
    title: str | None = Field(
        default=None,
        description="Title override when the section title cannot satisfy the contract",
    )
    short_description: str | None = None
    category: str | None = None
    service: str | None = None
    workflow_state: WorkflowState | None = None
    security_level: SecurityLevel | None = None
    derives_from_articles: tuple[str, ...] = Field(
        default=(),
        description="KB numbers or versioned unique keys whose content this unit reproduces",
    )
    supersedes: tuple[str, ...] = Field(default_factory=tuple)
    warning: str | None = None
    platform_authored: bool = False

    @property
    def unique_key(self) -> str:
        return f"{self.article_number}-v{self.version}"

    @field_validator("article_number")
    @classmethod
    def _article_number_shape(cls, value: str | None) -> str | None:
        if value is not None and not ARTICLE_NUMBER_PATTERN.match(value):
            raise ValueError(f"article_number must match KB\\d{{4}}, got {value!r}")
        return value

    @field_validator("version")
    @classmethod
    def _version_shape(cls, value: str | None) -> str | None:
        if value is not None and not VERSION_PATTERN.match(value):
            raise ValueError(f"version must match \\d+\\.\\d+, got {value!r}")
        return value


class ManualKBManifest(BaseModel):
    """Schema of ``data/corpus/manual_kb_manifest.json``."""

    model_config = ConfigDict(extra="forbid")

    manifest_version: str
    review_status: str | None = None
    source: ManifestSource
    identity_policy: IdentityPolicy
    vocabulary: Vocabulary
    units: list[ManifestUnit] = Field(..., min_length=1)


def parse_manifest(raw: dict) -> ManualKBManifest:
    """Parse and schema-check a raw manifest document."""
    return ManualKBManifest.model_validate(raw)


def load_manifest(path: Path) -> ManualKBManifest:
    """Load and schema-check the manifest file."""
    return parse_manifest(json.loads(path.read_text(encoding="utf-8")))


def sections_content_hash(sections: list[ManualSection]) -> str:
    """Hash the covered bodies, joined in the unit's declared order."""
    joined = "\n\n".join(section.body for section in sections)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _derivation_targets(
    refs: tuple[str, ...],
    corpus_by_key: dict[str, Article],
    corpus_by_number: dict[str, list[Article]],
) -> list[str]:
    """Expand derivation refs to corpus unique keys: ``KB0010`` -> every version.

    Bare numbers mean "content drawn from this article without version
    certainty", so every version counts (retired and restricted checks are
    conservative). Pinned unique keys mean exactly one version.
    """
    keys: list[str] = []
    for ref in refs:
        if "-v" in ref:
            if ref not in corpus_by_key:
                raise KeyError(ref)
            keys.append(ref)
        else:
            if ref not in corpus_by_number:
                raise KeyError(ref)
            keys.extend(article.unique_key for article in corpus_by_number[ref])
    return keys


def validate_manifest(
    manifest: ManualKBManifest,
    sections: list[ManualSection],
    corpus: list[Article],
) -> None:
    """Preflight the manifest against the current extraction and corpus.

    Raises ``ManifestPreflightError`` listing every violation; an empty error
    list means the manifest is safe to adapt and publish from.
    """
    errors: list[str] = []
    policy = manifest.identity_policy

    # --- identity sanity ----------------------------------------------------
    seen_units: set[str] = set()
    seen_numbers: dict[str, str] = {}
    for unit in manifest.units:
        if unit.unit_id in seen_units:
            errors.append(f"duplicate unit_id {unit.unit_id}")
        seen_units.add(unit.unit_id)
        if unit.article_number:
            if unit.article_number in seen_numbers:
                errors.append(
                    f"{unit.article_number} is claimed by both {seen_numbers[unit.article_number]} "
                    f"and {unit.unit_id}"
                )
            seen_numbers[unit.article_number] = unit.unit_id
        if unit.kind is UnitKind.NEW and unit.article_number:
            try:
                policy.check_new_number(unit.article_number)
            except ValueError as exc:
                errors.append(f"{unit.unit_id}: {exc}")

    # --- coverage and content drift ------------------------------------------
    by_number: dict[str, ManualSection] = {}
    for section in sections:
        if section.section_number in by_number:
            errors.append(f"extraction contains duplicate section number {section.section_number}")
        by_number[section.section_number] = section

    covered: dict[str, str] = {}
    for unit in manifest.units:
        for number in unit.source_sections:
            if number not in by_number:
                errors.append(f"{unit.unit_id}: source section {number} is not in the extraction")
                continue
            if number in covered:
                errors.append(
                    f"section {number} is covered by both {covered[number]} and {unit.unit_id}"
                )
            covered[number] = unit.unit_id
        present = [n for n in unit.source_sections if n in by_number]
        if unit.source_sections:
            actual = sections_content_hash([by_number[n] for n in present])
            if unit.content_sha256 != actual:
                errors.append(
                    f"{unit.unit_id}: source content changed since review "
                    "(content_sha256 mismatch); update the manifest explicitly"
                )
        elif unit.platform_authored:
            if not unit.body:
                errors.append(f"{unit.unit_id}: platform-authored unit needs a body")
            elif unit.content_sha256 != hashlib.sha256(unit.body.encode("utf-8")).hexdigest():
                errors.append(
                    f"{unit.unit_id}: platform-authored body changed since review "
                    "(content_sha256 mismatch); update the manifest explicitly"
                )
        else:
            errors.append(f"{unit.unit_id}: unit covers no section and is not platform-authored")
    for number in by_number:
        if number not in covered:
            errors.append(f"section {number} is not covered by any manifest unit")

    # --- kind-specific structure ---------------------------------------------
    for unit in manifest.units:
        uid = unit.unit_id
        if unit.kind in (UnitKind.ALIAS, UnitKind.INDEX_ALIAS):
            copied = [
                field
                for field in (
                    "category",
                    "service",
                    "workflow_state",
                    "security_level",
                    "title",
                    "short_description",
                )
                if getattr(unit, field) is not None
            ]
            if copied:
                errors.append(
                    f"{uid}: {unit.kind.value} units mirror corpus metadata; remove copied "
                    f"{', '.join(copied)}"
                )
        if unit.kind is UnitKind.ALIAS:
            if not unit.article_number or not unit.version:
                errors.append(f"{uid}: alias needs article_number and version")
            if unit.alias_targets:
                errors.append(f"{uid}: alias units use article_number, not alias_targets")
        elif unit.kind is UnitKind.INDEX_ALIAS:
            if unit.article_number:
                errors.append(f"{uid}: index_alias must not claim an article number")
            if not unit.alias_targets:
                errors.append(f"{uid}: index_alias must list alias_targets")
            if unit.source_sections and unit.version:
                errors.append(f"{uid}: index_alias does not pin a version")
        else:
            missing_meta = [
                field
                for field in ("category", "service", "workflow_state", "security_level")
                if getattr(unit, field) is None
            ]
            if missing_meta:
                errors.append(f"{uid}: new unit lacks {', '.join(missing_meta)}")
            if not unit.version:
                errors.append(f"{uid}: new unit lacks a version")
            if unit.category and unit.service:
                errors.extend(
                    f"{uid}: {e}" for e in manifest.vocabulary.check(unit.category, unit.service)
                )
            if unit.warning is not None and not unit.warning.strip():
                errors.append(f"{uid}: warning, when set, must not be empty")

    # --- alias resolution against the corpus ----------------------------------
    corpus_by_key = {article.unique_key: article for article in corpus}
    corpus_by_number: dict[str, list[Article]] = {}
    for article in corpus:
        corpus_by_number.setdefault(article.article_number, []).append(article)

    for unit in manifest.units:
        if unit.kind is UnitKind.NEW and unit.article_number in corpus_by_number:
            existing = corpus_by_number[unit.article_number][0].unique_key
            errors.append(
                f"{unit.unit_id}: new unit claims {unit.article_number} but the supplied corpus "
                f"already holds {existing}; collision with an existing identity is rejected"
            )

    for unit in manifest.units:
        if unit.kind is UnitKind.ALIAS and unit.article_number and unit.version:
            target = corpus_by_key.get(unit.unique_key)
            if target is None:
                errors.append(
                    f"{unit.unit_id}: alias target {unit.unique_key} not found in the corpus"
                )
            elif target.workflow_state is not WorkflowState.PUBLISHED:
                errors.append(
                    f"{unit.unit_id}: alias target {unit.unique_key} is "
                    f"{target.workflow_state.value}; retired versions cannot be aliased "
                    "as current procedure"
                )
        if unit.kind is UnitKind.INDEX_ALIAS:
            for number in unit.alias_targets:
                versions = corpus_by_number.get(number, [])
                if not versions:
                    errors.append(f"{unit.unit_id}: alias target {number} not found in the corpus")
                elif not any(a.workflow_state is WorkflowState.PUBLISHED for a in versions):
                    errors.append(f"{unit.unit_id}: alias target {number} has no published version")

    # --- derivation precedence: security inheritance, retired handling ---------
    for unit in manifest.units:
        if unit.kind is not UnitKind.NEW or not unit.derives_from_articles:
            continue
        uid = unit.unit_id
        try:
            keys = _derivation_targets(unit.derives_from_articles, corpus_by_key, corpus_by_number)
        except KeyError as exc:
            errors.append(f"{uid}: derivation target {exc.args[0]} not found in the corpus")
            continue
        derived = [corpus_by_key[key] for key in keys]
        restricted = any(a.security_level is SecurityLevel.RESTRICTED for a in derived)
        if restricted and unit.security_level is not SecurityLevel.RESTRICTED:
            errors.append(
                f"{uid}: derives restricted content but is marked {unit.security_level}; "
                "restricted inheritance cannot be downgraded"
            )
        retired = any(a.workflow_state is WorkflowState.RETIRED for a in derived)
        if retired:
            if unit.content_purpose is ContentPurpose.CURRENT_PROCEDURE:
                errors.append(
                    f"{uid}: derives retired instructions but is marked current_procedure; "
                    "retired content may only feed historical/reference/warning units"
                )
            elif unit.content_purpose is not ContentPurpose.WARNING and not (
                unit.warning and unit.warning.strip()
            ):
                errors.append(
                    f"{uid}: narrative about retired instructions needs a warning attached"
                )

    # --- supersession references ----------------------------------------------
    for unit in manifest.units:
        for ref in unit.supersedes:
            if not ARTICLE_NUMBER_PATTERN.match(ref) and not (
                "-v" in ref and VERSION_PATTERN.match(ref.split("-v", 1)[1])
            ):
                errors.append(f"{unit.unit_id}: supersedes entry {ref!r} is not a KB identity")

    if errors:
        raise ManifestPreflightError(errors)


def assign_article_numbers(manifest: ManualKBManifest) -> ManualKBManifest:
    """One-time allocation for unassigned new units; never renumbers.

    Numbers append after the highest allocated number, so a unit inserted
    anywhere in the list gets the next free number and every prior identity
    stays put. Numbers recorded in ``identity_policy.deallocated`` (burned by
    removed units) are skipped forever — deleting the highest allocation never
    reissues it.
    """
    policy = manifest.identity_policy
    errors: list[str] = []
    allocated: set[str] = set()
    burned = set(policy.deallocated)
    highest = policy.new_range_min - 1
    for unit in manifest.units:
        if unit.kind is UnitKind.NEW and unit.article_number:
            try:
                policy.check_new_number(unit.article_number)
            except ValueError as exc:
                errors.append(f"{unit.unit_id}: {exc}")
            if unit.article_number in allocated:
                errors.append(
                    f"{unit.article_number} is claimed twice (allocation must stay unique)"
                )
            allocated.add(unit.article_number)
            highest = max(highest, int(unit.article_number[2:]))
    if errors:
        raise ManifestPreflightError(errors)

    units: list[ManifestUnit] = []
    for unit in manifest.units:
        if unit.kind is UnitKind.NEW and unit.article_number is None:
            number = None
            while number is None:
                highest += 1
                if highest > policy.new_range_max:
                    raise ManifestPreflightError(
                        [
                            f"KB{policy.new_range_min:04d}-KB{policy.new_range_max:04d} "
                            "range exhausted; no number available for new units"
                        ]
                    )
                candidate = f"KB{highest:04d}"
                if candidate not in burned:
                    number = candidate
            if number in allocated:
                raise ManifestPreflightError(
                    [f"{number} already allocated; allocation must never collide"]
                )
            allocated.add(number)
            unit = unit.model_copy(
                update={"article_number": number, "version": unit.version or "1.0"}
            )
        units.append(unit)
    return manifest.model_copy(update={"units": units})
