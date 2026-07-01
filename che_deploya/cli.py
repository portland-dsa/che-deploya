"""``build_cli``: turn one or many ``DeploySpec``s into the root provisioning CLI."""

from __future__ import annotations

from typing import Mapping

from cyclopts import App

from .provision import build_provision
from .redeploy import build_redeploy
from .selection import normalize_specs, spec_name_enum
from .spec import DeploySpec
from .validate import validate

_HELP = "Provision and deploy from encrypted secrets."


def build_cli(
    specs: Mapping[str, DeploySpec] | DeploySpec, *, name: str | None = None
) -> App:
    """Validate the spec(s) and build the root cyclopts app.

    Accepts a single ``DeploySpec`` (the common case, unchanged) or a mapping of
    name to spec. With one spec the surface is exactly as before. With more than
    one, a required ``--spec`` selector is added to ``provision`` and ``redeploy``,
    its choices being the mapping's keys.
    """
    mapping = normalize_specs(specs)
    for spec in mapping.values():
        validate(spec)

    if len(mapping) == 1:
        (only,) = mapping.values()
        app = App(name=name or only.cli_name or f"{only.root}-deploy", help=_HELP)
        app.command(build_provision(mapping, None))
        app.command(build_redeploy(mapping, None))
        return app

    spec_name = spec_name_enum(mapping)
    first = next(iter(mapping.values()))
    app = App(name=name or f"{first.root}-deploy", help=_HELP)
    app.command(build_provision(mapping, spec_name))
    app.command(build_redeploy(mapping, spec_name))
    return app
