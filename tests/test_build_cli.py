import pytest
from che_deploya import build_cli, DeploySpec, Component
from che_deploya.validate import SpecError
from tests.example.spec import SPEC


def test_build_cli_rejects_invalid_spec():
    bad = DeploySpec(
        root="r", package="p", components=[Component(name="x"), Component(name="x")]
    )
    with pytest.raises(SpecError):
        build_cli(bad)


def test_build_cli_exposes_provision_and_redeploy():
    app = build_cli(SPEC)
    assert app["provision"] is not None
    assert app["redeploy"] is not None


def test_help_runs():
    app = build_cli(SPEC)
    with pytest.raises(SystemExit):
        app(["--help"])
