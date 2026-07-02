import pytest
from che_deploya.spec import (
    DeploySpec,
    Component,
    TemplatedUnit,
    StaticUnit,
    Environment,
    Stages,
    Check,
    StageRestart,
    SharedRestart,
)
from che_deploya.db import Db, Role, Grant, On, Privilege
from che_deploya.validate import validate, SpecError


def _spec(components, **kw):
    return DeploySpec(root="r", package="p", components=components, **kw)


def test_valid_spec_passes():
    validate(_spec([Component(name="bot")]))


def test_duplicate_component_names_rejected():
    with pytest.raises(SpecError, match="duplicate"):
        validate(_spec([Component(name="bot"), Component(name="bot")]))


def test_singleton_templated_unit_multi_stage_rejected():
    c = Component(
        name="bot",
        units=[
            TemplatedUnit(
                src="a", dest="b", env=Environment(names=set()), per_stage=False
            ),
        ],
    )
    with pytest.raises(SpecError, match="singleton"):
        validate(_spec([c]))


def test_grant_to_undeclared_role_rejected():
    db = Db(
        roles=[Role("m")],
        grants=[Grant({Privilege.Usage}, On.schema("public"), to="ghost")],
    )
    with pytest.raises(SpecError, match="ghost"):
        validate(_spec([Component(name="bot", db=db)]))


def test_component_stage_not_subset_rejected():
    c = Component(name="bot", stages={Stages.Production})
    with pytest.raises(SpecError, match="subset"):
        validate(_spec([c], stages={Stages.Staging}))


def test_templated_unit_secret_src_without_component_secrets_rejected():
    c = Component(
        name="bot",
        secrets=None,
        stages={Stages.Staging},
        units=[
            TemplatedUnit(
                src="a", dest="b", env=Environment(names=set()), per_stage=True
            )
        ],
    )
    with pytest.raises(SpecError, match="Secret"):
        validate(_spec([c]))


def _unit(dest, **kw):
    return StaticUnit(src="a", dest=dest, **kw)


def test_valid_check_and_restart_passes():
    c = Component(
        name="caddy",
        stages={Stages.Staging},
        units=[_unit("/etc/caddy/Caddyfile")],
        check=[
            Check(
                command=("caddy", "validate", "--config", "{file}"),
                target="/etc/caddy/Caddyfile",
            )
        ],
        restart=SharedRestart("caddy.service"),
    )
    validate(_spec([c], stages={Stages.Staging}))


@pytest.mark.parametrize(
    "component, match",
    [
        (
            Component(
                name="caddy",
                stages={Stages.Staging},
                units=[_unit("/etc/caddy/Caddyfile")],
                check=[
                    Check(
                        command=("caddy", "validate", "{stage}"),
                        target="/etc/caddy/Caddyfile",
                    )
                ],
            ),
            "file",
        ),
        (
            Component(
                name="caddy",
                stages={Stages.Staging},
                units=[_unit("/etc/caddy/Caddyfile")],
                check=[Check(command=("caddy", "{file}"), target="/etc/caddy/nope")],
            ),
            "target",
        ),
        (
            Component(
                name="caddy",
                stages={Stages.Staging},
                units=[_unit("/etc/caddy/site@{stage}.caddy", per_stage=True)],
                check=[
                    Check(
                        command=("caddy", "{file}"),
                        target="/etc/caddy/site@{stage}.caddy",
                    )
                ],
            ),
            "per-stage",
        ),
        (
            Component(
                name="caddy",
                stages={Stages.Staging},
                units=[_unit("/etc/caddy/Caddyfile"), _unit("/etc/caddy/Caddyfile")],
            ),
            "dest",
        ),
        (
            Component(
                name="caddy",
                stages={Stages.Staging},
                restart=StageRestart("caddy.service"),
            ),
            "SharedRestart",
        ),
        (
            Component(
                name="caddy",
                stages={Stages.Staging},
                restart=SharedRestart("app@{stage}.service"),
            ),
            "placeholder",
        ),
        (
            Component(
                name="caddy",
                stages={Stages.Staging},
                units=[_unit("/etc/caddy/Caddyfile")],
                check=[Check(command=(), target="/etc/caddy/Caddyfile")],
            ),
            "empty",
        ),
        (
            Component(
                name="caddy",
                stages={Stages.Staging},
                restart=StageRestart("app@{stage}-{root}.service"),
            ),
            r"only \{stage\}",
        ),
    ],
    ids=[
        "stray-placeholder",
        "bad-target",
        "per-stage-target",
        "dup-dest",
        "stage-restart-no-stage",
        "shared-restart-placeholder",
        "empty-command",
        "stage-restart-stray-placeholder",
    ],
)
def test_provisioning_validation_rejects(component, match):
    with pytest.raises(SpecError, match=match):
        validate(_spec([component], stages={Stages.Staging}))
