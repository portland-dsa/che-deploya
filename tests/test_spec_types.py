from che_deploya import StageRestart, SharedRestart, Component
from che_deploya.spec import normalize_restart


def test_component_defaults_have_no_check_or_restart():
    c = Component(name="x")
    assert c.check == ()
    assert c.restart == ()


def test_normalize_restart_wraps_a_single_item():
    r = SharedRestart("caddy.service")
    assert normalize_restart(r) == (r,)


def test_normalize_restart_passes_a_sequence_through():
    items = (StageRestart("app@{stage}.service"), SharedRestart("caddy.service"))
    assert normalize_restart(items) == items
