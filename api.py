"""REST API and standalone workspace."""
from contextlib import asynccontextmanager
from datetime import date
from hashlib import sha256
import json
import logging
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .analysis import GroundingError, ModelProvider, ProviderError, analyze
from .catalogue import act_links
from .citations import Locator, resolve_citation
from .config import Settings, kz_today
from .database import Analysis, Audit, Case, Evidence, PenaltyAssessment, Source, database, now
from .exports import document_content, render_docx, render_pdf
from .extraction import MAX_UPLOAD, extract_isolated, inspect
from .penalties import calculate
from .schemas import (
    AnalysisInput, CaseInput, ExportInput, JudgmentInput, MrpInput, NormInput,
    PenaltyInput, ReviewInput, RuleInput, SourceReview,
)
from .security import CASE_READ, CASE_WRITE, audit, authenticate
from .sources import SqlCatalogue, prepare_source, public_source, retrieve, review_source

STATIC = Path(__file__).with_name("static")
LOG = logging.getLogger(__name__)
ROLES_SOURCE = {"curator", "admin"}
ROLES_REVIEW = {"lawyer", "admin"}


def create_app(settings=None, provider=None):
    settings = settings or Settings.from_env()
    engine, sessions = database(settings.database_url)
    provider = provider or ModelProvider(settings)
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(application):
        application.state.cipher = settings.cipher()
        yield
        engine.dispose()

    app = FastAPI(title="AI ЖКХ LEGAL ENGINE", version="0.2.0", lifespan=lifespan)

    def db():
        with sessions() as session:
            yield session

    def principal(
        session=Depends(db),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ):
        user = authenticate(session, credentials.credentials if credentials else None)
        if not user:
            raise HTTPException(401, "authentication_required", {"WWW-Authenticate": "Bearer"})
        return user

    def require(user, roles):
        if user.role not in roles:
            raise HTTPException(403, "insufficient_role")

    def seal(value):
        return app.state.cipher.encrypt(json.dumps(value, ensure_ascii=False, default=str).encode())

    def open_sealed(value):
        return json.loads(app.state.cipher.decrypt(value))

    def tenant_record(session, model, resource_id, user, lock=False):
        query = select(model).where(model.id == resource_id, model.tenant == user.tenant)
        row = session.scalar(query.with_for_update() if lock else query)
        if not row:
            raise HTTPException(404, "not_found")
        return row

    def visible_analysis(session, row, result, case):
        if row.status != "approved":
            return row.status
        if result["case_updated_at"] != case.updated_at.isoformat():
            return "review_required"
        ids = [source["id"] for source in result["sources"]]
        current = session.scalars(select(Source.id).where(
            Source.id.in_(ids), Source.status == "approved",
        )).all()
        return "approved" if len(current) == len(ids) else "review_required"

    def write(session):
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "conflicting_record") from error

    def case_view(row, detail=False):
        result = {key: getattr(row, key) for key in (
            "id", "title", "region", "event_on", "language", "created_at", "updated_at",
        )}
        if detail:
            result["narrative"] = open_sealed(row.narrative)
        return result

    def evidence_view(row, detail=False):
        result = {"id": row.id, "filename": row.filename, "mime": row.mime, "sha256": row.digest}
        extraction = open_sealed(row.extracted)
        result.update({key: extraction[key] for key in ("status", "warnings")})
        if detail:
            result["chunks"] = extraction["chunks"]
        return result

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, error):
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": entry["loc"], "type": entry["type"]} for entry in error.errors()
        ]})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_, error):
        LOG.error("database_operation_failed class=%s", type(error).__name__)
        return JSONResponse(status_code=503, content={"detail": "database_unavailable"})

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        if request.url.path.startswith("/v1/") and request.method in {"POST", "PUT"}:
            length = request.headers.get("content-length", "0")
            multipart = request.headers.get("content-type", "").startswith("multipart/form-data")
            maximum = MAX_UPLOAD + 256 * 1024 if multipart else 4 * 1024 * 1024
            if request.headers.get("transfer-encoding") or not length.isascii() or not length.isdigit():
                return JSONResponse(status_code=411, content={"detail": "content_length_required"})
            if len(length) > 10 or int(length) > maximum:
                return JSONResponse(status_code=413, content={"detail": "request_too_large"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
        return response

    @app.get("/health")
    def health(session=Depends(db)):
        session.execute(text("SELECT 1 FROM cases LIMIT 1"))
        return {"status": "ok"}

    @app.get("/v1/me")
    def me(user=Depends(principal)):
        return {"name": user.name, "role": user.role, "tenant": user.tenant,
                "llm_configured": provider.enabled, "case_transfer_configured": settings.allow_case_transfer,
                "llm_local": provider.local}

    @app.post("/v1/cases", status_code=201)
    def create_case(data: CaseInput, user=Depends(principal), session=Depends(db)):
        require(user, CASE_WRITE)
        row = Case(tenant=user.tenant, author=user.id, title=data.title, narrative=seal(data.narrative),
                   region=data.region, event_on=data.event_on, language=data.language)
        session.add(row)
        try:
            session.flush()
            audit(session, user, "case.create", row.id)
            write(session)
        except IntegrityError as error:
            raise HTTPException(409, "conflicting_record") from error
        return case_view(row)

    @app.get("/v1/cases")
    def list_cases(limit: Annotated[int, Query(ge=1, le=100)] = 30,
                   offset: Annotated[int, Query(ge=0, le=100000)] = 0,
                   user=Depends(principal), session=Depends(db)):
        require(user, CASE_READ)
        rows = session.scalars(select(Case).where(Case.tenant == user.tenant)
                               .order_by(Case.updated_at.desc(), Case.id).limit(limit).offset(offset)).all()
        audit(session, user, "case.list", user.id)
        write(session)
        return [case_view(row) for row in rows]

    @app.get("/v1/cases/{case_id}")
    def get_case(case_id: str, user=Depends(principal), session=Depends(db)):
        require(user, CASE_READ)
        row = tenant_record(session, Case, case_id, user)
        audit(session, user, "case.read", row.id)
        write(session)
        return case_view(row, detail=True)

    @app.put("/v1/cases/{case_id}")
    def update_case(case_id: str, data: CaseInput, user=Depends(principal), session=Depends(db)):
        require(user, CASE_WRITE)
        row = tenant_record(session, Case, case_id, user, lock=True)
        for key in ("title", "region", "event_on", "language"):
            setattr(row, key, getattr(data, key))
        row.narrative = seal(data.narrative)
        row.updated_at = now()
        audit(session, user, "case.update", row.id)
        write(session)
        return case_view(row)

    @app.post("/v1/cases/{case_id}/evidence", status_code=201)
    def upload(case_id: str, file: UploadFile, user=Depends(principal), session=Depends(db)):
        require(user, CASE_WRITE)
        tenant_record(session, Case, case_id, user)
        raw = file.file.read(MAX_UPLOAD + 1)
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "file_too_large")
        filename = (file.filename or "document").replace("\\", "/").split("/")[-1][:180]
        try:
            extension, mime = inspect(filename, raw)
            extraction = extract_isolated(extension, raw, settings.ocr_languages)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        case = tenant_record(session, Case, case_id, user, lock=True)
        digest = sha256(raw).hexdigest()
        row = Evidence(case_id=case.id, tenant=user.tenant, filename=filename, mime=mime,
                       digest=digest, content=app.state.cipher.encrypt(raw), extracted=seal(extraction))
        session.add(row)
        case.updated_at = now()
        try:
            session.flush()
        except IntegrityError as error:
            raise HTTPException(409, "duplicate_evidence") from error
        audit(session, user, "evidence.upload", row.id)
        write(session)
        return evidence_view(row)

    @app.get("/v1/cases/{case_id}/evidence")
    def list_evidence(case_id: str, user=Depends(principal), session=Depends(db)):
        require(user, CASE_READ)
        case = tenant_record(session, Case, case_id, user)
        rows = session.scalars(select(Evidence).where(Evidence.case_id == case.id,
                               Evidence.tenant == user.tenant).order_by(Evidence.created_at)).all()
        audit(session, user, "evidence.list", case.id)
        write(session)
        return [evidence_view(row) for row in rows]

    @app.get("/v1/evidence/{evidence_id}")
    def get_evidence(evidence_id: str, user=Depends(principal), session=Depends(db)):
        require(user, CASE_READ)
        row = tenant_record(session, Evidence, evidence_id, user)
        audit(session, user, "evidence.read", row.id)
        write(session)
        return evidence_view(row, detail=True)

    @app.get("/v1/evidence/{evidence_id}/download")
    def download(evidence_id: str, user=Depends(principal), session=Depends(db)):
        require(user, CASE_READ)
        row = tenant_record(session, Evidence, evidence_id, user)
        audit(session, user, "evidence.download", row.id)
        write(session)
        disposition = "attachment; filename*=UTF-8''" + quote(row.filename, safe="")
        return Response(app.state.cipher.decrypt(row.content), media_type="application/octet-stream",
                        headers={"Content-Disposition": disposition})

    @app.post("/v1/cases/{case_id}/analyses", status_code=201)
    def create_analysis(case_id: str, data: AnalysisInput, user=Depends(principal), session=Depends(db)):
        require(user, CASE_WRITE)
        case = tenant_record(session, Case, case_id, user)
        if not provider.enabled:
            raise HTTPException(503, "llm_not_configured")
        if not provider.permits(data.allow_provider):
            raise HTTPException(403, "case_transfer_disabled")
        rows = session.scalars(select(Evidence).where(
            Evidence.case_id == case.id, Evidence.tenant == user.tenant,
        ).order_by(Evidence.created_at.desc(), Evidence.id).limit(13)).all()
        evidence = []
        for row in rows[:12]:
            item = evidence_view(row, detail=True)
            shortened = [{**chunk, "text": chunk["text"][:1800]} for chunk in item["chunks"][:4]]
            if shortened != item["chunks"]:
                item["status"] = "partial"
            item["chunks"] = shortened
            evidence.append(item)
        audit(session, user, "analysis.request", case.id)
        write(session)
        try:
            result = analyze(session, case, open_sealed(case.narrative), evidence, provider)
        except (ProviderError, GroundingError) as error:
            audit(session, user, "analysis.rejected", case.id)
            write(session)
            raise HTTPException(502, str(error)) from error
        if len(rows) > 12:
            result["limitations"].append("evidence_context_limit")
        result["case_updated_at"] = case.updated_at.isoformat()
        record = Analysis(case_id=case.id, tenant=user.tenant, author=user.id,
                          status="draft", payload=seal(result))
        session.add(record)
        session.flush()
        audit(session, user, "analysis.create", record.id)
        write(session)
        return {"id": record.id, "status": record.status, **result}

    @app.get("/v1/cases/{case_id}/analyses")
    def list_analyses(case_id: str, user=Depends(principal), session=Depends(db)):
        require(user, CASE_READ)
        case = tenant_record(session, Case, case_id, user)
        rows = session.scalars(select(Analysis).where(
            Analysis.case_id == case.id, Analysis.tenant == user.tenant,
        ).order_by(Analysis.created_at.desc()).limit(100)).all()
        return [{"id": row.id, "status": visible_analysis(session, row, open_sealed(row.payload), case),
                 "created_at": row.created_at} for row in rows]

    @app.get("/v1/analyses/{analysis_id}")
    def get_analysis(analysis_id: str, user=Depends(principal), session=Depends(db)):
        require(user, CASE_READ)
        row = tenant_record(session, Analysis, analysis_id, user)
        case = tenant_record(session, Case, row.case_id, user)
        result = open_sealed(row.payload)
        status = visible_analysis(session, row, result, case)
        audit(session, user, "analysis.read", row.id)
        write(session)
        return {"id": row.id, "status": status, **result}

    @app.post("/v1/analyses/{analysis_id}/review")
    def review_analysis(analysis_id: str, data: ReviewInput, user=Depends(principal), session=Depends(db)):
        require(user, ROLES_REVIEW)
        row = tenant_record(session, Analysis, analysis_id, user, lock=True)
        case = tenant_record(session, Case, row.case_id, user, lock=True)
        result = open_sealed(row.payload)
        if row.author == user.id:
            raise HTTPException(409, "different_reviewer_required")
        if row.status != "draft":
            raise HTTPException(409, "analysis_is_not_draft")
        if data.status == "approved":
            if result["release_blocked"] or result["case_updated_at"] != case.updated_at.isoformat():
                raise HTTPException(409, "analysis_not_eligible")
            ids = [source["id"] for source in result["sources"]]
            if len(session.scalars(select(Source.id).where(
                    Source.id.in_(ids), Source.status == "approved")).all()) != len(ids):
                raise HTTPException(409, "source_withdrawn")
        result["review_comment"] = data.comment
        changed = session.execute(update(Analysis).where(
            Analysis.id == row.id, Analysis.tenant == user.tenant, Analysis.status == "draft",
        ).values(status=data.status, payload=seal(result), reviewer=user.id, reviewed_at=now()))
        if changed.rowcount != 1:
            raise HTTPException(409, "analysis_is_not_draft")
        audit(session, user, "analysis." + data.status, row.id)
        write(session)
        return {"id": row.id, "status": data.status}

    @app.post("/v1/analyses/{analysis_id}/export")
    def export(analysis_id: str, data: ExportInput, user=Depends(principal), session=Depends(db)):
        require(user, CASE_READ)
        row = tenant_record(session, Analysis, analysis_id, user)
        case = tenant_record(session, Case, row.case_id, user)
        result = open_sealed(row.payload)
        status = visible_analysis(session, row, result, case)
        title, sections = document_content(result, data, status == "approved")
        try:
            raw = render_pdf(title, sections, settings.pdf_font) if data.format == "pdf" else render_docx(title, sections)
        except ValueError as error:
            raise HTTPException(503, str(error)) from error
        audit(session, user, "analysis.export", row.id)
        write(session)
        mime = "application/pdf" if data.format == "pdf" else (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        return Response(raw, media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="{data.kind}.{data.format}"'})

    @app.get("/v1/sources")
    def search_sources(kind: Literal["norm", "judgment", "rule", "mrp"] = "norm",
                       language: Literal["ru", "kk"] = "ru",
                       q: Annotated[str, Query(max_length=500)] = "",
                       event_on: Annotated[date | None, Query()] = None,
                       user=Depends(principal), session=Depends(db)):
        if q:
            return [public_source(row) for row in retrieve(session, kind, q, language, event_on)]
        query = select(Source).where(Source.kind == kind, Source.status == "approved",
                                     Source.language == language)
        if event_on and kind == "norm":
            query = query.where(Source.start <= event_on, Source.checked_on >= event_on,
                                or_(Source.end.is_(None), Source.end > event_on))
        rows = session.scalars(query.order_by(Source.checked_on.desc(), Source.id).limit(50)).all()
        return [public_source(row) for row in rows]

    @app.get("/v1/catalogue/acts")
    def catalogue_acts(language: Literal["ru", "kk"] = "ru", user=Depends(principal)):
        return act_links(language)

    @app.get("/v1/catalogue/citation")
    def citation(act_id: str, article: str, event_on: date, language: Literal["ru", "kk"] = "ru",
                 part: str | None = None, paragraph: str | None = None,
                 subparagraph: str | None = None,
                 user=Depends(principal), session=Depends(db)):
        try:
            address = Locator(act_id, article, language, part, paragraph, subparagraph)
        except ValueError as error:
            raise HTTPException(422, "invalid_locator") from error
        result = resolve_citation(SqlCatalogue(session), address, event_on=event_on, as_of=kz_today())
        row = session.get(Source, result.revision.revision_id) if result.revision else None
        return {"status": result.status, "source": public_source(row) if row else None}

    @app.get("/v1/sources/{source_id}/snapshot")
    def get_snapshot(source_id: str, user=Depends(principal), session=Depends(db)):
        row = session.get(Source, source_id)
        if not row or (row.status != "approved" and user.role not in ROLES_SOURCE):
            raise HTTPException(404, "not_found")
        return {"id": row.id, "sha256": row.digest, "snapshot": row.snapshot}

    @app.get("/v1/admin/sources")
    def admin_sources(status: Literal["draft", "approved", "withdrawn"] = "draft",
                      user=Depends(principal), session=Depends(db)):
        require(user, ROLES_SOURCE)
        rows = session.scalars(select(Source).where(Source.status == status)
                               .order_by(Source.id).limit(100)).all()
        return [public_source(row) for row in rows]

    @app.post("/v1/admin/sources", status_code=201)
    def add_source(data: Annotated[NormInput | JudgmentInput | MrpInput | RuleInput, Body(discriminator="kind")],
                   user=Depends(principal), session=Depends(db)):
        require(user, ROLES_SOURCE)
        try:
            row = prepare_source(session, data, user)
            session.add(row)
            session.flush()
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except IntegrityError as error:
            raise HTTPException(409, "duplicate_source") from error
        audit(session, user, "source.import", row.id)
        write(session)
        return public_source(row)

    @app.post("/v1/admin/sources/{source_id}/approve")
    def approve_source(source_id: str, data: SourceReview, user=Depends(principal), session=Depends(db)):
        require(user, ROLES_SOURCE)
        row = session.get(Source, source_id)
        if not row:
            raise HTTPException(404, "not_found")
        try:
            review_source(session, row, user)
        except ValueError as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        audit(session, user, "source.approve", row.id)
        write(session)
        return public_source(row)

    @app.post("/v1/admin/sources/{source_id}/withdraw")
    def withdraw_source(source_id: str, user=Depends(principal), session=Depends(db)):
        require(user, ROLES_SOURCE)
        changed = session.execute(update(Source).where(Source.id == source_id,
                                  Source.status == "approved").values(status="withdrawn"))
        if changed.rowcount != 1:
            raise HTTPException(404, "approved_source_not_found")
        audit(session, user, "source.withdraw", source_id)
        write(session)
        return {"id": source_id, "status": "withdrawn"}

    @app.post("/v1/admin/sources/{source_id}/embed")
    def embed_source(source_id: str, user=Depends(principal), session=Depends(db)):
        require(user, ROLES_SOURCE)
        row = session.get(Source, source_id)
        if not row or row.status != "approved" or row.kind not in {"norm", "judgment"}:
            raise HTTPException(404, "approved_search_source_not_found")
        if not provider.enabled or not settings.embedding_model or engine.dialect.name != "postgresql":
            raise HTTPException(503, "embeddings_not_configured")
        try:
            row.embedding = provider.embed(row.title + "\n" + row.text)
        except ProviderError as error:
            raise HTTPException(502, str(error)) from error
        row.embedding_model = settings.llm_url.rstrip("/") + "|" + settings.embedding_model
        audit(session, user, "source.embed", row.id)
        write(session)
        return {"id": row.id, "status": "indexed"}

    @app.post("/v1/penalties")
    def penalty(data: PenaltyInput, user=Depends(principal), session=Depends(db)):
        require(user, ROLES_REVIEW)
        try:
            result = calculate(session, data)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        audit(session, user, "penalty.calculate", data.rule_id)
        write(session)
        return result

    @app.post("/v1/cases/{case_id}/penalties", status_code=201)
    def case_penalty(case_id: str, data: PenaltyInput, user=Depends(principal), session=Depends(db)):
        require(user, ROLES_REVIEW)
        case = tenant_record(session, Case, case_id, user, lock=True)
        if case.event_on != data.event_on:
            raise HTTPException(409, "penalty_event_date_must_match_case")
        try:
            result = calculate(session, data)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        record = PenaltyAssessment(
            case_id=case.id, tenant=user.tenant, author=user.id,
            payload=seal({"calculation": result, "inputs": data.model_dump(mode="json"),
                          "case_updated_at": case.updated_at.isoformat()}),
        )
        session.add(record)
        session.flush()
        audit(session, user, "penalty.save", record.id)
        write(session)
        return {"id": record.id, "created_at": record.created_at, **result}

    @app.get("/v1/cases/{case_id}/penalties")
    def case_penalty_history(case_id: str, user=Depends(principal), session=Depends(db)):
        require(user, CASE_READ)
        case = tenant_record(session, Case, case_id, user)
        rows = session.scalars(select(PenaltyAssessment).where(
            PenaltyAssessment.case_id == case.id, PenaltyAssessment.tenant == user.tenant,
        ).order_by(PenaltyAssessment.created_at.desc()).limit(100)).all()
        results = []
        for row in rows:
            snapshot = open_sealed(row.payload)
            calculation = snapshot["calculation"]
            ids = {source["id"] for source in calculation["sources"]}
            approved = session.scalars(select(Source.id).where(
                Source.id.in_(ids), Source.status == "approved",
            )).all()
            stale = (len(approved) != len(ids) or snapshot["case_updated_at"] != case.updated_at.isoformat())
            results.append({"id": row.id, "created_at": row.created_at, **calculation,
                            "status": "review_required" if stale else calculation["status"]})
        audit(session, user, "penalty.list", case.id)
        write(session)
        return results

    @app.get("/v1/admin/audit")
    def list_audit(user=Depends(principal), session=Depends(db)):
        require(user, {"auditor", "admin"})
        rows = session.scalars(select(Audit).where(Audit.tenant == user.tenant)
                               .order_by(Audit.created_at.desc(), Audit.id).limit(100)).all()
        return [{key: getattr(row, key) for key in ("id", "actor", "action", "resource", "created_at")}
                for row in rows]

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
    return app


app = create_app()
