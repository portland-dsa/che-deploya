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
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import AbstractSet, Annotated, Mapping, Optional

from cyclopts import App, Parameter

from .. import ops
from ..model import FilePermissions
from ..selection import all_stages, select_specs, union_component_names
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


def _components_to_provision(
    selected: Mapping[str, DeploySpec], name: str
) -> list[tuple[DeploySpec, Component]]:
    """The (spec, component) pairs to provision for subcommand `name`.

    A subcommand name is the union of component names across all mounted specs,
    so a selected spec may not declare it.
    """
    pairs: list[tuple[DeploySpec, Component]] = []
    for spec_name, spec in selected.items():
        component = next((c for c in spec.components if c.name == name), None)
        if component is None:
            print(f"--spec {spec_name}: no component {name!r} to provision, skipping")
            continue
        pairs.append((spec, component))
    return pairs


def _check_shared_secrets_dir(
    secrets_dir: Optional[Path], selected: Mapping[str, DeploySpec]
) -> None:
    """Reject an explicit ``--secrets-dir`` shared across more than one spec.

    Each spec stages its secrets under its own ``/tmp/<root>`` by default, and
    the specs have distinct roots, so a single explicit directory pointed at
    several specs would mix their secrets - and with ``--self-destruct`` the
    first spec's cleanup would delete the directory out from under the next.
    Rather than silently destroy or mix, refuse the combination.
    """
    if secrets_dir is not None and len(selected) > 1:
        raise SystemExit(
            "--secrets-dir cannot be shared across multiple specs; omit it to "
            "stage each spec under its own /tmp/<root>, or select one spec."
        )


def build_provision(
    specs: Mapping[str, DeploySpec], SpecName: type[StrEnum] | None
) -> App:
    """Build the `provision` subapp for one or many specs.

    `SpecName is None` is the single-spec surface: one subcommand per component
    plus `all`, exactly as before. Otherwise a required, set-valued `--spec`
    selects among the mounted specs, the per-component subcommands are the union
    of component names, and every handler loops over the selected specs.
    """
    app = App(name="provision", help="Provision the box from its encrypted secrets.")
    if SpecName is None:
        _mount_single(app, next(iter(specs.values())))
    else:
        _mount_multi(app, specs, SpecName)
    return app


def _mount_single(app: App, spec: DeploySpec) -> None:
    """The original single-spec `provision` surface, unchanged."""

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


def _mount_multi(
    app: App, specs: Mapping[str, DeploySpec], SpecName: type[StrEnum]
) -> None:
    """The multi-spec `provision` surface: `--spec` selection over union commands."""

    spec_param = Annotated[frozenset[SpecName], Parameter(negative_iterable="")]

    def _default(
        *,
        spec,
        targets: frozenset[Stages] = frozenset(all_stages(specs)),
        secrets_dir: Optional[Path] = None,
        bundled_secrets: bool = False,
        self_destruct: bool = False,
    ) -> None:
        selected = select_specs(specs, spec)
        _check_shared_secrets_dir(secrets_dir, selected)
        for chosen in selected.values():
            provision_all(
                chosen,
                targets=targets,
                secrets_dir=secrets_dir or ops.staged_secrets(chosen.root),
                bundled_secrets=bundled_secrets,
                self_destruct=self_destruct,
            )

    _default.__annotations__["spec"] = spec_param
    app.default(_default)
    app.command(_default, name="all")

    def _make_component_command(name: str):
        def _one(
            *,
            spec,
            targets: frozenset[Stages] = frozenset(all_stages(specs)),
            secrets_dir: Optional[Path] = None,
        ) -> None:
            selected = select_specs(specs, spec)
            _check_shared_secrets_dir(secrets_dir, selected)
            pairs = _components_to_provision(selected, name)
            if len(pairs) == 0:
                raise SystemExit(
                    f"none of the selected specs declare a {name!r} component"
                )
            for chosen, component in pairs:
                _provision_component(
                    chosen,
                    component,
                    stages=targets,
                    secrets_dir=secrets_dir or ops.staged_secrets(chosen.root),
                )

        _one.__annotations__["spec"] = spec_param
        return _one

    for name in union_component_names(specs):
        app.command(
            _make_component_command(name),
            name=name,
            help=f"Provision the {name} component.",
        )
