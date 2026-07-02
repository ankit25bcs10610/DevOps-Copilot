"""Structural validation of the Postgres RLS policies (no live DB required).

Guards that every org-scoped tenant table has RLS enabled + an isolation policy that
keys on the per-session current-org setting — so a schema change can't silently drop
a tenant-isolation policy."""

from pathlib import Path

import pytest

_SQL = Path(__file__).resolve().parents[1] / "deploy" / "postgres" / "rls.sql"
_SCOPED_TABLES = ["orgs", "memberships", "api_keys", "integration_secrets", "usage"]


@pytest.fixture(scope="module")
def sql() -> str:
    return _SQL.read_text()


def test_rls_file_exists_and_creates_nonowner_role(sql):
    assert "CREATE ROLE copilot_app" in sql


@pytest.mark.parametrize("table", _SCOPED_TABLES)
def test_every_scoped_table_enables_rls(sql, table):
    assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
    assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql


@pytest.mark.parametrize("table", _SCOPED_TABLES)
def test_every_scoped_table_has_isolation_policy(sql, table):
    assert f"CREATE POLICY {table}_isolation ON {table}" in sql


def test_policies_key_on_current_org_setting(sql):
    # Every policy must constrain on the session's current org, not a constant.
    assert sql.count("current_setting('app.current_org'") >= 2 * len(_SCOPED_TABLES)
