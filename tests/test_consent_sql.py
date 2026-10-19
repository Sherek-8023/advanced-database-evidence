"""Integration regressions for canonical consent selection and Customer 360.

Run with: python -m unittest discover -s tests -p 'test_consent_sql.py' -v
Requires psycopg2 and a PostgreSQL role with CREATEDB (the local Docker demo role).
DB_HOST, DB_PORT, DB_USER and DB_PASSWORD override the Docker connection defaults.

Every run creates its own randomly named database, seeds SQL 01-06 there, and
drops only that database at exit. Tests roll back their individual changes.
DB_NAME is deliberately ignored: this suite never connects to poverty_bank.
Only the fixed maintenance database postgres is used to create/drop the test DB.
"""

from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import unittest
from uuid import uuid4

import psycopg2
from psycopg2 import errors, sql


SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
SEED_FILES = [
    "01_create_schemas_and_tables.sql",
    "02_load_synthetic_source_data.sql",
    "03_stage_and_resolve_customers.sql",
    "04_load_data_vault.sql",
    "05_load_warehouse.sql",
    "06_reporting_views.sql",
]

# Original F01 statement retained only as a regression probe, inside a savepoint
# in the disposable database. It must fail once Alice has two source events.
LEGACY_CONSENT_UPSERT = """
INSERT INTO dw.customer_consent
    (canonical_customer_id, consent_type, consent_status, updated_at)
SELECT i.canonical_customer_id, c.consent_type, c.consent_status, c.updated_at
FROM src_crm.consent c
JOIN stg.customer_identity i
  ON i.source_system = 'SRC_CRM' AND i.source_customer_id = c.crm_contact_id
ON CONFLICT (canonical_customer_id, consent_type) DO UPDATE
SET consent_status = EXCLUDED.consent_status, updated_at = EXCLUDED.updated_at
WHERE EXCLUDED.updated_at >= dw.customer_consent.updated_at;
"""


class ConsentSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = "a2_consent_test_" + uuid4().hex
        cls.admin = None
        cls.db = None
        cls.created = False
        cls.addClassCleanup(cls.cleanup_database)
        cls.connection_options = {
            "host": os.environ.get("DB_HOST", "postgres"),
            "port": int(os.environ.get("DB_PORT", "5432")),
            "user": os.environ.get("DB_USER", "student"),
            "password": os.environ.get("DB_PASSWORD", "student"),
            "connect_timeout": 10,
            "application_name": "a2_consent_integration_tests",
        }
        cls.admin = psycopg2.connect(dbname="postgres", **cls.connection_options)
        cls.admin.autocommit = True
        with cls.admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(cls.test_db)
            ))
        cls.created = True
        cls.db = psycopg2.connect(dbname=cls.test_db, **cls.connection_options)
        with cls.db.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != cls.test_db:
                raise RuntimeError("Refusing to run outside the disposable test database")
            for filename in SEED_FILES:
                cursor.execute((SQL_DIR / filename).read_text(encoding="utf-8"))
        cls.db.commit()

    @classmethod
    def cleanup_database(cls):
        if cls.db is not None:
            cls.db.close()
        if cls.admin is not None:
            try:
                if cls.created:
                    # Name is generated above; never supplied by environment/input.
                    with cls.admin.cursor() as cursor:
                        cursor.execute(sql.SQL("DROP DATABASE {}").format(
                            sql.Identifier(cls.test_db)
                        ))
            finally:
                cls.admin.close()

    def setUp(self):
        self.cursor = self.db.cursor()
        self.addCleanup(self.cursor.close)
        self.addCleanup(self.db.rollback)
        self.cursor.execute("""
            SELECT canonical_customer_id FROM stg.customer_identity
            WHERE source_system = 'SRC_CRM' AND source_customer_id = 'CRM-301'
        """)
        self.alice_id = self.cursor.fetchone()[0]

    def run_sql(self, filename):
        self.cursor.execute((SQL_DIR / filename).read_text(encoding="utf-8"))

    def load_warehouse(self):
        self.run_sql("05_load_warehouse.sql")

    def add_consent(self, event_id, status, timestamp, contact="CRM-301",
                    consent_type="CDR_DATA_SHARING"):
        self.cursor.execute("""
            INSERT INTO src_crm.consent
                (consent_id, crm_contact_id, consent_type, consent_status, updated_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (event_id, contact, consent_type, status, timestamp))

    def delete_alice_source_consents(self):
        self.cursor.execute("""
            DELETE FROM src_crm.consent
            WHERE crm_contact_id = 'CRM-301' AND consent_type = 'CDR_DATA_SHARING'
        """)

    def current_consent(self, consent_type="CDR_DATA_SHARING"):
        self.cursor.execute("""
            SELECT consent_status, updated_at FROM dw.customer_consent
            WHERE canonical_customer_id = %s AND consent_type = %s
        """, (self.alice_id, consent_type))
        return self.cursor.fetchall()

    def assert_consent(self, status, timestamp):
        self.assertEqual(self.current_consent(), [(status, datetime.fromisoformat(timestamp))])

    def report_rows(self):
        self.cursor.execute("SELECT * FROM dw.vw_customer_360 ORDER BY canonical_customer_id")
        return self.cursor.fetchall()

    def test_seed_reports_and_repeat_load_remain_stable(self):
        baseline = self.report_rows()
        self.assertEqual(len(baseline), 8)
        self.cursor.execute("""
            SELECT active_product_count, deposit_balance, credit_and_loan_balance,
                   cdr_consent_status FROM dw.vw_customer_360
            WHERE canonical_customer_id = %s
        """, (self.alice_id,))
        self.assertEqual(self.cursor.fetchall(), [
            (4, Decimal("20950.00"), Decimal("386850.00"), "ACTIVE")
        ])
        for _ in range(2):
            self.load_warehouse()
            self.assertEqual(self.report_rows(), baseline)

    def test_closed_card_is_not_an_active_holding(self):
        self.cursor.execute("UPDATE src_cards.card SET status='CLOSED', current_balance=0 WHERE card_id='CC-7004'")
        self.run_sql("03_stage_and_resolve_customers.sql")
        self.load_warehouse()
        self.cursor.execute("""
            SELECT has_credit_card, card_balance, suggested_action FROM dw.vw_product_opportunity
            WHERE customer_name='Emma Brown'
        """)
        self.assertEqual(self.cursor.fetchall(), [(False, Decimal('0'), 'CREDIT_CARD_OFFER_REVIEW')])

    def test_identity_conflict_blocks_product_suggestions(self):
        self.cursor.execute("""
            INSERT INTO src_deposits.customer VALUES
              ('TEST-DEP','Test Conflict','conflict@example.test',NULL,'1980-01-01');
            INSERT INTO src_crm.contact VALUES
              ('TEST-CRM','Test Conflict','conflict@example.test',NULL,'1990-01-01');
            INSERT INTO src_deposits.account VALUES
              ('TEST-ACCOUNT','TEST-DEP','SAVINGS','2020-01-01',20000,'OPEN');
        """)
        self.run_sql("03_stage_and_resolve_customers.sql")
        self.load_warehouse()
        self.cursor.execute("""
            SELECT review_readiness, suggested_action FROM dw.vw_product_opportunity
            WHERE canonical_customer_id IN (
              SELECT canonical_customer_id FROM stg.customer_identity
              WHERE source_customer_id IN ('TEST-DEP','TEST-CRM'))
        """)
        self.assertEqual(self.cursor.fetchall(), [
            ('IDENTITY_REVIEW_REQUIRED','NO_RULE_BASED_SUGGESTION'),
            ('IDENTITY_REVIEW_REQUIRED','NO_RULE_BASED_SUGGESTION')])

    def test_crm_only_customer_does_not_imply_no_products(self):
        self.cursor.execute("""
            INSERT INTO src_crm.contact VALUES
              ('TEST-CRM','Test CRM Only','only@example.test',NULL,'1980-01-01')
        """)
        self.run_sql("03_stage_and_resolve_customers.sql")
        self.load_warehouse()
        self.cursor.execute("""
            SELECT has_credit_card, has_loan, review_readiness, suggested_action
            FROM dw.vw_product_opportunity WHERE customer_name='Test CRM Only'
        """)
        self.assertEqual(self.cursor.fetchall(), [
            (None,None,'NO_ACCOUNT_DATA','NO_RULE_BASED_SUGGESTION')])

    def test_multiple_events_reproduce_old_failure_and_choose_newest(self):
        self.add_consent("TEST-NEW", "WITHDRAWN", "2026-10-01 12:00:00")
        self.add_consent("TEST-OLD", "ACTIVE", "2026-09-01 12:00:00")
        self.cursor.execute("SAVEPOINT legacy_failure")
        with self.assertRaises(errors.CardinalityViolation):
            self.cursor.execute(LEGACY_CONSENT_UPSERT)
        self.cursor.execute("ROLLBACK TO SAVEPOINT legacy_failure")
        self.cursor.execute("RELEASE SAVEPOINT legacy_failure")
        self.load_warehouse()
        self.assert_consent("WITHDRAWN", "2026-10-01 12:00:00")
        self.load_warehouse()
        self.assert_consent("WITHDRAWN", "2026-10-01 12:00:00")

    def test_canonical_mapping_precedes_latest_selection(self):
        self.cursor.execute("""
            INSERT INTO src_crm.contact VALUES
                ('CRM-TEST-ALICE', 'Alice Chen', ' ALICE.CHEN@example.test ',
                 '0412345678', '1988-04-12')
        """)
        self.add_consent("TEST-CONTACT-NEW", "WITHDRAWN", "2026-10-02 12:00:00",
                         contact="CRM-TEST-ALICE")
        self.add_consent("TEST-CONTACT-OLD", "ACTIVE", "2026-10-01 12:00:00")
        self.run_sql("03_stage_and_resolve_customers.sql")
        self.cursor.execute("""
            SELECT count(*), count(DISTINCT canonical_customer_id)
            FROM stg.customer_identity WHERE source_system = 'SRC_CRM'
              AND source_customer_id IN ('CRM-301', 'CRM-TEST-ALICE')
        """)
        self.assertEqual(self.cursor.fetchone(), (2, 1))
        self.load_warehouse()
        self.assert_consent("WITHDRAWN", "2026-10-02 12:00:00")

    def test_older_input_cannot_overwrite_newer_target(self):
        self.add_consent("TEST-NEW", "WITHDRAWN", "2026-10-02 12:00:00")
        self.load_warehouse()
        self.delete_alice_source_consents()
        self.add_consent("TEST-LATE-ARRIVAL", "ACTIVE", "2026-10-01 12:00:00")
        for _ in range(2):
            self.load_warehouse()
            self.assert_consent("WITHDRAWN", "2026-10-02 12:00:00")

    def test_same_timestamp_withdrawal_is_independent_of_source_order(self):
        events = [("TEST-A", "ACTIVE"), ("TEST-W", "WITHDRAWN"), ("TEST-P", "PENDING")]
        for ordered_events in (events, list(reversed(events))):
            with self.subTest(order=[event[0] for event in ordered_events]):
                self.delete_alice_source_consents()
                self.cursor.execute("""
                    DELETE FROM dw.customer_consent
                    WHERE canonical_customer_id = %s AND consent_type = 'CDR_DATA_SHARING'
                """, (self.alice_id,))
                for event_id, status in ordered_events:
                    self.add_consent(event_id, status, "2026-10-02 12:00:00")
                self.load_warehouse()
                self.assert_consent("WITHDRAWN", "2026-10-02 12:00:00")

    def test_equal_timestamp_across_batches_and_newer_reactivation(self):
        self.add_consent("TEST-A", "ACTIVE", "2026-10-02 12:00:00")
        self.load_warehouse()
        self.assert_consent("ACTIVE", "2026-10-02 12:00:00")
        self.delete_alice_source_consents()
        self.add_consent("TEST-W", "WITHDRAWN", "2026-10-02 12:00:00")
        self.load_warehouse()
        self.assert_consent("WITHDRAWN", "2026-10-02 12:00:00")
        self.delete_alice_source_consents()
        self.add_consent("TEST-A-REPLAY", "ACTIVE", "2026-10-02 12:00:00")
        self.load_warehouse()
        self.assert_consent("WITHDRAWN", "2026-10-02 12:00:00")
        self.add_consent("TEST-A-NEW", "ACTIVE", "2026-10-03 12:00:00")
        self.load_warehouse()
        self.assert_consent("ACTIVE", "2026-10-03 12:00:00")

    def test_other_tied_statuses_follow_stable_bytewise_rule(self):
        self.add_consent("TEST-A", "ACTIVE", "2026-10-02 12:00:00")
        self.add_consent("TEST-P", "PENDING", "2026-10-02 12:00:00")
        self.load_warehouse()
        self.assert_consent("PENDING", "2026-10-02 12:00:00")
        self.delete_alice_source_consents()
        self.add_consent("TEST-A-REPLAY", "ACTIVE", "2026-10-02 12:00:00")
        self.load_warehouse()
        self.assert_consent("PENDING", "2026-10-02 12:00:00")

    def test_other_consent_types_reproduce_old_fanout_without_affecting_cdr(self):
        self.add_consent("TEST-MARKETING", "WITHDRAWN", "2026-10-04 12:00:00",
                         consent_type="MARKETING")
        self.add_consent("TEST-ANALYTICS", "PENDING", "2026-10-05 12:00:00",
                         consent_type="ANALYTICS")
        self.load_warehouse()
        current_views = (SQL_DIR / "06_reporting_views.sql").read_text(encoding="utf-8")
        type_filter = " AND co.consent_type = 'CDR_DATA_SHARING'"
        self.assertIn(type_filter, current_views)
        self.cursor.execute("SAVEPOINT legacy_fanout")
        self.cursor.execute(current_views.replace(type_filter, ""))
        self.cursor.execute("""
            SELECT cdr_consent_status FROM dw.vw_customer_360
            WHERE canonical_customer_id = %s ORDER BY cdr_consent_status
        """, (self.alice_id,))
        self.assertEqual(self.cursor.fetchall(), [("ACTIVE",), ("PENDING",), ("WITHDRAWN",)])
        self.cursor.execute("ROLLBACK TO SAVEPOINT legacy_fanout")
        self.cursor.execute("RELEASE SAVEPOINT legacy_fanout")
        self.cursor.execute("""
            SELECT cdr_consent_status, consent_updated_at FROM dw.vw_customer_360
            WHERE canonical_customer_id = %s
        """, (self.alice_id,))
        self.assertEqual(self.cursor.fetchall(), [("ACTIVE", datetime(2026, 9, 18, 12))])
        self.assertEqual(len(self.current_consent("MARKETING")), 1)
        self.assertEqual(len(self.current_consent("ANALYTICS")), 1)
        self.assertEqual(len(self.report_rows()), 8)

    def test_customers_with_no_cdr_or_no_consent_are_retained(self):
        self.delete_alice_source_consents()
        self.cursor.execute("""
            DELETE FROM dw.customer_consent
            WHERE canonical_customer_id = %s AND consent_type = 'CDR_DATA_SHARING'
        """, (self.alice_id,))
        self.add_consent("TEST-MARKETING", "ACTIVE", "2026-10-04 12:00:00",
                         consent_type="MARKETING")
        self.load_warehouse()
        self.cursor.execute("""
            SELECT cdr_consent_status, consent_updated_at FROM dw.vw_customer_360
            WHERE canonical_customer_id = %s
        """, (self.alice_id,))
        self.assertEqual(self.cursor.fetchall(), [(None, None)])
        self.cursor.execute("""
            SELECT cdr_consent_status, consent_updated_at FROM dw.vw_customer_360
            WHERE customer_name = 'David Li'
        """)
        self.assertEqual(self.cursor.fetchall(), [(None, None)])
        self.cursor.execute("""
            SELECT canonical_customer_id, count(*) FROM dw.vw_customer_360
            GROUP BY canonical_customer_id
        """)
        self.assertEqual(len(self.cursor.fetchall()), 8)
        self.cursor.execute("""
            SELECT canonical_customer_id FROM dw.vw_customer_360
            GROUP BY canonical_customer_id HAVING count(*) <> 1
        """)
        self.assertEqual(self.cursor.fetchall(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
