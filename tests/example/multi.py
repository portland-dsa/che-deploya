"""Two minimal specs sharing one package, for the multi-spec CLI tests."""

from che_deploya import Component, DeploySpec

BACKEND = DeploySpec(
    root="backend-app",
    package="tests.example",
    components=[Component(name="api"), Component(name="db")],
)

FRONTEND = DeploySpec(
    root="frontend-app",
    package="tests.example",
    components=[Component(name="web"), Component(name="api")],
)

SPECS = {"backend": BACKEND, "frontend": FRONTEND}
