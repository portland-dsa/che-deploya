import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from che_deploya import Component, DeploySpec, StaticUnit, Stages
from che_deploya.provision import systemd as sysmod
from che_deploya.provision.systemd import provision_systemd, CheckFailed
from che_deploya.spec import Check, StageRestart, SharedRestart


def _read_asset(resource_loc, src):
    return b"UNIT-BYTES"


def _spec(component, stages=frozenset({Stages.Staging})):
    return DeploySpec(root="app", package="p", components=[component], stages=stages)


@pytest.fixture
def env(monkeypatch):
    """Patch the ops surface; record installs and subprocess calls.

    `rc_queue` lets a test dictate the return code of successive `ops.run`
    calls; a nonzero code with the default `check=True` raises, matching real
    `ops.run`.
    """
    installs: list[tuple[Path, bytes]] = []
    calls: list[list[str]] = []
    rc_queue: list[int] = []

    monkeypatch.setattr(sysmod.ops, "prepare", lambda d: None)
    monkeypatch.setattr(
        sysmod.ops, "daemon_reload", lambda: calls.append(["daemon-reload"])
    )
    monkeypatch.setattr(sysmod.ops, "install_dir", lambda *a, **k: None)
    monkeypatch.setattr(
        sysmod.ops,
        "install_file",
        lambda data, dest, mode, *a, **k: installs.append((Path(dest), data)),
    )

    def _run(cmd, *, check=True, capture=False, text=True, **kw):
        calls.append(list(cmd))
        rc = rc_queue.pop(0) if rc_queue else 0
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)
        return SimpleNamespace(returncode=rc, stdout=b"", stderr=b"")

    monkeypatch.setattr(sysmod.ops, "run", _run)
    return SimpleNamespace(installs=installs, calls=calls, rc_queue=rc_queue)


def test_static_units_install_then_daemon_reload(env, tmp_path):
    component = Component(
        name="app",
        units=[
            StaticUnit(src="a", dest="/etc/systemd/system/app@.service"),
            StaticUnit(src="b", dest="/etc/systemd/system/app.socket"),
        ],
    )
    provision_systemd(
        _spec(component),
        component,
        stages={Stages.Staging},
        secrets_dir=tmp_path / "secrets",
        read_asset=_read_asset,
    )
    assert [dest for dest, _ in env.installs] == [
        Path("/etc/systemd/system/app@.service"),
        Path("/etc/systemd/system/app.socket"),
    ]
    assert ["daemon-reload"] in env.calls


def _caddy(check_cmd):
    return Component(
        name="caddy",
        units=[StaticUnit(src="a", dest="/etc/caddy/Caddyfile")],
        check=[Check(command=check_cmd, target="/etc/caddy/Caddyfile")],
    )


def test_check_pass_installs_and_cleans_tree(env, tmp_path):
    stage_dir = tmp_path / "stage"
    component = _caddy(("validate", "{file}"))
    env.rc_queue.append(0)  # check exits 0
    provision_systemd(
        _spec(component),
        component,
        stages={Stages.Staging},
        secrets_dir=tmp_path / "secrets",
        read_asset=_read_asset,
        staged_dir=stage_dir,
    )
    assert [dest for dest, _ in env.installs] == [Path("/etc/caddy/Caddyfile")]
    assert not stage_dir.exists()


def test_check_fail_aborts_before_install(env, tmp_path):
    component = _caddy(("validate", "{file}"))
    env.rc_queue.append(1)  # check exits nonzero
    with pytest.raises(CheckFailed):
        provision_systemd(
            _spec(component),
            component,
            stages={Stages.Staging},
            secrets_dir=tmp_path / "secrets",
            read_asset=_read_asset,
            staged_dir=tmp_path / "stage",
        )
    assert env.installs == []


def test_check_runs_against_staged_file(env, tmp_path, monkeypatch):
    stage_dir = tmp_path / "stage"
    seen = {}

    def _run(cmd, *, check=True, capture=False, text=True, **kw):
        seen["cmd"] = list(cmd)
        seen["exists"] = Path(cmd[1]).is_file()  # file is staged before the check
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    # Replace the fixture's ops.run for this test; monkeypatch restores it.
    monkeypatch.setattr(sysmod.ops, "run", _run)

    component = _caddy(("validate", "{file}"))
    provision_systemd(
        _spec(component),
        component,
        stages={Stages.Staging},
        secrets_dir=tmp_path / "secrets",
        read_asset=_read_asset,
        staged_dir=stage_dir,
    )
    assert Path(seen["cmd"][1]) == stage_dir / "etc/caddy/Caddyfile"
    assert seen["exists"] is True


def _restarts(calls):
    return [c for c in calls if c[:2] == ["systemctl", "restart"]]


def test_shared_restart_fires_once_across_stages(env, tmp_path):
    component = Component(
        name="caddy",
        units=[StaticUnit(src="a", dest="/etc/caddy/Caddyfile")],
        restart=SharedRestart("caddy.service"),
    )
    provision_systemd(
        _spec(component, stages=frozenset({Stages.Staging, Stages.Production})),
        component,
        stages={Stages.Staging, Stages.Production},
        secrets_dir=tmp_path / "secrets",
        read_asset=_read_asset,
    )
    assert _restarts(env.calls) == [["systemctl", "restart", "caddy.service"]]


def test_stage_restart_fans_out_in_enum_order(env, tmp_path):
    component = Component(
        name="app",
        units=[StaticUnit(src="a", dest="/etc/systemd/system/app@.service")],
        restart=StageRestart("app@{stage}.service"),
    )
    provision_systemd(
        _spec(component, stages=frozenset({Stages.Staging, Stages.Production})),
        component,
        stages={Stages.Production, Stages.Staging},
        secrets_dir=tmp_path / "secrets",
        read_asset=_read_asset,
    )
    assert _restarts(env.calls) == [
        ["systemctl", "restart", "app@staging.service"],
        ["systemctl", "restart", "app@production.service"],
    ]


def test_restart_order_honored(env, tmp_path):
    component = Component(
        name="stack",
        units=[StaticUnit(src="a", dest="/etc/systemd/system/db@.service")],
        restart=(StageRestart("db@{stage}.service"), SharedRestart("caddy.service")),
    )
    provision_systemd(
        _spec(component),
        component,
        stages={Stages.Staging},
        secrets_dir=tmp_path / "secrets",
        read_asset=_read_asset,
    )
    assert _restarts(env.calls) == [
        ["systemctl", "restart", "db@staging.service"],
        ["systemctl", "restart", "caddy.service"],
    ]


def test_restart_failure_raises_and_leaves_files(env, tmp_path):
    component = Component(
        name="caddy",
        units=[StaticUnit(src="a", dest="/etc/caddy/Caddyfile")],
        restart=SharedRestart("caddy.service"),
    )
    env.rc_queue.append(1)  # the systemctl restart fails (check=True -> raises)
    with pytest.raises(subprocess.CalledProcessError):
        provision_systemd(
            _spec(component),
            component,
            stages={Stages.Staging},
            secrets_dir=tmp_path / "secrets",
            read_asset=_read_asset,
        )
    assert [dest for dest, _ in env.installs] == [Path("/etc/caddy/Caddyfile")]
