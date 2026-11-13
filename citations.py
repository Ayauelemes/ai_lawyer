"""Exact lookup over a curated catalogue; no network calls or legal inference."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from hashlib import sha256
import re
from typing import Protocol, Sequence
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class Locator:
    act_id: str
    article: str
    language: str = "ru"
    part: str | None = None
    paragraph: str | None = None
    subparagraph: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z0-9_]+", self.act_id):
            raise ValueError("Invalid act identifier")
        if self.language not in {"ru", "kk"}:
            raise ValueError("Unsupported source language")
        for token in (self.article, self.part, self.paragraph, self.subparagraph):
            if token is not None and (not token or token != token.strip()):
                raise ValueError("Invalid locator component")
        if not self.article:
            raise ValueError("Article is required")


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    WITHDRAWN = "withdrawn"


def official_url_matches(url: str, locator: Locator) -> bool:
    """Check URL shape and act identity; approval must verify the actual snapshot."""
    if any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return False
    parsed = urlsplit(url)
    site_language = {"ru": "rus", "kk": "kaz"}[locator.language]
    return (
        parsed.scheme == "https"
        and parsed.netloc in {"adilet.zan.kz", "www.adilet.zan.kz", "old.adilet.zan.kz"}
        and parsed.path == f"/{site_language}/docs/{locator.act_id}"
        and not parsed.query
    )


@dataclass(frozen=True, slots=True)
class ProvisionRevision:
    revision_id: str
    locator: Locator
    act_title: str
    text: str
    effective_from: date
    effective_until: date | None
    source_url: str
    snapshot_sha256: str
    status: ReviewStatus = ReviewStatus.DRAFT
    checked_on: date | None = None
    reviewer_id: str | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.revision_id, self.act_title, self.text)):
            raise ValueError("Revision id, act title and provision text are required")
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("Invalid half-open effective interval")
        if not re.fullmatch(r"[0-9a-f]{64}", self.snapshot_sha256):
            raise ValueError("Invalid source snapshot digest")
        if not official_url_matches(self.source_url, self.locator):
            raise ValueError("URL does not match official act and language")
        if self.status is ReviewStatus.APPROVED and (
            self.checked_on is None or not (self.reviewer_id or "").strip()
        ):
            raise ValueError("Approval requires reviewer and review date")

    @property
    def text_sha256(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()

    def covers(self, event_on: date) -> bool:
        return self.effective_from <= event_on and (
            self.effective_until is None or event_on < self.effective_until
        )


class ProvisionCatalogue(Protocol):
    def revisions_for(self, locator: Locator) -> Sequence[ProvisionRevision]: ...


class CitationStatus(StrEnum):
    CATALOGUE_MATCH = "catalogue_match"
    EVENT_DATE_REQUIRED = "event_date_required"
    NOT_FOUND = "not_found"
    NO_EFFECTIVE_REVISION = "no_effective_revision"
    NO_REVIEWED_COVERAGE = "no_reviewed_coverage"
    CONFLICTING_REVISIONS = "conflicting_revisions"
    CATALOGUE_UNAVAILABLE = "catalogue_unavailable"


class CatalogueUnavailable(Exception):
    """Expected storage outage; adapters must translate known storage failures."""


@dataclass(frozen=True, slots=True)
class CitationResult:
    status: CitationStatus
    revision: ProvisionRevision | None = None


def resolve_citation(
    catalogue: ProvisionCatalogue, locator: Locator, *, event_on: date | None, as_of: date
) -> CitationResult:
    """Resolve an exact reference, including review coverage through the event date.

    This result does not determine legal applicability or independently authenticate a source.
    """
    if event_on is None:
        return CitationResult(CitationStatus.EVENT_DATE_REQUIRED)
    try:
        exact = tuple(r for r in catalogue.revisions_for(locator) if r.locator == locator)
    except CatalogueUnavailable:
        return CitationResult(CitationStatus.CATALOGUE_UNAVAILABLE)
    if not exact:
        return CitationResult(CitationStatus.NOT_FOUND)
    effective = tuple(r for r in exact if r.covers(event_on))
    if not effective:
        return CitationResult(CitationStatus.NO_EFFECTIVE_REVISION)
    reviewed = tuple(
        r for r in effective
        if r.status is ReviewStatus.APPROVED
        and r.checked_on is not None
        and event_on <= r.checked_on <= as_of
    )
    if len(reviewed) > 1:
        return CitationResult(CitationStatus.CONFLICTING_REVISIONS)
    if not reviewed:
        return CitationResult(CitationStatus.NO_REVIEWED_COVERAGE)
    return CitationResult(CitationStatus.CATALOGUE_MATCH, reviewed[0])
