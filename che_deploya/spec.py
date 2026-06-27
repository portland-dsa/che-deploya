# che_deploya/spec.py
"""The declarative model a project's ``spec.py`` builds.

These frozen dataclasses describe *what* a project deploys - its stages,
components, secrets, environment values, unit files, and database. The generic
verb handlers interpret this model; nothing here does I/O. See
``docs/specs/2026-06-27-deploy-framework-design.md`` for the rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import AbstractSet, Final, Mapping, Sequence, Union

from .model import FilePermissions


class Stages(StrEnum):
    """The deploy stages this model supports.

    Framework-owned: the model is fixed at staging plus production. A
    ``StrEnum`` member is its own wire name, so the same value is a CLI choice, a
    path segment, the ``{stage}`` substitution, and the credential group suffix.
    """

    Staging = "staging"
    Production = "production"


class _SecretSource:
    """The type of the :data:`SecretSource` sentinel (see it for usage)."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "SecretSource"


SecretSource: Final = _SecretSource()
"""Default for :attr:`Environment.src`: read from the component's ``Secret`` file.

Environment values and secrets share one encrypted file in this deploy model, so
an ``Environment`` with no explicit ``src`` reads the same file its component's
``Secret`` does.
"""


@dataclass(frozen=True)
class Secret:
    """The encrypted tokens a component installs as systemd credentials.

    ``names`` is the set of secret-token enum members to install; ``exclude``
    drops specific tokens on specific stages. ``src`` is the encrypted file's
    source-tree path (``{repo_root}``/``{stage}`` placeholders). Each token is
    written to ``<dest_dir>/<token>.cred``: the ``<token>.cred`` filename is
    fixed (a unit's ``LoadCredentialEncrypted=`` references it), while
    ``dest_dir`` is overridable and defaults to ``/etc/{root}/{component}/{stage}``.
    """

    names: AbstractSet[StrEnum]
    src: str
    exclude: Mapping[Stages, AbstractSet[StrEnum]] = field(default_factory=dict)
    dest_dir: str = "/etc/{root}/{component}/{stage}"
    resource_loc: str | None = None


@dataclass(frozen=True)
class Environment:
    """The non-secret ``Environment=`` keys a ``TemplatedUnit`` fills in.

    ``src`` defaults to :data:`SecretSource` (the containing component's
    ``Secret`` file). There is no destination: values substitute into the
    ``TemplatedUnit`` that holds this ``Environment``.
    """

    names: AbstractSet[StrEnum]
    src: Union[str, _SecretSource] = SecretSource
    exclude: Mapping[Stages, AbstractSet[StrEnum]] = field(default_factory=dict)


@dataclass(frozen=True)
class StaticUnit:
    """A unit file copied into place verbatim."""

    src: str
    dest: str
    resource_loc: str | None = None
    mode: FilePermissions = FilePermissions.WorldConfig
    per_stage: bool = False


@dataclass(frozen=True)
class TemplatedUnit:
    """A unit file rendered from a template, its ``${...}`` filled from ``env``."""

    src: str
    dest: str
    env: Environment
    resource_loc: str | None = None
    mode: FilePermissions = FilePermissions.WorldConfig
    per_stage: bool = False


Unit = Union[StaticUnit, TemplatedUnit]


@dataclass(frozen=True)
class Component:
    """One deployable layer: its secrets, units, and optional database.

    Environment values are not a field here - they live on the ``TemplatedUnit``
    that consumes them. ``stages`` of ``None`` means "use the spec's stages".
    """

    name: str
    secrets: Secret | None = None
    units: Sequence[Unit] = ()
    db: "Db | None" = None
    stages: AbstractSet[Stages] | None = None


@dataclass(frozen=True)
class DeploySpec:
    """The complete declaration ``build_cli`` interprets into a CLI."""

    root: str
    package: str
    components: Sequence[Component]
    stages: AbstractSet[Stages] = frozenset(Stages)
    repo_root: str | None = None
    cli_name: str | None = None

    def active_stages(self, component: Component) -> frozenset[Stages]:
        """The stages in scope for ``component`` (its own set, else the spec's)."""
        return frozenset(component.stages if component.stages is not None else self.stages)


from .db import Db  # noqa: E402  - resolves the Component.db forward reference
