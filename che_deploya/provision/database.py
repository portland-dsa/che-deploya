# che_deploya/provision/database.py
"""``provision db``: create a component's Postgres roles, databases, and grants.

Idempotent and non-destructive: existing roles, databases, and passwords are left
exactly as they are, so this is safe to re-run against a live box. The interpreter
walks a ``Db`` in dependency order - group role, then per stage the roles,
databases, and grants - emitting the statements from ``che_deploya.db``'s
renderers. The psql helpers are recycled verbatim from the original tool.

Box-side: escalates to root and drives psql as the postgres superuser.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import AbstractSet, Optional

from .. import ops
from ..db import (
    Database, Grant, On, Revoke, Role,
    render_create_database, render_create_role, render_grant, render_group_role, render_revoke,
)
from ..spec import Component, DeploySpec, Stages
from ..templating import resolve
from .creds import secret_file_for


def provision_db(
    spec: DeploySpec, component: Component, *, stages: AbstractSet[Stages], secrets_dir: Path
) -> None:
    """Provision ``component``'s database for each stage (idempotent, non-destructive)."""
    db = component.db
    if db is None:
        return
    ops.prepare(secrets_dir)

    if db.group_role is not None:
        _psql("-c", render_group_role(db.group_role))

    for stage in stages:
        def sub(name: str, stage: Stages = stage) -> str:
            return resolve(name, root=spec.root, component=component.name, stage=stage)

        for role in db.roles:
            name = sub(role.name)
            if _role_exists(name):
                continue
            renamed = replace(role, name=name)
            if role.password is not None:
                pw = _migration_password(spec, component, role.password, stage, secrets_dir)
                _psql(stdin=render_create_role(renamed, password=pw) + "\n")
                del pw
            else:
                _psql("-c", render_create_role(renamed, password=None))

        for database in db.databases:
            dname = sub(database.name)
            if not _db_exists(dname):
                _psql("-c", render_create_database(replace(database, name=dname, owner=sub(database.owner))))

        for database in db.databases:
            dname = sub(database.name)
            for rev in db.revokes:
                _psql("-c", render_revoke(replace(rev, on=On(rev.on.scope, sub(rev.on.name)))), dbname=dname)
            for grant in db.grants:
                if grant.only is not None and stage not in grant.only:
                    continue
                resolved = replace(grant, to=sub(grant.to), on=On(grant.on.scope, sub(grant.on.name)))
                _psql("-c", render_grant(resolved), dbname=dname)
            print(f"ok: {dname}")


def _migration_password(spec, component, token, stage, secrets_dir) -> str:
    """Decrypt this stage's migration password out of its encrypted file."""
    resolver = secret_file_for(spec, component, secrets_dir)
    for s in ops.SecretsIter([stage], resolver, [token]):
        return s.value.decode("utf-8").strip()
    raise KeyError(token)


# --- recycled verbatim from the original provision/db.py -----------------------

def _psql(*sql_args: str, dbname: Optional[str] = None, stdin: Optional[str] = None) -> str:
    cmd = ["runuser", "-u", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1"]
    if dbname is not None:
        cmd += ["-d", dbname]
    cmd += list(sql_args)
    return ops.run(cmd, input=stdin, capture=True).stdout.strip()


def _role_exists(name: str) -> bool:
    return bool(_psql("-tAc", f"SELECT 1 FROM pg_roles WHERE rolname = '{name}'"))


def _db_exists(name: str) -> bool:
    return bool(_psql("-tAc", f"SELECT 1 FROM pg_database WHERE datname = '{name}'"))
