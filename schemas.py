from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Language = Literal["ru", "kk"]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12000)]
Snapshot = Annotated[str, StringConstraints(min_length=1, max_length=500000)]
ID = Annotated[str, StringConstraints(pattern=r"^[0-9a-f-]{36}$")]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseInput(Schema):
    title: Short
    narrative: Text
    region: Annotated[Short, Field(max_length=120)]
    event_on: date | None = None
    language: Language = "ru"


class NormInput(Schema):
    kind: Literal["norm"]
    act_id: Annotated[str, Field(pattern=r"^[A-Z0-9_]{1,40}$")]
    article: Annotated[Short, Field(max_length=48)]
    part: Annotated[Short, Field(max_length=48)] | None = None
    paragraph: Annotated[Short, Field(max_length=48)] | None = None
    subparagraph: Annotated[Short, Field(max_length=48)] | None = None
    title: Short
    language: Language
    url: Annotated[str, Field(max_length=2048)]
    text: Text
    snapshot: Snapshot
    start: date
    end: date | None = None
    effective_basis: Short


class JudgmentInput(Schema):
    kind: Literal["judgment"]
    title: Short
    language: Language
    url: Annotated[str, Field(max_length=2048)]
    text: Text
    snapshot: Snapshot
    court: Short
    case_number: Short
    decided_on: date
    category: Short
    facts: Text
    applied_norms: list[Short] = Field(max_length=40)
    outcome: Short
    instance: Short


class MrpInput(Schema):
    kind: Literal["mrp"]
    title: Short
    language: Language
    url: Annotated[str, Field(max_length=2048)]
    act_id: Annotated[str, Field(pattern=r"^[A-Z0-9_]{1,40}$")]
    text: Text
    snapshot: Snapshot
    start: date
    end: date
    amount: Decimal = Field(gt=0, max_digits=9, decimal_places=2)


class RuleInput(Schema):
    kind: Literal["rule"]
    title: Short
    language: Language
    norm_id: ID
    mrp_date_norm_id: ID
    subject: Short
    conditions: list[Short] = Field(min_length=1, max_length=20)
    mrp_date_basis: Literal["event", "decision"]
    minimum: Decimal = Field(gt=0, max_digits=8, decimal_places=3)
    maximum: Decimal = Field(gt=0, max_digits=8, decimal_places=3)

    @model_validator(mode="after")
    def validate_range(self):
        if self.maximum < self.minimum or len(set(self.conditions)) != len(self.conditions):
            raise ValueError("Invalid range or duplicate conditions")
        return self


class SourceReview(Schema):
    verified: Literal[True]


class AnalysisInput(Schema):
    allow_provider: bool = False


class Claim(Schema):
    text: Text
    source_id: ID
    quote: Annotated[str, StringConstraints(strip_whitespace=True, min_length=12, max_length=800)]
    evidence_ids: list[ID] = Field(max_length=15)


class Analogy(Claim):
    similarities: Text
    differences: Text


class EvidenceAssessment(Schema):
    evidence_id: ID
    excerpt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=500)]
    assessment: Text


class ModelOpinion(Schema):
    in_scope: bool
    category: Short
    facts: list[Short] = Field(max_length=24)
    issues: list[Short] = Field(max_length=16)
    questions: list[Short] = Field(max_length=20)
    applicant: list[Short] = Field(max_length=16)
    respondent: list[Short] = Field(max_length=16)
    evidence: list[EvidenceAssessment] = Field(max_length=20)
    legal: list[Claim] = Field(max_length=18)
    practice: list[Analogy] = Field(max_length=10)
    civil: list[Claim] = Field(max_length=12)
    administrative: list[Claim] = Field(max_length=12)
    conclusion: list[Claim] = Field(max_length=12)
    next_actions: list[Short] = Field(max_length=12)


class ReviewInput(Schema):
    status: Literal["approved", "returned"]
    comment: Text


class PenaltyInput(Schema):
    rule_id: ID
    event_on: date
    decision_on: date | None = None
    subject: Short
    confirmations: dict[Short, Short] = Field(max_length=20)


class ExportInput(Schema):
    kind: Literal["opinion", "demand", "complaint", "application", "lawsuit"]
    format: Literal["pdf", "docx"]
    addressee: Short | None = None
    applicant: Short | None = None
    respondent: Short | None = None
    requests: list[Short] = Field(default_factory=list, max_length=15)
