# che_deploya/redeploy.py
"""``redeploy``: bundle this tool (framework + project spec + assets + secrets)
into a .pyz, ship it to the box, and re-provision over SSH.

Workstation-side. The ssh/scp/mktemp flow and the prebuilt-.pyz refusal are
recycled from the original tool; the bundle step is generalized to vendor the
``che-deploya`` framework and to pull the project's assets/secrets - which live
outside the package in the source tree - into the package inside the archive.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import zipapp
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Tuple

from cyclopts import App

from . import ops
from .spec import DeploySpec, Stages
from .templating import resolve


def _repo_root(spec: DeploySpec, pkg_dir: Path) -> str:
    if spec.repo_root is not None:
        return spec.repo_root
    return ops.run(["git", "rev-parse", "--show-toplevel"],
                   capture=True, cwd=str(pkg_dir)).stdout.strip()


def _package_dir(spec: DeploySpec) -> Path:
    found = importlib.util.find_spec(spec.package)
    assert found is not None and found.origin is not None, f"cannot locate package {spec.package!r}"
    return Path(found.origin).resolve().parent


def bundle(spec: DeploySpec) -> Tuple[Path, TemporaryDirectory]:
    """Build a fresh .pyz with the framework, the project package, its assets, and secrets."""
    if zipfile.is_zipfile(sys.argv[0]):
        raise RuntimeError(
            "redeploy must run from a source checkout, not a prebuilt .pyz: it bundles the "
            "current assets and encrypted secrets into a fresh archive, which a running .pyz cannot do."
        )

    pkg_dir = _package_dir(spec)
    repo_root = _repo_root(spec, pkg_dir)
    out = TemporaryDirectory(prefix=f"{spec.root}-deploy-pyz-")
    pyz = Path(out.name) / f"{spec.root}-deploy.pyz"

    with TemporaryDirectory(prefix=f"{spec.root}-deploy-src-") as src:
        # Vendor che_deploya (a git dependency) + cyclopts into the bundle. `uv pip install
        # <name>` does NOT read the consuming project's [tool.uv.sources], so a git-sourced
        # che-deploya would never resolve from there. Instead export the locked requirements -
        # which carry the resolved git URL - and install from that file. This is uv's standard
        # "vendor deps into a target dir" recipe. `cwd` is the consuming project dir that holds
        # pyproject.toml + uv.lock (the package dir, as in the original tool). requirements.txt
        # is written to the OUTER temp dir so it is not swept into the archive.
        req = Path(out.name) / "requirements.txt"
        ops.run(["uv", "export", "--frozen", "--no-dev", "--no-editable", "--no-emit-project",
                 "-o", str(req)], cwd=str(pkg_dir))
        ops.run(["uv", "pip", "install", "--target", src, "-r", str(req)], cwd=str(pkg_dir))
        dest_pkg = Path(src) / spec.package
        shutil.copytree(pkg_dir, dest_pkg,
                        ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyz"))
        _bundle_assets(spec, repo_root, dest_pkg)
        zipapp.create_archive(
            src, target=pyz, main=f"{spec.package}.cli:main",
            interpreter="/usr/bin/env python3", compressed=True,
        )

    return pyz, out


def _bundle_assets(spec: DeploySpec, repo_root: str, dest_pkg: Path) -> None:
    """Copy each unit asset and per-stage secret from the tree into the staged package."""
    bundled_secrets = dest_pkg / ops.BUNDLED_SECRETS_DIR
    bundled_secrets.mkdir(exist_ok=True)
    seen_secret: set[str] = set()

    for component in spec.components:
        active = spec.active_stages(component)
        for unit in component.units:
            stages = active if unit.per_stage else {next(iter(active))}
            for stage in stages:
                src_path = Path(resolve(unit.src, repo_root=repo_root, root=spec.root,
                                        component=component.name, stage=stage))
                rel = resolve(unit.resource_loc or f"assets/{src_path.name}",
                              root=spec.root, component=component.name, stage=stage)
                target = dest_pkg / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, target)
        if component.secrets is not None:
            for stage in active:
                src_path = Path(resolve(component.secrets.src, repo_root=repo_root, root=spec.root,
                                        component=component.name, stage=stage))
                if src_path.name in seen_secret:
                    continue
                seen_secret.add(src_path.name)
                shutil.copy2(src_path, bundled_secrets / src_path.name)


def build_redeploy(spec: DeploySpec) -> App:
    app = App(name="redeploy",
              help="Bundle this tool and its secrets into a .pyz, ship it, and provision over SSH.")

    @app.default
    def _run(*, host: str, user: Optional[str] = None,
             targets: frozenset[Stages] = frozenset(spec.stages)) -> None:
        # Recycled verbatim from the original _run, with spec-derived names.
        dest = f"{user}@{host}" if user is not None else host
        pyz, tmp = bundle(spec)
        try:
            stage = ops.run(["ssh", dest, "mktemp -d"], capture=True).stdout.strip()
            ops.scp(pyz, host, user=user, target_dir=stage)
            targets_flag = " ".join(f"--targets {t.value}" for t in targets)
            remote = (
                f'sudo python3 "{stage}/{pyz.name}" provision '
                f"--bundled-secrets --self-destruct {targets_flag}"
                f'; rc=$?; rm -rf -- "{stage}"; exit $rc'
            )
            ops.run(["ssh", "-tt", dest, remote])
        finally:
            tmp.cleanup()

    return app
