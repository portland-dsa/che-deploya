# che_deploya/provision/systemd.py
"""``provision systemd``: install each component's unit files.

A ``StaticUnit`` is copied verbatim; a ``TemplatedUnit`` has its ``Environment``
decrypted and substituted into the template (``string.Template``: only ``${...}``
placeholders change). The collect/substitute/install shape is recycled from the
original ``_render``/``_collect``/``_install_static``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from string import Template
from typing import AbstractSet, Callable, Dict, List, Tuple, assert_never

from .. import ops
from ..model import FilePermissions
from ..spec import (
    Component,
    DeploySpec,
    Environment,
    SecretSource,
    SharedRestart,
    StageRestart,
    Stages,
    TemplatedUnit,
    Unit,
    normalize_restart,
)
from ..templating import resolve
from .creds import secret_file_for

ReadAsset = Callable[[str | None, str], bytes]

Rendered = Tuple[Path, bytes, Unit]


class CheckFailed(Exception):
    """A component's staged-config check exited nonzero; nothing was installed."""


def provision_systemd(
    spec: DeploySpec,
    component: Component,
    *,
    stages: AbstractSet[Stages],
    secrets_dir: Path,
    read_asset: ReadAsset,
    staged_dir: Path | None = None,
) -> None:
    """Install every unit for ``component``, checking candidates before install.

    When ``component.check`` is non-empty the rendered bytes are first written to
    a staging tree (``staged_dir``, a per-run ``mkdtemp`` by default) and each
    check is run against it; a nonzero exit raises :class:`CheckFailed` with
    nothing installed. Then the files are installed and systemd is reloaded.
    """
    ops.prepare(secrets_dir)
    active = frozenset(stages)

    collected = _render(spec, component, active, secrets_dir, read_asset)

    if component.check:
        _stage_and_check(spec, component, collected, staged_dir)

    for dest, data, unit in collected:
        ops.install_dir(dest.parent, unit.dir_mode, keep_existing=True)
        ops.install_file(data, dest, unit.file_mode)
        print(f"ok: {dest}")

    ops.daemon_reload()

    _restart(component, active)


def _restart(component: Component, active: frozenset[Stages]) -> None:
    """Restart each declared unit after install, in declared order.

    A :class:`StageRestart` expands to one restart per active stage (enum order);
    a :class:`SharedRestart` restarts once. A failure raises loudly - the
    installed files stay, since they passed their check.
    """
    for item in normalize_restart(component.restart):
        match item:
            case StageRestart(template):
                for stage in (s for s in Stages if s in active):
                    unit = resolve(template, stage=stage)
                    ops.run(["systemctl", "restart", unit])
                    print(f"restarted: {unit}")
            case SharedRestart(unit):
                ops.run(["systemctl", "restart", unit])
                print(f"restarted: {unit}")
            case _:
                assert_never(item)


def _stage_and_check(
    spec: DeploySpec,
    component: Component,
    collected: List[Rendered],
    staged_dir: Path | None,
) -> None:
    """Mirror the rendered files into a tree and run each check against it.

    The tree mirrors real destination paths (so a config that imports fragments
    by a relative path resolves the same way it will once installed). ``{file}``
    in a check command resolves to the staged path of that check's ``target``.
    The tree is 0700 (root-owned, since provisioning runs as root) and removed
    unconditionally afterward. A nonzero check exit raises :class:`CheckFailed`.
    """
    root = (
        staged_dir
        if staged_dir is not None
        else Path(tempfile.mkdtemp(prefix=f"{spec.root}-stage-"))
    )
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(FilePermissions.PrivateDir)
    try:
        for dest, data, _unit in collected:
            staged = root / dest.relative_to(dest.anchor)
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(data)
        for chk in component.check:
            target = Path(resolve(chk.target, root=spec.root, component=component.name))
            file_path = str(root / target.relative_to(target.anchor))
            cmd = [resolve(token, file=file_path) for token in chk.command]
            result = ops.run(cmd, check=False, capture=True)
            if result.returncode != 0:
                raise CheckFailed(
                    f"component {component.name!r} check failed "
                    f"(exit {result.returncode}): {' '.join(cmd)}"
                )
            print(f"check ok: {' '.join(cmd)}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _render(
    spec: DeploySpec,
    component: Component,
    active: frozenset[Stages],
    secrets_dir: Path,
    read_asset: ReadAsset,
) -> List[Rendered]:
    """Render every unit x stage into ``(dest, data, unit)`` tuples.

    No installation happens here - splitting render from install lets a check run
    against the rendered bytes before any of them reach their destinations. Each
    tuple carries the resolved ``dest``, the rendered bytes, and the ``unit`` its
    file/dir modes come from.
    """
    collected: List[Rendered] = []
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
            collected.append((dest, data, unit))
    return collected


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
