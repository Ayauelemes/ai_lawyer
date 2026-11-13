from hashlib import sha256
import secrets

from sqlalchemy import select

from .database import Audit, User

ROLES = {"operator", "lawyer", "curator", "auditor", "admin"}
CASE_READ = {"operator", "lawyer", "admin"}
CASE_WRITE = CASE_READ


def token_digest(token):
    return sha256(token.encode("utf-8")).hexdigest()


def issue_token():
    return secrets.token_urlsafe(36)


def authenticate(session, token):
    if not token or len(token) > 256:
        return None
    return session.scalar(select(User).where(User.token_digest == token_digest(token), User.active))


def audit(session, actor, action, resource):
    session.add(Audit(tenant=actor.tenant, actor=actor.id, action=action, resource=resource))
