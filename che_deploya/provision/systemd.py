# che_deploya/provision/systemd.py
"""``provision systemd``: install each component's unit files.

A ``StaticUnit`` is copied verbatim; a ``TemplatedUnit`` has its ``Environment``
decrypted and substituted into the template (``string.Template``: only ``${...}``
placeholders change). The collect/substitute/install shape is recycled from the
original ``_render``/``_collect``/``_install_static``.
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import AbstractSet, Callable, Dict

from .. import ops
from ..spec import (
    Component,
    DeploySpec,
    Environment,
    SecretSource,
    Stages,
    TemplatedUnit,
)
from ..templating import resolve
from .creds import secret_file_for

ReadAsset = Callable[[str | None, str], bytes]


def provision_systemd(
    spec: DeploySpec,
    component: Component,
    *,
    stages: AbstractSet[Stages],
    secrets_dir: Path,
    read_asset: ReadAsset,
) -> None:
    """Install every unit for ``component`` over ``stages``, then reload systemd."""
    ops.prepare(secrets_dir)
    active = frozenset(stages)

    for unit in component.units:
        targets = active if unit.per_stage else frozenset({next(iter(active))})
        for stage in targets:
            resource_loc = (
                None
                if unit.resource_loc is None
                else resolve(
                    unit.resource_loc,
                    root=spec.root,
                    component=component.name,
                    stage=stage,
                )
            )
            src = resolve(
                unit.src,
                repo_root="",
                root=spec.root,
                component=component.name,
                stage=stage,
            )
            data = read_asset(resource_loc, src)
            if isinstance(unit, TemplatedUnit):
                env = _collect_env(spec, component, unit.env, stage, secrets_dir)
                data = Template(data.decode("utf-8")).substitute(env).encode("utf-8")
            dest = Path(
                resolve(
                    unit.dest, root=spec.root, component=component.name, stage=stage
                )
            )
            ops.install_dir(dest.parent, unit.dir_mode, keep_existing=True)
            ops.install_file(data, dest, unit.file_mode)
            print(f"ok: {dest}")

    ops.daemon_reload()


def _collect_env(
    spec: DeploySpec,
    component: Component,
    env: Environment,
    stage: Stages,
    secrets_dir: Path,
) -> Dict[str, str]:
    """Decrypt this stage's environment values into a ``{key: value}`` mapping."""
    if env.src is SecretSource:
        assert component.secrets is not None
        resolver = secret_file_for(spec, component, secrets_dir)
    else:
        src = str(env.src)

        def _resolver(s: Stages) -> Path:
            return (
                secrets_dir
                / Path(
                    resolve(
                        src,
                        repo_root="",
                        root=spec.root,
                        component=component.name,
                        stage=s,
                    )
                ).name
            )

        # silences annoying pylance type error
        resolver = _resolver

    values: Dict[str, str] = {}
    for decrypted in ops.SecretsIter(
        [stage], resolver, list(env.names), exceptions=dict(env.exclude)
    ):
        values[str(decrypted.token_name)] = decrypted.value.decode("utf-8").strip()
    return values
