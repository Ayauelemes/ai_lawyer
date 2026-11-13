"""Deterministic calculator over separately approved rules and MRP records."""
from decimal import Decimal

from sqlalchemy import or_, select

from .config import kz_today
from .database import Source
from .sources import approved_norm, exact_on, public_source


def calculate(session, request):
    rule = session.get(Source, request.rule_id)
    if not rule or rule.kind != "rule" or rule.status != "approved":
        raise ValueError("approved_rule_required")
    details = rule.details
    if request.subject != details["subject"]:
        raise ValueError("subject_mismatch")
    if set(details["conditions"]) != set(request.confirmations):
        raise ValueError("conditions_not_confirmed")
    norm = approved_norm(session, details["norm_id"])
    date_norm = approved_norm(session, details["mrp_date_norm_id"])
    if not exact_on(session, norm, request.event_on):
        raise ValueError("sanction_norm_not_effective")
    mrp_on = request.event_on if details["mrp_date_basis"] == "event" else request.decision_on
    if mrp_on is None or mrp_on < request.event_on or mrp_on > kz_today():
        raise ValueError("valid_mrp_date_required")
    if not exact_on(session, date_norm, mrp_on):
        raise ValueError("mrp_date_norm_not_effective")
    amounts = session.scalars(select(Source).where(
        Source.kind == "mrp", Source.status == "approved", Source.start <= mrp_on,
        Source.checked_on >= mrp_on, Source.checked_on <= kz_today(),
        or_(Source.end.is_(None), Source.end > mrp_on),
    ).limit(2)).all()
    if len(amounts) != 1:
        raise ValueError("unique_verified_mrp_required")
    mrp = amounts[0]
    amount = Decimal(mrp.details["amount"])
    low = Decimal(details["minimum"])
    high = Decimal(details["maximum"])
    return {
        "status": "conditional_calculation", "currency": "KZT", "mrp_on": mrp_on,
        "mrp": str(amount), "minimum_mrp": str(low), "maximum_mrp": str(high),
        "minimum_kzt": str(amount * low), "maximum_kzt": str(amount * high),
        "formula": "KZT = MRP × multiplier", "conditions": request.confirmations,
        "sources": [public_source(row) for row in (rule, norm, date_norm, mrp)],
    }
