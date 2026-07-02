# che_deploya/spec.py
"""The declarative model a project's ``spec.py`` builds.

These frozen dataclasses describe *what* a project deploys - its stages,
components, secrets, environment values, unit files, and database. The generic
verb handlers interpret this model; nothing here does I/O.
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
    """A unit file copied into place verbatim.

    ``file_mode`` is the mode of the installed file. ``dir_mode`` is applied to the
    parent directory only when it has to be created - an existing parent (such as
    ``/etc/systemd/system`` or ``/usr/local/sbin``) is left exactly as it is.
    """

    src: str
    dest: str
    resource_loc: str | None = None
    file_mode: FilePermissions = FilePermissions.WorldConfig
    dir_mode: FilePermissions = FilePermissions.WorldDir
    per_stage: bool = False


@dataclass(frozen=True)
class TemplatedUnit:
    """A unit file rendered from a template, its ``${...}`` filled from ``env``.

    ``file_mode`` and ``dir_mode`` behave as on :class:`StaticUnit`: ``file_mode``
    modes the rendered file, ``dir_mode`` the parent only when it must be created.
    """

    src: str
    dest: str
    env: Environment
    resource_loc: str | None = None
    file_mode: FilePermissions = FilePermissions.WorldConfig
    dir_mode: FilePermissions = FilePermissions.WorldDir
    per_stage: bool = False


Unit = Union[StaticUnit, TemplatedUnit]


@dataclass(frozen=True)
class Check:
    """A validation command run against the staged candidate files, before install.

    ``command`` is an argv sequence whose single placeholder ``{file}`` resolves
    to the staged path of ``target`` - a ``dest`` the component itself owns. The
    check runs once, against a tree that already holds every unit of the
    component, so a config that imports fragments by a relative path validates
    the same bytes that will serve.
    """

    command: Sequence[str]
    target: str


@dataclass(frozen=True)
class StageRestart:
    """A per-stage service instance to restart, e.g. ``"app@{stage}.service"``.

    ``template`` must contain ``{stage}``; it expands to one restart per target
    stage. Use it only for genuinely distinct per-stage units - a service shared
    across stages is a :class:`SharedRestart`.
    """

    template: str


@dataclass(frozen=True)
class SharedRestart:
    """A single box-wide service to restart exactly once, e.g. ``"caddy.service"``.

    ``unit`` is literal - no placeholders - because the whole point is that one
    shared process is cycled once, not once per stage.
    """

    unit: str


RestartItem = StageRestart | SharedRestart
"""One entry in a component's ``restart`` list: per-stage or shared."""


def normalize_restart(
    restart: RestartItem | Sequence[RestartItem],
) -> tuple[RestartItem, ...]:
    """Coerce the single-or-sequence ``restart`` field into a tuple.

    A bare ``StageRestart``/``SharedRestart`` is wrapped; any other sequence is
    passed through as a tuple, preserving declared order.
    """
    if isinstance(restart, (StageRestart, SharedRestart)):
        return (restart,)
    return tuple(restart)


@dataclass(frozen=True)
class Component:
    """One deployable layer: its secrets, units, and optional database.

    Environment values are not a field here - they live on the ``TemplatedUnit``
    that consumes them. ``stages`` of ``None`` means "use the spec's stages".
    ``check`` validates the staged candidate files before install; ``restart``
    cycles services after install. Both default to today's behavior.
    """

    name: str
    secrets: Secret | None = None
    units: Sequence[Unit] = ()
    db: "Db | None" = None
    stages: AbstractSet[Stages] | None = None
    check: Sequence[Check] = ()
    restart: RestartItem | Sequence[RestartItem] = ()


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
        return frozenset(
            component.stages if component.stages is not None else self.stages
        )


from .db import Db  # noqa: E402  - resolves the Component.db forward reference
