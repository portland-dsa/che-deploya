"""Declarative Postgres provisioning: the model plus pure SQL rendering.

A ``Db`` describes a component's schema as data - a cluster group role, per-stage
roles and databases, and the revokes/grants applied inside each database. The
render functions turn each element into the exact statement the current hand-written
``provision/db.py`` issues, so the behavior (and its idempotent, non-destructive,
never-rotate-a-live-password discipline, enforced by the handler in Task 9) is
unchanged. Names are ``{stage}``-substituted by the caller before rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, AbstractSet, Sequence

if TYPE_CHECKING:
    from .spec import Stages


class Privilege(StrEnum):
    Connect = "CONNECT"
    Usage = "USAGE"
    All = "ALL"
    Select = "SELECT"
    Insert = "INSERT"
    Delete = "DELETE"


class GrantScope(StrEnum):
    Database = "database"
    Schema = "schema"
    Table = "table"


@dataclass(frozen=True)
class On:
    """The object a grant or revoke targets inside a database connection."""

    scope: GrantScope
    name: str

    @classmethod
    def database(cls, name: str) -> "On":
        return cls(GrantScope.Database, name)

    @classmethod
    def schema(cls, name: str) -> "On":
        return cls(GrantScope.Schema, name)

    @classmethod
    def table(cls, name: str) -> "On":
        return cls(GrantScope.Table, name)


@dataclass(frozen=True)
class Role:
    """A Postgres role; created only if absent and never altered afterward.

    ``password`` names a secret-token enum member whose decrypted value is fed to
    ``psql`` on stdin at creation (never on ``argv``). ``member_of`` adds the role
    to a group with ``IN ROLE`` at creation.
    """

    name: str
    login: bool = False
    password: StrEnum | None = None
    member_of: str | None = None


@dataclass(frozen=True)
class Database:
    name: str
    owner: str


@dataclass(frozen=True)
class Grant:
    privileges: AbstractSet[Privilege]
    on: On
    to: str
    only: "AbstractSet[Stages] | None" = None        # restrict to these Stages; None = the db's stages
    require_exists: bool = False


@dataclass(frozen=True)
class Revoke:
    privileges: AbstractSet[Privilege]
    on: On
    frm: str = "PUBLIC"


@dataclass(frozen=True)
class Db:
    group_role: Role | None = None
    roles: Sequence[Role] = ()
    databases: Sequence[Database] = ()
    revokes: Sequence[Revoke] = ()
    grants: Sequence[Grant] = ()


def role_names(db: Db) -> set[str]:
    """Every role name a ``Db`` declares (the group plus the per-stage roles)."""
    names = {r.name for r in db.roles}
    if db.group_role is not None:
        names.add(db.group_role.name)
    return names


def _privs(privileges: AbstractSet[Privilege]) -> str:
    """Render a privilege set in a stable order (``ALL`` alone, else sorted)."""
    if Privilege.All in privileges:
        return "ALL"
    return ", ".join(sorted(str(p) for p in privileges))


def _target(on: On) -> str:
    if on.scope is GrantScope.Database:
        return f"DATABASE {on.name}"
    if on.scope is GrantScope.Schema:
        return f"SCHEMA {on.name}"
    return on.name  # table: bare, schema-qualified by search_path


def _pg_literal(value: str) -> str:
    """Render a PostgreSQL string literal, doubling embedded single quotes.

    Recycled verbatim from the current ``provision/db.py``; used only for the
    migration password (fed to psql on stdin, never argv).
    """
    return "'" + value.replace("'", "''") + "'"


def render_group_role(role: Role) -> str:
    """The idempotent ``DO $$ ... CREATE ROLE <name> NOLOGIN $$`` for the group role."""
    return (
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role.name}') THEN "
        f"CREATE ROLE {role.name} NOLOGIN; "
        "END IF; END $$;"
    )


def render_create_role(role: Role, *, password: str | None) -> str:
    """``CREATE ROLE`` for a per-stage role (existence is checked by the handler).

    ``password`` is the already-decrypted value when ``role.password`` is set; it
    is embedded as a literal here and the statement is fed to psql on stdin.
    """
    parts = [f"CREATE ROLE {role.name}"]
    if role.login:
        parts.append("LOGIN")
    if password is not None:
        parts.append(f"PASSWORD {_pg_literal(password)}")
    if role.member_of is not None:
        parts.append(f"IN ROLE {role.member_of}")
    return " ".join(parts) + ";"


def render_create_database(db_: Database) -> str:
    return f"CREATE DATABASE {db_.name} OWNER {db_.owner};"


def render_revoke(rev: Revoke) -> str:
    return f"REVOKE {_privs(rev.privileges)} ON {_target(rev.on)} FROM {rev.frm};"


def render_grant(grant: Grant) -> str:
    stmt = f"GRANT {_privs(grant.privileges)} ON {_target(grant.on)} TO {grant.to};"
    if grant.require_exists:
        return (
            "DO $$ BEGIN "
            f"IF to_regclass('public.{grant.on.name}') IS NOT NULL THEN "
            f"{stmt} END IF; END $$;"
        )
    return stmt
