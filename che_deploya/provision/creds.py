# che_deploya/provision/creds.py
"""``provision creds``: install systemd credentials from the encrypted secrets.

For each component with a ``Secret``, decrypt its tokens out of the per-stage
encrypted file and re-encrypt each into a systemd credential under
``<dest_dir>/<token>.cred``. The plaintext flows ``sops`` -> ``systemd-creds``
over a pipe and never touches argv or disk. The per-instance install loop is
recycled from the original ``_provision_instances``.

Box-side: escalates to root and reads real secrets.
"""

from __future__ import annotations

from pathlib import Path
from typing import AbstractSet, Callable

from .. import ops
from ..model import FilePermissions
from ..spec import Component, DeploySpec, Stages
from ..templating import resolve


def secret_file_for(
    spec: DeploySpec, component: Component, secrets_dir: Path
) -> Callable[[Stages], Path]:
    """A resolver from stage to that stage's staged encrypted file.

    The staged file keeps the basename of the component's ``Secret.src`` (with
    ``{stage}`` substituted), so a per-stage ``<stage>.enc.yaml`` resolves to
    ``<secrets_dir>/<stage>.enc.yaml`` - matching how ``redeploy`` extracts them.
    """
    assert component.secrets is not None
    src = component.secrets.src

    def resolver(stage: Stages) -> Path:
        name = Path(resolve(src, repo_root="", root=spec.root,
                            component=component.name, stage=stage)).name
        return secrets_dir / name

    return resolver


def provision_creds(
    spec: DeploySpec, component: Component, *, stages: AbstractSet[Stages], secrets_dir: Path
) -> None:
    """Install every credential for ``component`` over ``stages``."""
    secret = component.secrets
    if secret is None:
        return
    ops.prepare(secrets_dir)

    resolver = secret_file_for(spec, component, secrets_dir)
    iterator = ops.SecretsIter(
        list(stages), resolver, list(secret.names), exceptions=dict(secret.exclude)
    )
    for extracted in iterator:
        stage = extracted.target
        print(f"provisioning {extracted.token_name} for {component.name} on {stage}")
        dest_dir = Path(
            resolve(secret.dest_dir, root=spec.root, component=component.name, stage=stage)
        )
        owner = ops.service(spec.root, stage)
        ops.install_dir(dest_dir, FilePermissions.GroupDir, owner)
        dest = dest_dir / f"{extracted.token_name}.cred"
        ops.creds_encrypt(extracted.token_name, extracted.value, dest, owner)
        print(f"ok: {dest}")
