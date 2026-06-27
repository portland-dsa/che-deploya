"""A compact two-unit spec for exercising build_cli end to end."""
from enum import StrEnum
from che_deploya import (
    DeploySpec, Component, StaticUnit, TemplatedUnit, Secret, Environment,
)

class SecretTokens(StrEnum):
    ApiToken = "api_token"

class EnvVars(StrEnum):
    GuildId = "guild_id"

SPEC = DeploySpec(
    root="example-app",
    package="tests.example",
    components=[
        Component(
            name="app",
            secrets=Secret(names=frozenset(SecretTokens), src="{repo_root}/secrets/{stage}.enc.yaml"),
            units=[
                TemplatedUnit(src="{repo_root}/deploy/assets/app@{stage}.conf",
                              resource_loc="assets/app@{stage}.conf",
                              dest="/etc/systemd/system/app@{stage}.conf", per_stage=True,
                              env=Environment(names=frozenset(EnvVars))),
                StaticUnit(src="{repo_root}/deploy/assets/app@.service",
                           resource_loc="assets/app@.service",
                           dest="/etc/systemd/system/app@.service"),
            ],
        ),
    ],
)
