"""Startup validation that makes inconsistent specs fail before any box is touched.

``build_cli`` runs :func:`validate` so a malformed declaration raises here rather
than partway through provisioning. The checks cover exactly the mistakes the type
system cannot: dangling role references, stage-set inconsistencies, ambiguous
singleton templated units, and duplicate component names.
"""

from __future__ import annotations

from string import Formatter
from typing import assert_never

from .spec import (
    DeploySpec,
    Component,
    TemplatedUnit,
    SecretSource,
    StageRestart,
    SharedRestart,
    normalize_restart,
)
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
        _check_provisioning(component)
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
    seen: set[str] = set()
    for unit in component.units:
        if unit.dest in seen:
            raise SpecError(
                f"component {component.name!r} has two units with dest "
                f"{unit.dest!r}; each dest must be unique"
            )
        seen.add(unit.dest)
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


def _placeholders(text: str) -> set[str]:
    """The ``{name}`` fields referenced in ``text`` (``{{`` escapes excluded)."""
    return {name for _, name, _, _ in Formatter().parse(text) if name is not None}


def _check_provisioning(component: Component) -> None:
    """Validate a component's ``check`` and ``restart`` declarations."""
    by_dest = {unit.dest: unit for unit in component.units}
    for chk in component.check:
        if not chk.command:
            raise SpecError(
                f"component {component.name!r} has a check with an empty command"
            )
        used: set[str] = set()
        for token in chk.command:
            used |= _placeholders(token)
        stray = used - {"file"}
        if stray:
            raise SpecError(
                f"component {component.name!r} check command uses placeholder(s) "
                f"{sorted(stray)}; only {{file}} is allowed"
            )
        unit = by_dest.get(chk.target)
        if unit is None:
            raise SpecError(
                f"component {component.name!r} check target {chk.target!r} "
                f"matches no unit dest"
            )
        if unit.per_stage:
            raise SpecError(
                f"component {component.name!r} check target {chk.target!r} is a "
                f"per-stage unit; a check runs once and cannot target one"
            )

    for item in normalize_restart(component.restart):
        match item:
            case StageRestart(template):
                if not template:
                    raise SpecError(
                        f"component {component.name!r} has an empty StageRestart"
                    )
                if "{stage}" not in template:
                    raise SpecError(
                        f"component {component.name!r} StageRestart {template!r} "
                        f"must contain {{stage}}; use SharedRestart for a service "
                        f"shared across stages"
                    )
                stray = _placeholders(template) - {"stage"}
                if stray:
                    raise SpecError(
                        f"component {component.name!r} StageRestart {template!r} "
                        f"uses placeholder(s) {sorted(stray)}; only {{stage}} is "
                        f"allowed"
                    )
            case SharedRestart(unit_name):
                if not unit_name:
                    raise SpecError(
                        f"component {component.name!r} has an empty SharedRestart"
                    )
                if _placeholders(unit_name):
                    raise SpecError(
                        f"component {component.name!r} SharedRestart {unit_name!r} "
                        f"must not contain a placeholder"
                    )
            case _:
                assert_never(item)
