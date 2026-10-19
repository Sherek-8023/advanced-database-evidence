"""Safety and lifecycle regressions for the independent quality demonstration.

Unit tests do not connect to PostgreSQL. Integration tests require BOTH
QUALITY_INTEGRATION_TESTS=1 and QUALITY_TEST_DISPOSABLE=1, and must run on a
fresh, disposable quality Compose project. They reset the three fixed quality
databases in that cluster. Never set these flags on the presentation stack.
The runner's cluster marker, inventory and hostname guards remain enabled.
"""

from contextlib import closing
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import psycopg2


PROJECT_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "a2_quality_demo_tests_target", PROJECT_DIR / "scripts" / "quality_demo.py"
)
demo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = demo
SPEC.loader.exec_module(demo)

INTEGRATION_ENABLED = (
    os.environ.get("QUALITY_INTEGRATION_TESTS") == "1"
    and os.environ.get("QUALITY_TEST_DISPOSABLE") == "1"
)
ALICE = "CUST-07E495D4EE"
REPORTS = (
    "vw_customer_360", "vw_transaction_risk_monitor", "vw_product_opportunity"
)


class QualitySafetyTests(unittest.TestCase):
    def test_arbitrary_case_names_cannot_select_a_database(self):
        with patch.object(demo.psycopg2, "connect") as connect:
            for case in ("poverty_bank", "postgres", "quality_identity", "../identity",
                         "identity; DROP DATABASE poverty_bank", "", "all"):
                with self.subTest(case=case), self.assertRaises(demo.SafetyError):
                    demo.validate_case(case)
            connect.assert_not_called()

    def test_default_and_unexpected_database_inventory_is_rejected(self):
        allowed = ["postgres", "template0", "template1"]
        demo.validate_inventory(allowed)
        demo.validate_inventory(allowed + list(demo.CASE_DATABASES.values()))
        for database in ("poverty_bank", "production", "quality_typo"):
            with self.subTest(database=database), self.assertRaises(demo.SafetyError):
                demo.validate_inventory(allowed + [database])

    def test_standard_postgres_host_cannot_be_used_by_quality_runner(self):
        with patch.object(demo.psycopg2, "connect") as connect:
            for host in ("postgres", "localhost", "127.0.0.1", "quality-postgres.evil", ""):
                with self.subTest(host=host), patch.dict(os.environ, {"QUALITY_PGHOST": host}):
                    with self.assertRaises(demo.SafetyError):
                        demo.connection_options()
            connect.assert_not_called()

    def test_normal_database_environment_does_not_redirect_quality_connections(self):
        with patch.dict(os.environ, {
            "QUALITY_PGHOST": "quality-postgres", "PGDATABASE": "poverty_bank",
            "PGHOST": "postgres", "DB_NAME": "poverty_bank", "DB_HOST": "postgres",
        }):
            options = demo.connection_options()
        self.assertEqual(options["host"], "quality-postgres")
        self.assertNotIn("dbname", options)

    def test_connection_to_main_database_is_refused_before_network_access(self):
        with patch.object(demo.psycopg2, "connect") as connect:
            for database in ("poverty_bank", "quality_typo", "template0"):
                with self.subTest(database=database), self.assertRaises(demo.SafetyError):
                    demo.connect(database)
            connect.assert_not_called()

    def test_missing_or_wrong_cluster_marker_cannot_mutate_an_existing_cluster(self):
        system_databases = [("postgres",), ("template0",), ("template1",)]
        cases = (
            (False, system_databases, None, []),
            (True, system_databases + [("quality_identity",)], None, []),
            (False, system_databases, "a2_quality_cluster", [("wrong-marker",)]),
            (True, system_databases + [("poverty_bank",)], None, []),
        )
        for initialize, databases, table, marker in cases:
            with self.subTest(initialize=initialize, databases=databases, marker=marker):
                connection = MagicMock()
                cursor = connection.cursor.return_value.__enter__.return_value
                cursor.fetchall.side_effect = [databases, marker]
                cursor.fetchone.return_value = (table,)
                with patch.object(demo, "connect", return_value=connection):
                    with self.assertRaises(demo.SafetyError):
                        demo.ensure_cluster(initialize=initialize)
                for invocation in cursor.execute.call_args_list:
                    self.assertTrue(invocation.args[0].lstrip().startswith("SELECT"))
                connection.close.assert_called_once()


