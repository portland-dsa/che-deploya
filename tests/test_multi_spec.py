from pathlib import Path

import pytest

from che_deploya import build_cli
from che_deploya.selection import (
    all_stages,
    normalize_specs,
    select_specs,
    spec_name_enum,
    union_component_names,
)
from che_deploya.spec import Stages
from che_deploya.validate import SpecError
from tests.example.multi import BACKEND, FRONTEND, SPECS
from tests.example.spec import SPEC


def test_normalize_bare_spec_becomes_one_entry():
    result = normalize_specs(SPEC)
    assert list(result.values()) == [SPEC]


def test_normalize_mapping_is_copied():
    result = normalize_specs(SPECS)
    assert result == SPECS
    assert result is not SPECS


def test_normalize_empty_mapping_raises():
    with pytest.raises(SpecError):
        normalize_specs({})


def test_spec_name_enum_members_are_the_keys():
    enum = spec_name_enum(SPECS)
    assert {m.value for m in enum} == {"backend", "frontend"}


def test_spec_name_enum_rejects_non_identifier_key():
    with pytest.raises(SpecError):
        spec_name_enum({"web-app": BACKEND})


def test_select_specs_picks_the_named_subset():
    enum = spec_name_enum(SPECS)
    picked = select_specs(SPECS, frozenset({enum.backend}))
    assert picked == {"backend": BACKEND}


def test_union_component_names_dedupes_preserving_order():
    assert union_component_names(SPECS) == ["api", "db", "web"]


def test_all_stages_is_the_union():
    assert all_stages(SPECS) == frozenset(Stages)


from che_deploya.provision import _check_shared_secrets_dir, _components_to_provision


def test_single_spec_provision_needs_no_spec_option():
    app = build_cli(SPEC)
    command, bound, _ = app.parse_args(["provision", "app"])
    assert "spec" not in bound.arguments


def test_single_spec_provision_rejects_spec_option():
    app = build_cli(SPEC)
    with pytest.raises(SystemExit):
        app(["provision", "app", "--spec", "backend"])


def test_multi_spec_provision_requires_spec():
    app = build_cli(SPECS)
    with pytest.raises(SystemExit):
        app(["provision", "api"])


def test_multi_spec_provision_rejects_unknown_spec():
    app = build_cli(SPECS)
    with pytest.raises(SystemExit):
        app(["provision", "api", "--spec", "bogus"])


def test_multi_spec_mounts_the_union_of_component_names():
    app = build_cli(SPECS)
    assert app["provision"]["api"] is not None
    assert app["provision"]["db"] is not None
    assert app["provision"]["web"] is not None


def test_multi_spec_binds_the_selected_spec():
    app = build_cli(SPECS)
    _, bound, _ = app.parse_args(["provision", "api", "--spec", "backend"])
    assert {member.value for member in bound.arguments["spec"]} == {"backend"}


def test_components_to_provision_returns_present_pairs():
    pairs = _components_to_provision({"backend": BACKEND}, "api")
    assert [(spec.root, component.name) for spec, component in pairs] == [
        ("backend-app", "api")
    ]


def test_components_to_provision_skips_absent_loudly(capsys):
    pairs = _components_to_provision({"frontend": FRONTEND}, "db")
    assert pairs == []
    out = capsys.readouterr().out
    assert "frontend" in out and "db" in out


def test_empty_mapping_rejected_by_build_cli():
    with pytest.raises(SpecError):
        build_cli({})


def test_shared_secrets_dir_rejected_across_multiple_specs():
    # A CLI-usage mistake caught after cyclopts parsing exits cleanly (SystemExit),
    # not with a raw SpecError traceback.
    with pytest.raises(SystemExit):
        _check_shared_secrets_dir(Path("/tmp/x"), SPECS)


def test_shared_secrets_dir_allowed_for_a_single_spec():
    _check_shared_secrets_dir(Path("/tmp/x"), {"backend": BACKEND})


def test_omitted_secrets_dir_is_never_rejected():
    _check_shared_secrets_dir(None, SPECS)


from che_deploya.redeploy import _remote_command


def test_remote_command_single_omits_spec():
    cmd = _remote_command("/tmp/s", "p.pyz", frozenset({Stages.Staging}), None)
    assert "--spec" not in cmd
    assert "provision --bundled-secrets --self-destruct" in cmd
    assert "--targets staging" in cmd


def test_remote_command_multi_includes_spec():
    cmd = _remote_command("/tmp/s", "p.pyz", frozenset({Stages.Production}), "frontend")
    assert "provision --spec frontend --bundled-secrets" in cmd
    assert "--targets production" in cmd


def test_single_spec_redeploy_needs_no_spec_option():
    app = build_cli(SPEC)
    _, bound, _ = app.parse_args(["redeploy", "--host", "h"])
    assert bound.arguments["host"] == "h"
    assert "spec" not in bound.arguments


def test_multi_spec_redeploy_requires_spec():
    app = build_cli(SPECS)
    with pytest.raises(SystemExit):
        app(["redeploy", "--host", "h"])


def test_multi_spec_redeploy_binds_host_and_spec():
    app = build_cli(SPECS)
    _, bound, _ = app.parse_args(["redeploy", "--host", "h", "--spec", "frontend"])
    assert bound.arguments["host"] == "h"
    assert {member.value for member in bound.arguments["spec"]} == {"frontend"}


def test_spec_name_enum_rejects_case_colliding_keys():
    with pytest.raises(SpecError):
        spec_name_enum({"api": BACKEND, "API": FRONTEND})


def test_spec_name_enum_rejects_transform_colliding_keys():
    # cyclopts folds both to "web-api" (camelCase -> snake -> lower -> "-"), so
    # only one would ever be selectable; reject the ambiguous pair up front.
    with pytest.raises(SpecError):
        spec_name_enum({"web_api": BACKEND, "webApi": FRONTEND})


def test_spec_name_enum_rejects_reserved_enum_name():
    # "mro" is a valid identifier but reserved by the enum machinery; the cryptic
    # ValueError from the constructor is surfaced as a typed SpecError.
    with pytest.raises(SpecError):
        spec_name_enum({"mro": BACKEND, "other": FRONTEND})


def test_multi_spec_provision_rejects_empty_spec():
    # `--empty-spec` (cyclopts' synthesized empty-set flag) must not satisfy the
    # required selector and silently provision nothing at exit 0.
    app = build_cli(SPECS)
    with pytest.raises(SystemExit) as exc:
        app(["provision", "all", "--empty-spec"])
    assert exc.value.code != 0


def test_multi_spec_redeploy_rejects_empty_spec():
    app = build_cli(SPECS)
    with pytest.raises(SystemExit) as exc:
        app(["redeploy", "--host", "h", "--empty-spec"])
    assert exc.value.code != 0


def test_component_absent_in_every_selected_spec_errors():
    # `provision db --spec frontend` where frontend declares no `db` component is a
    # no-op; report it as an error instead of exiting 0 having provisioned nothing.
    app = build_cli(SPECS)
    with pytest.raises(SystemExit) as exc:
        app(["provision", "db", "--spec", "frontend"])
    assert exc.value.code != 0
    assert "db" in str(exc.value.code)
