import argparse
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import select

from .config import Settings
from .database import User, database
from .security import ROLES, issue_token, token_digest


def initialize(settings):
    data = Path("local-data")
    data.mkdir(mode=0o700, exist_ok=True)
    if not settings.data_key:
        try:
            fd = os.open(data / "data.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
    settings.cipher()
    command.upgrade(Config("alembic.ini"), "head")


def main():
    parser = argparse.ArgumentParser(prog="zhkh-legal")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    for name in ("user-add", "user-rotate", "user-disable"):
        sub = commands.add_parser(name)
        sub.add_argument("--tenant", required=True)
        sub.add_argument("--name", required=True)
        if name == "user-add":
            sub.add_argument("--role", choices=sorted(ROLES), required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "init":
        initialize(settings)
        return
    if not args.tenant.strip() or len(args.tenant) > 80 or not args.name.strip() or len(args.name) > 120:
        parser.error("Invalid tenant or user name")
    engine, sessions = database(settings.database_url)
    with sessions.begin() as session:
        user = session.scalar(select(User).where(User.tenant == args.tenant, User.name == args.name))
        if args.command == "user-add":
            if user:
                parser.error("User already exists")
            user = User(tenant=args.tenant, name=args.name, role=args.role)
            session.add(user)
        elif not user:
            parser.error("User not found")
        if args.command == "user-disable":
            user.active = False
            token = None
        else:
            token = issue_token()
            user.token_digest = token_digest(token)
            user.active = True
    engine.dispose()
    if token:
        print(token)


if __name__ == "__main__":
    main()
