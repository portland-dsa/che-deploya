import pytest
from che_deploya.templating import resolve

def test_substitutes_known_fields():
    out = resolve("/etc/{root}/{component}/{stage}", root="botonio-botsci",
                  component="bot", stage="staging")
    assert out == "/etc/botonio-botsci/bot/staging"

def test_partial_fields_leaves_others_untouched():
    out = resolve("{repo_root}/secrets/{stage}.enc.yaml", repo_root="/r", stage="staging")
    assert out == "/r/secrets/staging.enc.yaml"

def test_unknown_placeholder_raises():
    with pytest.raises(KeyError):
        resolve("{bogus}/x", root="r")
