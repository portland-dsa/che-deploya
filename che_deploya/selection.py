# che_deploya/selection.py
"""Resolve a `--spec` selection against the specs mounted on one CLI."""

from __future__ import annotations

from enum import StrEnum
from typing import AbstractSet, Mapping, cast

from cyclopts.utils import default_name_transform

from .spec import DeploySpec, Stages
from .validate import SpecError


def normalize_specs(
    specs: Mapping[str, DeploySpec] | DeploySpec,
) -> dict[str, DeploySpec]:
    """Coerce either accepted `build_cli` argument into a name-to-spec dict.

    A bare `DeploySpec` is the single-spec case every current caller passes; it
    becomes a one-entry dict whose key never surfaces, because a single spec
    mounts no `--spec`. A mapping is copied so the caller's object is not aliased,
    and an empty mapping raises `SpecError` - a CLI over no specs has nothing to
    do. Key validity is checked later, in `spec_name_enum`, where a key actually
    becomes a CLI token.
    """
    if isinstance(specs, DeploySpec):
        return {specs.cli_name or specs.root: specs}
    mapping = dict(specs)
    if not mapping:
        raise SpecError("build_cli requires at least one spec")
    return mapping


def spec_name_enum(names: Mapping[str, DeploySpec]) -> type[StrEnum]:
    """Build the `StrEnum` of valid `--spec` names from the mounted specs.

    Cyclopts matches a choice by enum *member name*, so each spec name is used as
    both the member name and its value: the same string is the `--help` choice,
    the token `--spec` accepts, and the name `redeploy` passes to the box. This
    mirrors how `Stages` doubles as a CLI choice type. A key that is not a valid
    identifier cannot be an enum member name, so it raises `SpecError` rather than
    a cryptic enum error.

    Cyclopts does not match a `--spec` token literally: it runs both the member
    names and the token through `default_name_transform` (camelCase to snake, then
    lower, then `_` to `-`) and compares the results, returning the *first* member
    that matches. Two keys with distinct casing but the same transform (``web_api``
    and ``webApi`` both fold to ``web-api``) would therefore make one spec
    unreachable and, on redeploy, silently ship one spec's archive to provision
    the other. Folding on the same transform catches that collision here and
    raises `SpecError`, so an ambiguous vocabulary never reaches the box. Some
    valid identifiers are still reserved enum member names (``mro`` and the like);
    those surface from the constructor as a `ValueError`, re-raised as `SpecError`.
    """
    seen: dict[str, str] = {}
    for name in names:
        if not name.isidentifier():
            raise SpecError(f"spec name {name!r} is not a valid identifier")
        folded = default_name_transform(name)
        if folded in seen:
            raise SpecError(
                f"spec names {seen[folded]!r} and {name!r} collide to {folded!r}; "
                f"cyclopts resolves --spec choices by their transformed name"
            )
        seen[folded] = name
    try:
        return cast(type[StrEnum], StrEnum("SpecName", {name: name for name in names}))
    except ValueError as exc:
        raise SpecError(f"spec name reserved by the enum machinery: {exc}") from exc


def select_specs(
    specs: Mapping[str, DeploySpec], chosen: AbstractSet[StrEnum]
) -> dict[str, DeploySpec]:
    """The name-to-spec subset the `--spec` names picked, in `specs` order.

    `chosen` holds `SpecName` members whose values are keys of `specs`; the result
    keeps `specs`' iteration order so a multi-spec command acts on its specs in a
    stable sequence.
    """
    picked = {member.value for member in chosen}
    return {name: spec for name, spec in specs.items() if name in picked}


def all_stages(specs: Mapping[str, DeploySpec]) -> frozenset[Stages]:
    """The union of every mounted spec's stages - the `--targets` default in
    multi-spec mode, since each spec re-intersects it with its own active set."""
    return frozenset().union(*(spec.stages for spec in specs.values()))


def union_component_names(specs: Mapping[str, DeploySpec]) -> list[str]:
    """Every component name across all specs, de-duplicated, first-seen order.

    These become the per-component subcommand names in multi-spec mode; a name
    two specs share collapses to one subcommand that `--spec` then disambiguates.
    """
    names: list[str] = []
    for spec in specs.values():
        for component in spec.components:
            if component.name not in names:
                names.append(component.name)
    return names
