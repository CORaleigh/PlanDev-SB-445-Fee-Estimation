import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT = SCRIPTS_DIR / "run_query.py"
SPEC = importlib.util.spec_from_file_location("run_query", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

import db_connect


class FakeDB:
    def __init__(self, result_sets):
        self.result_sets = result_sets
        self.sql = None

    def execute_query_sets(self, sql):
        self.sql = sql
        return self.result_sets


class RunQueryTests(unittest.TestCase):
    def test_newest_sql_driver_is_selected_without_connecting(self):
        drivers = ["SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"]
        with patch.object(db_connect.pyodbc, "drivers", return_value=drivers), \
             patch.object(db_connect.pyodbc, "connect") as connect, \
             patch.object(db_connect, "logger"):
            connection = db_connect.TrustedSQLServerConnection("server.example", "database")
        self.assertEqual(connection.driver, "ODBC Driver 18 for SQL Server")
        connect.assert_not_called()

    def test_load_env_reads_named_sql_settings(self):
        names = ("ENERGOV_SQL_SERVER", "ENERGOV_SQL_DATABASE")
        original = {name: os.environ.get(name) for name in names}
        try:
            os.environ["ENERGOV_SQL_SERVER"] = "server.example"
            os.environ["ENERGOV_SQL_DATABASE"] = "EnerGov"
            actual = MODULE.load_env(Path("missing.env"))
        finally:
            for name, value in original.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(actual, ("server.example", "EnerGov"))

    def test_load_env_accepts_legacy_sql_settings(self):
        names = (
            "ENERGOV_SQL_SERVER",
            "ENERGOV_SQL_DATABASE",
            "ENERGOVDB_SERVER",
            "ENERGOVDB_DATABASE",
        )
        original = {name: os.environ.get(name) for name in names}
        try:
            os.environ.pop("ENERGOV_SQL_SERVER", None)
            os.environ.pop("ENERGOV_SQL_DATABASE", None)
            os.environ["ENERGOVDB_SERVER"] = "legacy-server.example"
            os.environ["ENERGOVDB_DATABASE"] = "LegacyEnerGov"
            actual = MODULE.load_env(Path("missing.env"))
        finally:
            for name, value in original.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(actual, ("legacy-server.example", "LegacyEnerGov"))

    def test_run_query_sets_preserves_every_result_set(self):
        expected = [
            pd.DataFrame({"a": [1, 2]}),
            pd.DataFrame({"b": ["x"]}),
            pd.DataFrame(columns=["c"]),
        ]
        db = FakeDB(expected)
        query = MODULE.ExplorationQuery(
            name="multi",
            description="test",
            sql="SELECT 1; SELECT 2;",
        )

        actual = MODULE.run_query_sets(db, query)

        self.assertEqual(len(actual), 3)
        self.assertEqual(actual[0].to_dict("records"), [{"a": 1}, {"a": 2}])
        self.assertEqual(actual[1].to_dict("records"), [{"b": "x"}])
        self.assertTrue(actual[2].empty)
        self.assertEqual(db.sql, query.sql)

    def test_write_result_sets_uses_numbered_csv_files(self):
        result_sets = [
            pd.DataFrame({"a": [1]}),
            pd.DataFrame({"b": [2]}),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = MODULE.write_result_sets(
                result_sets,
                Path(temp_dir) / "results",
            )

            self.assertEqual(
                [path.name for path in paths],
                ["result-01.csv", "result-02.csv"],
            )
            self.assertEqual(
                pd.read_csv(paths[0]).to_dict("records"),
                [{"a": 1}],
            )
            self.assertEqual(
                pd.read_csv(paths[1]).to_dict("records"),
                [{"b": 2}],
            )


if __name__ == "__main__":
    unittest.main()
