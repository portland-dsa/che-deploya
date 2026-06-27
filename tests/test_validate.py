import pytest
from che_deploya.spec import DeploySpec, Component, TemplatedUnit, Environment, Stages
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
    c = Component(name="bot", units=[
        TemplatedUnit(src="a", dest="b", env=Environment(names=set()), per_stage=False),
    ])
    with pytest.raises(SpecError, match="singleton"):
        validate(_spec([c]))

def test_grant_to_undeclared_role_rejected():
    db = Db(roles=[Role("m")], grants=[Grant({Privilege.Usage}, On.schema("public"), to="ghost")])
    with pytest.raises(SpecError, match="ghost"):
        validate(_spec([Component(name="bot", db=db)]))

def test_component_stage_not_subset_rejected():
    c = Component(name="bot", stages={Stages.Production})
    with pytest.raises(SpecError, match="subset"):
        validate(_spec([c], stages={Stages.Staging}))
