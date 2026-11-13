"""SQL persistence shared by local and PostgreSQL deployments."""

from datetime import UTC, date, datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, LargeBinary,
    String, Text, UniqueConstraint, create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(UTC)


def identifier():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    tenant: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(default=True)
    __table_args__ = (UniqueConstraint("tenant", "name"),)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    author: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(240))
    narrative: Mapped[bytes] = mapped_column(LargeBinary)
    region: Mapped[str] = mapped_column(String(120))
    event_on: Mapped[date | None] = mapped_column(Date)
    language: Mapped[str] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("id", "tenant"),)


class CaseRecord:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    case_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    @classmethod
    def case_constraint(cls):
        return ForeignKeyConstraint(["case_id", "tenant"], ["cases.id", "cases.tenant"])


class Evidence(CaseRecord, Base):
    __tablename__ = "evidence"
    filename: Mapped[str] = mapped_column(String(180))
    mime: Mapped[str] = mapped_column(String(80))
    digest: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    extracted: Mapped[bytes] = mapped_column(LargeBinary)
    __table_args__ = (CaseRecord.case_constraint(), UniqueConstraint("case_id", "digest"))


class Analysis(CaseRecord, Base):
    __tablename__ = "analyses"
    author: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    reviewer: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CaseRecord.case_constraint(), Index("ix_analysis_case", "case_id", "created_at"))


class PenaltyAssessment(CaseRecord, Base):
    __tablename__ = "penalty_assessments"
    author: Mapped[str] = mapped_column(ForeignKey("users.id"))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    __table_args__ = (CaseRecord.case_constraint(), Index("ix_penalty_case", "case_id", "created_at"))


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    kind: Mapped[str] = mapped_column(String(12), index=True)
    key: Mapped[str] = mapped_column(String(520), index=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(2))
    text: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON)
    start: Mapped[date | None] = mapped_column(Date)
    end: Mapped[date | None] = mapped_column(Date)
    checked_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="draft")
    author: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reviewer: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    embedding: Mapped[list | None] = mapped_column(JSON().with_variant(Vector(1536), "postgresql"))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        UniqueConstraint("kind", "key", "digest", "start", "language"),
        Index("ix_sources_review", "kind", "status", "language"),
    )


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(48))
    resource: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def database(url):
    options = {"hide_parameters": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine, sessionmaker(engine, expire_on_commit=False)
