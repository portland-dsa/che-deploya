"""``build_cli``: turn a ``DeploySpec`` into the root provisioning CLI."""

from __future__ import annotations

from cyclopts import App

from .provision import build_provision
from .redeploy import build_redeploy
from .spec import DeploySpec
from .validate import validate


def build_cli(spec: DeploySpec) -> App:
    """Validate ``spec`` and build its root cyclopts app (``provision`` + ``redeploy``)."""
    validate(spec)
    app = App(
        name=spec.cli_name or f"{spec.root}-deploy",
        help="Provision and deploy from encrypted secrets.",
    )
    app.command(build_provision(spec))
    app.command(build_redeploy(spec))
    return app
