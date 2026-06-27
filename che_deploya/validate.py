"""Startup validation that makes inconsistent specs fail before any box is touched.

``build_cli`` runs :func:`validate` so a malformed declaration raises here rather
than partway through provisioning. The checks cover exactly the mistakes the type
system cannot: dangling role references, stage-set inconsistencies, ambiguous
singleton templated units, and duplicate component names.
"""

from __future__ import annotations

from .spec import DeploySpec, Component, TemplatedUnit, SecretSource
from .db import Db, role_names


class SpecError(Exception):
    """A declaration is internally inconsistent. Raised by :func:`validate`."""


def validate(spec: DeploySpec) -> None:
    """Raise :class:`SpecError` if ``spec`` is internally inconsistent."""
    seen: set[str] = set()
    for component in spec.components:
        if component.name in seen:
            raise SpecError(f"duplicate component name: {component.name!r}")
        seen.add(component.name)

        active = spec.active_stages(component)
        if not active <= frozenset(spec.stages):
            raise SpecError(
                f"component {component.name!r} stages {set(active)} are not a "
                f"subset of the spec stages {set(spec.stages)}"
            )

        _check_exclusions(component, active)
        _check_units(component, active)
        if component.db is not None:
            _check_db(component, component.db)


def _check_exclusions(component: Component, active: frozenset) -> None:
    maps = []
    if component.secrets is not None:
        maps.append(component.secrets.exclude)
    for unit in component.units:
        if isinstance(unit, TemplatedUnit):
            maps.append(unit.env.exclude)
    for mapping in maps:
        for stage in mapping:
            if stage not in active:
                raise SpecError(
                    f"component {component.name!r} excludes on {stage!r}, which "
                    f"is not an active stage for it"
                )


def _check_units(component: Component, active: frozenset) -> None:
    for unit in component.units:
        if isinstance(unit, TemplatedUnit) and not unit.per_stage and len(active) != 1:
            raise SpecError(
                f"component {component.name!r} has a singleton templated unit "
                f"({unit.dest!r}) but {len(active)} active stages; a singleton "
                f"templated unit needs exactly one stage to render with"
            )
        if (
            isinstance(unit, TemplatedUnit)
            and unit.env.src is SecretSource
            and component.secrets is None
        ):
            raise SpecError(
                f"component {component.name!r} has a templated unit {unit.dest!r} "
                f"whose env reads from the component Secret file, but the component "
                f"declares no secrets"
            )


def _check_db(component: Component, db: Db) -> None:
    declared = role_names(db)
    for database in db.databases:
        if database.owner not in declared:
            raise SpecError(
                f"component {component.name!r} database {database.name!r} owner "
                f"{database.owner!r} is not a declared role"
            )
    for role in db.roles:
        if role.member_of is not None and role.member_of not in declared:
            raise SpecError(
                f"component {component.name!r} role {role.name!r} member_of "
                f"{role.member_of!r} is not a declared role"
            )
    for grant in db.grants:
        if grant.to != "PUBLIC" and grant.to not in declared:
            raise SpecError(
                f"component {component.name!r} grant to {grant.to!r} is not a "
                f"declared role"
            )
