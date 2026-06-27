import sys
import zipfile
from unittest.mock import patch

import pytest
from cyclopts import App

from che_deploya.redeploy import build_redeploy, bundle
from che_deploya.spec import DeploySpec, Component


def _spec() -> DeploySpec:
    return DeploySpec(root="test", package="che_deploya", components=[Component(name="bot")])


def test_build_redeploy_returns_app():
    app = build_redeploy(_spec())
    assert isinstance(app, App)


def test_bundle_refuses_prebuilt_pyz(tmp_path):
    # Create a minimal valid zip file to simulate running from a .pyz
    fake_pyz = tmp_path / "fake.pyz"
    with zipfile.ZipFile(fake_pyz, "w") as zf:
        zf.writestr("__main__.py", "")

    with patch.object(sys, "argv", [str(fake_pyz)]):
        with pytest.raises(RuntimeError, match="prebuilt .pyz"):
            bundle(_spec())