@unittest.skipUnless(
    INTEGRATION_ENABLED,
    "Requires explicit integration and disposable-cluster opt-ins",
)
class QualityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        demo.command("init", "all")

    def query(self, case, statement, parameters=()):
        database = demo.validate_case(case)
        self.assertNotEqual(database, "poverty_bank")
        with closing(psycopg2.connect(dbname=database, **demo.connection_options())) as connection:
            connection.set_session(readonly=True)
            with connection.cursor() as cursor:
                cursor.execute(statement, parameters)
                return cursor.fetchall()

    def reports(self, case):
        return {
            name: self.query(case, f"SELECT row_to_json(r)::text FROM dw.{name} r ORDER BY 1")
            for name in REPORTS
        }

    def source_counts(self, case):
        return self.query(case, """
            SELECT (SELECT count(*) FROM src_deposits.customer),
                   (SELECT count(*) FROM src_cards.cardholder),
                   (SELECT count(*) FROM src_lending.borrower),
                   (SELECT count(*) FROM src_crm.contact),
                   (SELECT count(*) FROM src_crm.consent)
        """)[0]

    def assert_phase(self, case, expected):
        self.assertEqual(self.query(case, "SELECT phase FROM quality_meta.state"), [(expected,)])

    def assert_seed(self, case):
        self.assertEqual(self.query(case, "SELECT count(*) FROM dw.vw_customer_360"), [(8,)])
        self.assertEqual(self.query(case, """
            SELECT cdr_consent_status, deposit_balance, credit_and_loan_balance
            FROM dw.vw_customer_360 WHERE canonical_customer_id = %s
        """, (ALICE,)), [("ACTIVE", Decimal("20950.00"), Decimal("386850.00"))])

    def reset_baseline(self, case):
        demo.command("reset", case)
        self.assert_seed(case)
        self.assert_phase(case, "baseline")
        return self.reports(case), self.source_counts(case)

    def assert_repeat_is_stable(self, case, expected_reports, expected_sources):
        demo.command("inject", case)
        self.assertEqual(self.source_counts(case), expected_sources)
        demo.command("process", case)
        self.assertEqual(self.reports(case), expected_reports)
        demo.command("process", case)
        self.assertEqual(self.reports(case), expected_reports)
        demo.command("verify", case)

    def test_conflicting_birth_dates_are_separate_ambiguous_identities(self):
        baseline, source_counts = self.reset_baseline("identity")
        neighbours = {case: self.reports(case) for case in ("consent", "types")}
        demo.command("inject", "identity")
        self.assert_phase("identity", "injected")
        self.assertEqual(self.reports("identity"), baseline)
        injected_sources = self.source_counts("identity")
        self.assertEqual(sum(injected_sources[:4]), sum(source_counts[:4]) + 2)
        demo.command("process", "identity")
        self.assert_phase("identity", "processed")
        self.assertEqual(self.query("identity", "SELECT count(*) FROM dw.vw_customer_360"), [(10,)])
        self.assertEqual(self.query("identity", """
            SELECT count(*), count(DISTINCT canonical_customer_id),
                   count(DISTINCT normalized_email)
            FROM stg.customer_identity WHERE match_status = 'AMBIGUOUS'
        """), [(2, 2, 1)])
        self.assertEqual(self.query("identity", """
            SELECT count(DISTINCT s.date_of_birth)
            FROM stg.customer_source s
            JOIN stg.customer_identity i USING (source_system, source_customer_id)
            WHERE i.match_status = 'AMBIGUOUS'
        """), [(2,)])
        self.assert_repeat_is_stable("identity", self.reports("identity"), injected_sources)
        for case, expected in neighbours.items():
            self.assertEqual(self.reports(case), expected)

    def test_latest_consent_tie_selects_withdrawal_without_changing_balances(self):
        baseline, _ = self.reset_baseline("consent")
        demo.command("inject", "consent")
        self.assertEqual(self.reports("consent"), baseline)
        self.assertEqual(self.query("consent", """
            SELECT array_agg(DISTINCT consent_status ORDER BY consent_status)
            FROM src_crm.consent
            WHERE crm_contact_id = 'CRM-301' AND consent_type = 'CDR_DATA_SHARING'
              AND updated_at = (
                  SELECT max(updated_at) FROM src_crm.consent
                  WHERE crm_contact_id = 'CRM-301' AND consent_type = 'CDR_DATA_SHARING'
              )
        """), [(["ACTIVE", "WITHDRAWN"],)])
        demo.command("process", "consent")
        self.assertEqual(self.query("consent", """
            SELECT cdr_consent_status, deposit_balance, credit_and_loan_balance
            FROM dw.vw_customer_360 WHERE canonical_customer_id = %s
        """, (ALICE,)), [("WITHDRAWN", Decimal("20950.00"), Decimal("386850.00"))])
        self.assertEqual(self.query("consent", """
            SELECT c.updated_at = (
                SELECT max(updated_at) FROM src_crm.consent
                WHERE crm_contact_id = 'CRM-301' AND consent_type = 'CDR_DATA_SHARING'
            ) FROM dw.customer_consent c
            WHERE c.canonical_customer_id = %s AND c.consent_type = 'CDR_DATA_SHARING'
        """, (ALICE,)), [(True,)])
        processed = self.reports("consent")
        for report in REPORTS[1:]:
            self.assertEqual(processed[report], baseline[report])
        self.assert_repeat_is_stable("consent", processed, self.source_counts("consent"))

    def test_additional_consent_type_does_not_fan_out_customer_report(self):
        baseline, _ = self.reset_baseline("types")
        demo.command("inject", "types")
        self.assertEqual(self.reports("types"), baseline)
        self.assertEqual(self.query("types", """
            SELECT count(*) FROM src_crm.consent
            WHERE crm_contact_id = 'CRM-301' AND consent_type = 'MARKETING'
        """), [(1,)])
        demo.command("process", "types")
        self.assertEqual(self.query("types", """
            SELECT array_agg(consent_type ORDER BY consent_type)
            FROM dw.customer_consent WHERE canonical_customer_id = %s
        """, (ALICE,)), [(["CDR_DATA_SHARING", "MARKETING"],)])
        self.assertEqual(self.query("types", """
            SELECT count(*), count(DISTINCT canonical_customer_id) FROM dw.vw_customer_360
        """), [(8, 8)])
        # A join without the CDR-type condition would now return Alice twice.
        self.assertEqual(self.query("types", """
            SELECT count(*) FROM dw.dim_customer d
            JOIN dw.customer_consent c USING (canonical_customer_id)
            WHERE d.canonical_customer_id = %s
        """, (ALICE,)), [(2,)])
        self.assertEqual(self.reports("types"), baseline)
        self.assert_repeat_is_stable("types", baseline, self.source_counts("types"))

    def test_reset_reproduces_seed_and_init_does_not_destroy_progress(self):
        baseline, sources = self.reset_baseline("identity")
        demo.command("run", "identity")
        processed = self.reports("identity")
        self.assertNotEqual(processed, baseline)
        demo.command("init", "all")
        self.assertEqual(self.reports("identity"), processed)
        demo.command("reset", "identity")
        self.assertEqual(self.reports("identity"), baseline)
        self.assertEqual(self.source_counts("identity"), sources)

    def test_failed_processing_rolls_back_stage_vault_warehouse_and_phase(self):
        baseline, _ = self.reset_baseline("identity")
        demo.command("inject", "identity")
        before = {
            "staging": self.query("identity", "SELECT count(*) FROM stg.customer_source"),
            "vault": self.query("identity", "SELECT count(*) FROM vault.hub_customer"),
            "source": self.source_counts("identity"),
        }
        with tempfile.TemporaryDirectory(prefix="a2-quality-test-") as directory:
            altered_sql = Path(directory)
            for original in demo.SQL_DIR.glob("*.sql"):
                shutil.copyfile(original, altered_sql / original.name)
            warehouse_file = altered_sql / "05_load_warehouse.sql"
            warehouse_file.write_text(
                warehouse_file.read_text(encoding="utf-8") + "\nSELECT 1 / 0;\n",
                encoding="utf-8",
            )
            with patch.object(demo, "SQL_DIR", altered_sql):
                with self.assertRaises(psycopg2.errors.DivisionByZero):
                    demo.command("process", "identity")
        self.assert_phase("identity", "injected")
        self.assertEqual(self.reports("identity"), baseline)
        self.assertEqual(self.query("identity", "SELECT count(*) FROM stg.customer_source"), before["staging"])
        self.assertEqual(self.query("identity", "SELECT count(*) FROM vault.hub_customer"), before["vault"])
        self.assertEqual(self.source_counts("identity"), before["source"])
        demo.command("process", "identity")
        self.assert_phase("identity", "processed")
        self.assertEqual(self.query("identity", "SELECT count(*) FROM dw.vw_customer_360"), [(10,)])

    def test_reader_can_read_reports_but_cannot_read_sources_or_write(self):
        options = {
            **demo.connection_options(), "user": "report_reader", "password": "local_demo_reader"
        }
        for case in demo.CASE_DATABASES:
            with self.subTest(case=case), closing(psycopg2.connect(
                dbname=demo.validate_case(case), **options
            )) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SHOW default_transaction_read_only")
                    self.assertEqual(cursor.fetchone(), ("on",))
                    for report in REPORTS:
                        cursor.execute(f"SELECT count(*) FROM dw.{report}")
                        self.assertGreater(cursor.fetchone()[0], 0)
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        cursor.execute("SELECT * FROM src_crm.consent LIMIT 0")
                    connection.rollback()
                    # Test writes to a table this role CAN read, so the failure
                    # proves missing write privileges, not a missing SELECT grant.
                    # WHERE false prevents changes if a regression allows writes.
                    connection.set_session(readonly=False)
                    cursor.execute("SHOW transaction_read_only")
                    self.assertEqual(cursor.fetchone(), ("off",))
                    cursor.execute("""
                        SELECT has_table_privilege(current_user, 'dw.dim_account', 'SELECT'),
                               has_table_privilege(current_user, 'dw.dim_account', 'INSERT'),
                               has_table_privilege(current_user, 'dw.dim_account', 'UPDATE'),
                               has_table_privilege(current_user, 'dw.dim_account', 'DELETE')
                    """)
                    self.assertEqual(cursor.fetchone(), (True, False, False, False))
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        cursor.execute("""
                            UPDATE dw.dim_account SET current_balance = current_balance WHERE false
                        """)
                    connection.rollback()
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        cursor.execute("""
                            INSERT INTO dw.dim_account
                                (source_system, source_account_id, canonical_customer_id, product_key,
                                 opened_on, current_balance, account_status)
                            SELECT source_system, source_account_id, canonical_customer_id, product_key,
                                   opened_on, current_balance, account_status
                            FROM dw.dim_account WHERE false
                        """)
                    connection.rollback()


if __name__ == "__main__":
    unittest.main()
