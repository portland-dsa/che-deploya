from che_deploya.db import (
    Db, Role, Database, Grant, Revoke, On, Privilege, role_names,
    render_group_role, render_create_role, render_create_database,
    render_revoke, render_grant,
)

def test_role_names_collects_group_and_roles():
    db = Db(group_role=Role("botonio_app"),
            roles=[Role("botonio_staging_migrate"), Role("botonio_staging_app")])
    assert role_names(db) == {"botonio_app", "botonio_staging_migrate", "botonio_staging_app"}

def test_group_role_render():
    assert render_group_role(Role("botonio_app")) == (
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'botonio_app') THEN "
        "CREATE ROLE botonio_app NOLOGIN; "
        "END IF; END $$;"
    )

def test_create_role_with_password_quotes_literal():
    out = render_create_role(Role("botonio_staging_migrate", login=True,
                                  password="db_migration_password"), password="p'w")
    assert out == "CREATE ROLE botonio_staging_migrate LOGIN PASSWORD 'p''w';"

def test_create_role_member_of():
    out = render_create_role(Role("botonio_staging_app", login=True,
                                  member_of="botonio_app"), password=None)
    assert out == "CREATE ROLE botonio_staging_app LOGIN IN ROLE botonio_app;"

def test_create_database():
    out = render_create_database(Database("botonio_staging", owner="botonio_staging_migrate"))
    assert out == "CREATE DATABASE botonio_staging OWNER botonio_staging_migrate;"

def test_revoke_public():
    assert render_revoke(Revoke({Privilege.All}, On.schema("public"))) == (
        "REVOKE ALL ON SCHEMA public FROM PUBLIC;"
    )

def test_grant_connect_database():
    out = render_grant(Grant({Privilege.Connect}, On.database("botonio_staging"),
                             to="botonio_staging_app"))
    assert out == "GRANT CONNECT ON DATABASE botonio_staging TO botonio_staging_app;"

def test_grant_table_guarded():
    out = render_grant(Grant({Privilege.Delete}, On.table("manual_override"),
                             to="botonio_app", require_exists=True))
    assert out == (
        "DO $$ BEGIN IF to_regclass('public.manual_override') IS NOT NULL THEN "
        "GRANT DELETE ON manual_override TO botonio_app; END IF; END $$;"
    )
