"""Curated legal corpus, provenance and date-aware hybrid retrieval."""
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from pgvector.sqlalchemy import Vector
from sqlalchemy import func, literal_column, or_, select, text, type_coerce, update

from .citations import Locator, ProvisionRevision, ReviewStatus, official_url_matches, resolve_citation
from .config import kz_today
from .database import Source
from .schemas import JudgmentInput, MrpInput, NormInput, RuleInput


def fingerprint(value):
    return sha256(value.encode()).hexdigest()


def locator(details):
    return Locator(**{key: details.get(key) for key in (
        "act_id", "article", "language", "part", "paragraph", "subparagraph"
    )})


def address_key(address):
    return json.dumps([address.act_id, address.article, address.part,
                       address.paragraph, address.subparagraph], ensure_ascii=False)


def revision(row):
    return ProvisionRevision(
        row.id, locator(row.details), row.title, row.text, row.start, row.end,
        row.url, row.digest, ReviewStatus(row.status), row.checked_on, row.reviewer,
    )


def judiciary_url(url):
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        return False
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.netloc in {"office.sud.kz", "sud.gov.kz", "www.sud.gov.kz", "sudact.sud.kz"}
        and len(parts.path.strip("/")) > 0
    )


def approved_norm(session, source_id):
    row = session.get(Source, source_id)
    if not row or row.kind != "norm" or row.status != "approved":
        raise ValueError("approved_norm_required")
    return row


def prepare_source(session, entry, actor):
    data = entry.model_dump(mode="json")
    if isinstance(entry, RuleInput):
        norm = approved_norm(session, entry.norm_id)
        approved_norm(session, entry.mrp_date_norm_id)
        snapshot = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return Source(
            kind=entry.kind, key=entry.norm_id, title=entry.title, url=norm.url,
            language=entry.language, text=norm.text, snapshot=snapshot,
            digest=fingerprint(snapshot), details=data, start=norm.start, end=norm.end,
            author=actor.id,
        )
    if entry.text not in entry.snapshot:
        raise ValueError("text_must_match_snapshot")
    start = getattr(entry, "start", None)
    end = getattr(entry, "end", None)
    if end is not None and end <= start:
        raise ValueError("invalid_interval")
    if isinstance(entry, NormInput):
        address = locator(data)
        if not official_url_matches(entry.url, address):
            raise ValueError("invalid_official_act_url")
        key = address_key(address)
    elif isinstance(entry, MrpInput):
        if not official_url_matches(entry.url, Locator(entry.act_id, "1", entry.language)):
            raise ValueError("invalid_official_mrp_url")
        key = "KZT"
    elif isinstance(entry, JudgmentInput):
        if not judiciary_url(entry.url) or entry.decided_on > kz_today():
            raise ValueError("invalid_judgment_source")
        key = fingerprint(json.dumps([entry.court.casefold(), entry.case_number.casefold(),
                                      entry.decided_on.isoformat(), entry.instance.casefold()]))
        start = entry.decided_on
    else:
        raise ValueError("unknown_source_kind")
    for key_to_remove in ("snapshot", "text", "title", "url", "kind"):
        data.pop(key_to_remove)
    return Source(
        kind=entry.kind, key=key, title=entry.title, url=entry.url, language=entry.language,
        text=entry.text, snapshot=entry.snapshot, digest=fingerprint(entry.snapshot),
        details=data, start=start, end=end, author=actor.id,
    )


