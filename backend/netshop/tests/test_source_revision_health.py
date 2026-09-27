"""Pure netshop.0003 marker readiness catalog checks; no database writes."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from teruisi_backend import health


def guard():
    return {"columns":[("transaction_id",True,"bigint"),
                ("baseline_revision",True,"bigint"),
                ("baseline_digest",True,"character varying(64)")],
        "primaryKey":[("PRIMARY KEY (transaction_id)",)],
        "triggers":list(health.NETSHOP_MARKER_TRIGGERS),
        "functions":[(name,definer,["search_path=pg_catalog, public"])
            for name,definer in health.NETSHOP_MARKER_FUNCTIONS.items()],
        "privileges":(False,)*34}


class Cursor:
    def __init__(self, migrated, present, state):
        self.migrated,self.present,self.state = migrated,present,deepcopy(state)
        self.rows = []

    def execute(self, query, params=None):
        if "FROM django_migrations" in query:
            self.rows = [(self.migrated,)]
        elif "to_regclass('public.netshop_source_revision_markers')" in query:
            self.rows = [(self.present,)]
        elif "FROM pg_catalog.pg_attribute a" in query:
            self.rows = self.state["columns"]
        elif "pg_get_constraintdef" in query:
            self.rows = self.state["primaryKey"]
        elif "FROM pg_catalog.pg_trigger t" in query:
            self.rows = self.state["triggers"]
        elif "FROM pg_catalog.pg_proc p" in query:
            self.rows = self.state["functions"]
        elif "pg_catalog.has_table_privilege" in query:
            self.rows = [self.state["privileges"]]
        else:
            raise AssertionError("unexpected netshop health query: "+query)

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class NetshopSourceRevisionHealthTests(SimpleTestCase):
    def ready(self, migrated=False, present=False, state=None):
        cursor = Cursor(migrated,present,state or guard())
        with patch.object(health,"connection",SimpleNamespace(vendor="postgresql")):
            health._validate_netshop_source_marker_guard(cursor)

    def test_netshop_0002_old_path_and_0003_complete_path(self):
        self.ready()
        self.ready(migrated=True,present=True)

    def test_migration_marker_bidirectional_rejection(self):
        with self.assertRaisesMessage(health.ReadinessError,
                "netshop_source_marker_schema_missing"):
            self.ready(migrated=True)
        with self.assertRaisesMessage(health.ReadinessError,
                "netshop_source_marker_without_migration"):
            self.ready(present=True)

    def test_triggers_functions_search_path_columns_and_grants_are_exact(self):
        cases = []
        value = guard(); value["columns"].pop(); cases.append(value)
        value = guard(); value["primaryKey"].clear(); cases.append(value)
        value = guard(); value["triggers"].pop(); cases.append(value)
        value = guard(); value["triggers"][0] = (*value["triggers"][0][:3],
            "D",*value["triggers"][0][4:]); cases.append(value)
        value = guard(); value["functions"].pop(); cases.append(value)
        value = guard(); value["functions"][0] = (value["functions"][0][0],
            False,["search_path=public"]); cases.append(value)
        value = guard(); value["privileges"] = (False,)*10+(True,)+(False,)*23; cases.append(value)
        for state in cases:
            with self.subTest(state=state),self.assertRaises(health.ReadinessError):
                self.ready(True,True,state)
