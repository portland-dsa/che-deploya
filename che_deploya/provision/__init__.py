# che_deploya/provision/__init__.py
"""The ``provision`` verb: a subcommand per component, plus ``all``.

Each component in the spec becomes ``provision <component>``.

``provision all`` runs every component. ``--bundled-secrets``

self-extracts the secrets shipped inside the .pyz (recycled from the original
``_extract_bundled_secrets``); ``--self-destruct`` removes the staged secrets
afterward.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path
from typing import AbstractSet, Optional

from cyclopts import App

from .. import ops
from ..model import FilePermissions
from ..spec import Component, DeploySpec, Stages
from ..templating import resolve
from .creds import provision_creds
from .database import provision_db
from .systemd import ReadAsset, provision_systemd


def read_asset_for(spec: DeploySpec) -> ReadAsset:
    """Read a unit asset from the bundled package, falling back to the source tree.

    On the box the bytes live at ``resource_loc`` inside the shipped package; from
    a local source checkout they live at ``src``. Trying the package resource
    first and the tree second lets both work.
    """

    def read_asset(resource_loc: Optional[str], src: str) -> bytes:
        loc = resource_loc if resource_loc is not None else f"assets/{Path(src).name}"
        res = resources.files(spec.package).joinpath(loc)
        if res.is_file():
            return res.read_bytes()
        return Path(src).read_bytes()

    return read_asset


def _provision_component(
    spec: DeploySpec,
    component: Component,
    *,
    stages: AbstractSet[Stages],
    secrets_dir: Path,
) -> None:
    read_asset = read_asset_for(spec)
    active = frozenset(stages) & spec.active_stages(component)
    if not active:
        return
    provision_creds(spec, component, stages=active, secrets_dir=secrets_dir)
    provision_systemd(
        spec, component, stages=active, secrets_dir=secrets_dir, read_asset=read_asset
    )
    provision_db(spec, component, stages=active, secrets_dir=secrets_dir)


def provision_all(
    spec: DeploySpec,
    *,
    targets: AbstractSet[Stages],
    secrets_dir: Path,
    bundled_secrets: bool = False,
    self_destruct: bool = False,
) -> None:
    """Provision every component for ``targets`` (same flow as the original ``all``)."""
    if bundled_secrets:
        _extract_bundled_secrets(spec, targets, secrets_dir)
    try:
        for component in spec.components:
            _provision_component(
                spec, component, stages=targets, secrets_dir=secrets_dir
            )
    finally:
        if self_destruct:
            shutil.rmtree(secrets_dir, ignore_errors=True)
            print(f"self-destruct: removed {secrets_dir}")


def _extract_bundled_secrets(
    spec: DeploySpec, targets: AbstractSet[Stages], secrets_dir: Path
) -> None:
    """Unpack the per-stage ``.enc.yaml`` bundled into this archive into ``secrets_dir``.

    Recycled from the original tool: escalate first so the dir is root:root 0700
    (which ``assert_trusted`` then accepts), then materialize only the requested
    stages' files from each component's bundled secret.
    """
    ops.ensure_root()
    secrets_dir = Path(secrets_dir)
    ops.install_dir(secrets_dir, FilePermissions.PrivateDir)
    bundle = resources.files(spec.package).joinpath(ops.BUNDLED_SECRETS_DIR)
    seen: set[str] = set()
    for component in spec.components:
        if component.secrets is None:
            continue
        for stage in frozenset(targets) & spec.active_stages(component):
            name = Path(
                resolve(
                    component.secrets.src,
                    repo_root="",
                    root=spec.root,
                    component=component.name,
                    stage=stage,
                )
            ).name
            if name in seen:
                continue
            seen.add(name)
            ops.install_file(
                bundle.joinpath(name).read_bytes(),
                secrets_dir / name,
                FilePermissions.Private,
            )
            print(f"extracted bundled secret -> {secrets_dir / name}")


def build_provision(spec: DeploySpec) -> App:
    """Build the ``provision`` subapp: one subcommand per component, plus ``all``."""
    app = App(name="provision", help="Provision the box from its encrypted secrets.")

    def _default(
        *,
        targets: frozenset[Stages] = frozenset(spec.stages),
        secrets_dir: Path = ops.staged_secrets(spec.root),
        bundled_secrets: bool = False,
        self_destruct: bool = False,
    ) -> None:
        provision_all(
            spec,
            targets=targets,
            secrets_dir=secrets_dir,
            bundled_secrets=bundled_secrets,
            self_destruct=self_destruct,
        )

    app.default(_default)
    app.command(_default, name="all")

    # cyclopts maps EVERY function parameter to a CLI argument, so the per-component
    # command must capture `component` by closure - never as a parameter (a `component`
    # parameter would surface as a broken CLI option). A factory gives each command its
    # own binding without exposing it.
    def _make_component_command(component: Component):
        def _one(
            *,
            targets: frozenset[Stages] = frozenset(spec.stages),
            secrets_dir: Path = ops.staged_secrets(spec.root),
        ) -> None:
            _provision_component(
                spec, component, stages=targets, secrets_dir=secrets_dir
            )

        return _one

    for component in spec.components:
        app.command(
            _make_component_command(component),
            name=component.name,
            help=f"Provision the {component.name} component.",
        )

    return app