def review_source(session, row, actor):
    if row.author == actor.id:
        raise ValueError("different_reviewer_required")
    if row.kind == "rule":
        approved_norm(session, row.details["norm_id"])
        approved_norm(session, row.details["mrp_date_norm_id"])
    # Serialize reviews across languages for statutory amounts and per exact corpus key.
    if session.bind.dialect.name == "postgresql":
        scope = "mrp" if row.kind == "mrp" else f"{row.kind}:{row.language}:{row.key}"
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"), {"scope": scope})
    changed = session.execute(
        update(Source).where(Source.id == row.id, Source.status == "draft")
        .values(status="approved", checked_on=kz_today(), reviewer=actor.id)
    )
    if changed.rowcount != 1:
        raise ValueError("source_is_not_draft")
    query = select(Source.id).where(
        Source.kind == row.kind, Source.key == row.key, Source.id != row.id,
        Source.status == "approved",
    )
    if row.kind != "mrp":
        query = query.where(Source.language == row.language)
    if row.kind in {"norm", "mrp"}:
        query = query.where(or_(Source.end.is_(None), Source.end > row.start))
        if row.end is not None:
            query = query.where(Source.start < row.end)
    if row.kind != "rule" and session.scalar(query.limit(1)):
        raise ValueError("overlapping_approved_source")


def public_source(row, *, include_text=True):
    result = {
        "id": row.id, "kind": row.kind, "title": row.title, "url": row.url,
        "language": row.language, "start": row.start, "end": row.end,
        "checked_on": row.checked_on, "status": row.status,
        "snapshot_sha256": row.digest, "details": row.details,
    }
    if include_text:
        result["text"] = row.text
    return result


class SqlCatalogue:
    def __init__(self, session):
        self.session = session

    def revisions_for(self, address):
        rows = self.session.scalars(select(Source).where(
            Source.kind == "norm", Source.language == address.language,
            Source.key == address_key(address), Source.status == "approved",
        )).all()
        return [revision(row) for row in rows]


def exact_on(session, row, event_on):
    result = resolve_citation(SqlCatalogue(session), locator(row.details),
                              event_on=event_on, as_of=kz_today())
    return result.revision is not None and result.revision.revision_id == row.id


def terms(query):
    return list(dict.fromkeys(word.lower() for word in re.findall(r"[^\W_]+", query)
                              if len(word) >= 3))[:40]


def retrieve(session, kind, query, language, event_on=None, vector=None, embedding_model=None, limit=12):
    base = select(Source).where(
        Source.kind == kind, Source.status == "approved", Source.language == language,
        Source.checked_on <= kz_today(),
    )
    if kind == "norm" and event_on:
        base = base.where(Source.start <= event_on, Source.checked_on >= event_on,
                          or_(Source.end.is_(None), Source.end > event_on))
    words = terms(query)
    if not words:
        return []
    is_postgres = session.bind.dialect.name == "postgresql"
    combined = Source.title + literal_column("' '") + Source.text
    if is_postgres:
        language_config = literal_column("'simple'")
        lexemes = func.to_tsvector(language_config, combined)
        prefixes = (word[:-2] if len(word) >= 7 else word for word in words)
        needle = func.to_tsquery(language_config, " | ".join(f"{word}:*" for word in prefixes))
        matches = base.where(lexemes.bool_op("@@")(needle)).order_by(
            func.ts_rank_cd(lexemes, needle).desc(), Source.id)
        lexical = session.scalars(matches.limit(limit * 3)).all()
    else:
        # SQLite lower() only folds ASCII; casefold Cyrillic in Python.
        rows = session.scalars(base.order_by(Source.checked_on.desc(), Source.id).limit(5000)).all()
        weighted = [(sum((row.title + " " + row.text).casefold().count(w) for w in words), row)
                    for row in rows]
        lexical = [row for score, row in sorted(weighted, key=lambda pair: -pair[0]) if score][:limit * 3]
    rankings = [lexical]
    if is_postgres and vector is not None:
        semantic = base.where(Source.embedding.is_not(None), Source.embedding_model == embedding_model).order_by(
            type_coerce(Source.embedding, Vector(1536)).cosine_distance(vector), Source.id,
        )
        rankings.append(session.scalars(semantic.limit(limit * 3)).all())
    scores = {}
    for ranking in rankings:
        for index, row in enumerate(ranking, 1):
            old_score, _ = scores.get(row.id, (0, row))
            scores[row.id] = (old_score + 1 / (60 + index), row)
    ranked = sorted(scores.values(), key=lambda pair: (-pair[0], pair[1].id))
    return [row for _, row in ranked if kind != "norm" or not event_on
            or exact_on(session, row, event_on)][:limit]
