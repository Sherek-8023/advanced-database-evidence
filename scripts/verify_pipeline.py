"""Read-only assertions for the supplied Poverty Bank synthetic demonstration.

Run after the original SQL pipeline, including after a repeat load. This checks
the current seed data, not production readiness, PFM authorisation, historical
change handling, or the edge cases noted in the code review. Existing append-only
Vault snapshots are allowed. No tables, schemas, source records, or other
databases are changed.

Connection defaults match the teammate's scripts. Optional overrides:
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_CONNECT_TIMEOUT.
Exit status: 0 = every check passed; 1 = a check or database operation failed.
"""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import os
import sys

import psycopg2


class Checks:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def equal(self, name, actual, expected):
        # Explicit comparison remains active even under python -O.
        if actual != expected:
            self.failed += 1
            print(f"FAIL {name}: expected {expected!r}; got {actual!r}")
        else:
            self.passed += 1
            print(f"PASS {name}")

    def at_least(self, name, actual, minimum):
        self.equal(f"{name} (actual={actual}, minimum={minimum})", actual >= minimum, True)


def rows(cursor, statement):
    cursor.execute(statement)
    return cursor.fetchall()


def seed_customer_id(source_system, source_customer_id, email):
    """Independent identity expectation for these non-ambiguous seed records."""
    normalized_email = email.strip().lower() if email else ""
    if normalized_email:
        token = hashlib.md5(normalized_email.encode("utf-8")).hexdigest()[:10].upper()
        return "CUST-" + token
    token = hashlib.md5(
        f"{source_system}:{source_customer_id}".encode("utf-8")
    ).hexdigest()[:10].upper()
    return "UNMATCHED-" + token


def compare_records(checks, name, actual, expected):
    """Compare full records, preserving duplicate multiplicity."""
    actual_set, expected_set = Counter(actual), Counter(expected)
    if actual_set == expected_set:
        checks.equal(name, True, True)
        return
    missing = list((expected_set - actual_set).elements())
    unexpected = list((actual_set - expected_set).elements())
    checks.failed += 1
    print(f"FAIL {name}: missing={len(missing)}, unexpected={len(unexpected)}")
    for record in missing[:3]:
        print(f"  missing: {record!r}")
    for record in unexpected[:3]:
        print(f"  unexpected: {record!r}")


def verify(cursor, checks, *, expected_database=None, expected_read_only="on"):
    if expected_read_only not in ("on", "off"):
        raise ValueError("Invalid expected transaction mode")
    checks.equal(
        ("transaction is read-only with a stable snapshot" if expected_read_only == "on"
         else "isolated setup transaction is read-write with a stable snapshot"),
        rows(cursor, "SELECT current_setting('transaction_read_only'), "
                     "current_setting('transaction_isolation')"),
        [(expected_read_only, "repeatable read")],
    )
    checks.equal("connected database", rows(cursor, "SELECT current_database()")[0][0],
                 expected_database or os.environ.get("DB_NAME", "poverty_bank"))

    checks.equal("four source customer inventories", dict(rows(cursor, """
        SELECT 'SRC_DEPOSITS', count(*) FROM src_deposits.customer
        UNION ALL SELECT 'SRC_CARDS', count(*) FROM src_cards.cardholder
        UNION ALL SELECT 'SRC_LENDING', count(*) FROM src_lending.borrower
        UNION ALL SELECT 'SRC_CRM', count(*) FROM src_crm.contact
    """)), {"SRC_DEPOSITS": 6, "SRC_CARDS": 4, "SRC_LENDING": 4, "SRC_CRM": 6})

    checks.equal("20 staged source customers", rows(cursor,
                 "SELECT count(*) FROM stg.customer_source")[0][0], 20)
    statuses = dict(rows(cursor, """
        SELECT match_status, count(*) FROM stg.customer_identity GROUP BY match_status
    """))
    statuses.setdefault("AMBIGUOUS", 0)
    checks.equal("identity outcomes", statuses,
                 {"MATCHED": 18, "UNMATCHED": 2, "AMBIGUOUS": 0})
    source_customers = rows(cursor, """
        SELECT 'SRC_DEPOSITS', customer_id, email FROM src_deposits.customer
        UNION ALL SELECT 'SRC_CARDS', cardholder_id, email FROM src_cards.cardholder
        UNION ALL SELECT 'SRC_LENDING', borrower_id, email FROM src_lending.borrower
        UNION ALL SELECT 'SRC_CRM', crm_contact_id, email FROM src_crm.contact
    """)
    expected_identities = [
        (source, source_id, seed_customer_id(source, source_id, email),
         "MATCHED" if email and email.strip() else "UNMATCHED")
        for source, source_id, email in source_customers
    ]
    compare_records(checks, "each source customer has its expected seed identity",
                    rows(cursor, "SELECT source_system, source_customer_id, "
                         "canonical_customer_id, match_status FROM stg.customer_identity"),
                    expected_identities)

    checks.equal("warehouse and stage row counts", dict(rows(cursor, """
        SELECT 'canonical_identities', count(DISTINCT canonical_customer_id)
          FROM stg.customer_identity
        UNION ALL SELECT 'customers', count(*) FROM dw.dim_customer
        UNION ALL SELECT 'stage_accounts', count(*) FROM stg.account_source
        UNION ALL SELECT 'accounts', count(*) FROM dw.dim_account
        UNION ALL SELECT 'stage_transactions', count(*) FROM stg.transaction_source
        UNION ALL SELECT 'transactions', count(*) FROM dw.fact_transaction
        UNION ALL SELECT 'stage_repayments', count(*) FROM stg.repayment_source
        UNION ALL SELECT 'repayments', count(*) FROM dw.fact_loan_repayment
        UNION ALL SELECT 'interactions', count(*) FROM dw.fact_customer_interaction
    """)), {"canonical_identities": 8, "customers": 8, "stage_accounts": 15,
             "accounts": 15, "stage_transactions": 14, "transactions": 14,
             "stage_repayments": 4, "repayments": 4, "interactions": 5})

    # Read raw business sources directly. Identity expectations are computed above
    # independently of staging and warehouse joins, so a shared bad join cannot
    # make this comparison pass merely because aggregate amounts still match.
    source_transactions = rows(cursor, """
        SELECT 'SRC_DEPOSITS', t.transaction_id, t.account_id,
               c.customer_id, c.email, t.transaction_ts, t.transaction_type,
               t.amount, t.merchant, t.channel
          FROM src_deposits.deposit_transaction t
          JOIN src_deposits.account a ON a.account_id = t.account_id
          JOIN src_deposits.customer c ON c.customer_id = a.customer_id
        UNION ALL
        SELECT 'SRC_CARDS', t.transaction_id, t.card_id,
               c.cardholder_id, c.email, t.transaction_ts, 'CARD_PURCHASE',
               t.amount, t.merchant, t.channel
          FROM src_cards.card_transaction t
          JOIN src_cards.card c0 ON c0.card_id = t.card_id
          JOIN src_cards.cardholder c ON c.cardholder_id = c0.cardholder_id
    """)
    expected_transactions = [
        (source, transaction_id, account_id,
         seed_customer_id(source, source_customer_id, email), timestamp,
         transaction_type, amount, merchant, channel)
        for (source, transaction_id, account_id, source_customer_id, email,
             timestamp, transaction_type, amount, merchant, channel) in source_transactions
    ]
    totals = defaultdict(lambda: [0, Decimal("0")])
    for record in expected_transactions:
        totals[record[0]][0] += 1
        totals[record[0]][1] += record[6]
    checks.equal("independent source transaction counts and signed amounts", dict(totals),
                 {"SRC_DEPOSITS": [8, Decimal("-6443.40")],
                  "SRC_CARDS": [6, Decimal("3720.60")]})
    compare_records(checks, "every source transaction matches staging", rows(cursor, """
        SELECT source_system, source_transaction_id, source_account_id,
               canonical_customer_id, transaction_ts, transaction_type,
               amount, merchant, channel FROM stg.transaction_source
    """), expected_transactions)
    compare_records(checks, "every source transaction matches warehouse and associations",
                    rows(cursor, """
        SELECT t.source_system, t.source_transaction_id, a.source_account_id,
               c.canonical_customer_id, t.transaction_ts, t.transaction_type,
               t.amount, t.merchant, t.channel
          FROM dw.fact_transaction t
          JOIN dw.dim_customer c ON c.customer_key = t.customer_key
          JOIN dw.dim_account a ON a.account_key = t.account_key
    """), expected_transactions)
    checks.equal("transaction account, customer and date references agree", rows(cursor, """
        SELECT count(*) FROM dw.fact_transaction t
          JOIN dw.dim_customer c ON c.customer_key = t.customer_key
          JOIN dw.dim_account a ON a.account_key = t.account_key
          JOIN dw.dim_date d ON d.date_key = t.date_key
         WHERE t.source_system IS DISTINCT FROM a.source_system
            OR c.canonical_customer_id IS DISTINCT FROM a.canonical_customer_id
            OR d.calendar_date IS DISTINCT FROM t.transaction_ts::date
    """)[0][0], 0)

    report_names = {"vw_customer_360", "vw_transaction_risk_monitor", "vw_product_opportunity"}
    checks.equal("three report views exist", {r[0] for r in rows(cursor, """
        SELECT table_name FROM information_schema.views WHERE table_schema = 'dw'
          AND table_name IN ('vw_customer_360', 'vw_transaction_risk_monitor',
                             'vw_product_opportunity')
    """)}, report_names)
    checks.equal("three report views contain expected seed rows", dict(rows(cursor, """
        SELECT 'vw_customer_360', count(*) FROM dw.vw_customer_360
        UNION ALL SELECT 'vw_transaction_risk_monitor', count(*) FROM dw.vw_transaction_risk_monitor
        UNION ALL SELECT 'vw_product_opportunity', count(*) FROM dw.vw_product_opportunity
    """)), {"vw_customer_360": 8, "vw_transaction_risk_monitor": 8,
             "vw_product_opportunity": 8})
    compare_records(checks, "Customer 360 has exactly one row per canonical customer",
                    rows(cursor, "SELECT canonical_customer_id FROM dw.vw_customer_360"),
                    rows(cursor, "SELECT canonical_customer_id FROM dw.dim_customer"))
    checks.equal("Alice Customer 360 amounts and active products", rows(cursor, """
        SELECT deposit_balance, credit_and_loan_balance, active_product_count
          FROM dw.vw_customer_360 WHERE customer_name = 'Alice Chen'
    """), [(Decimal("20950.00"), Decimal("386850.00"), 4)])
    compare_records(checks, "exactly three expected daily risk review cases",
                    rows(cursor, """
        SELECT customer_name, calendar_date, transaction_count, gross_transaction_value,
               largest_transaction FROM dw.vw_transaction_risk_monitor WHERE review_flag = 'REVIEW'
    """), [("Alice Chen", date(2026, 9, 20), 3, Decimal("1582.30"), Decimal("1250.00")),
            ("Carla Singh", date(2026, 9, 22), 2, Decimal("5040.00"), Decimal("3200.00")),
            ("Emma Brown", date(2026, 9, 23), 2, Decimal("2605.20"), Decimal("2450.00"))])
    checks.equal("Alice is the only seeded product opportunity", rows(cursor, """
        SELECT customer_name, suggested_action FROM dw.vw_product_opportunity
         WHERE suggested_action <> 'NO_RULE_BASED_SUGGESTION'
    """), [("Alice Chen", "CARD_REPAYMENT_SUPPORT_REVIEW")])

    checks.equal("stable Vault hub and relationship counts", dict(rows(cursor, """
        SELECT 'customers', count(*) FROM vault.hub_customer
        UNION ALL SELECT 'accounts', count(*) FROM vault.hub_account
        UNION ALL SELECT 'customer_accounts', count(*) FROM vault.link_customer_account
    """)), {"customers": 8, "accounts": 15, "customer_accounts": 15})
    satellite_counts = dict(rows(cursor, """
        SELECT 'profiles', count(*) FROM vault.sat_customer_profile
        UNION ALL SELECT 'accounts', count(*) FROM vault.sat_account_details
        UNION ALL SELECT 'consents', count(*) FROM vault.sat_customer_consent
    """))
    print(f"INFO Vault satellite snapshots: {satellite_counts}; repeat-load snapshots are allowed")
    for name, minimum in {"profiles": 20, "accounts": 15, "consents": 6}.items():
        checks.at_least(f"Vault {name} snapshot coverage", satellite_counts[name], minimum)
    checks.equal("Vault snapshots cover the seed source business keys", dict(rows(cursor, """
        SELECT 'profiles', count(DISTINCT (record_source, source_customer_id))
          FROM vault.sat_customer_profile
        UNION ALL SELECT 'accounts', count(DISTINCT account_hk) FROM vault.sat_account_details
        UNION ALL SELECT 'consents', count(DISTINCT consent_id) FROM vault.sat_customer_consent
    """)), {"profiles": 20, "accounts": 15, "consents": 6})


def main():
    checks = Checks()
    connection = None
    print("Poverty Bank seed-demo verification (read-only; no writes or fault injection)")
    try:
        connection = psycopg2.connect(
            dbname=os.environ.get("DB_NAME", "poverty_bank"),
            user=os.environ.get("DB_USER", "student"),
            password=os.environ.get("DB_PASSWORD", "student"),
            host=os.environ.get("DB_HOST", "postgres"),
            port=int(os.environ.get("DB_PORT", "5432")),
            connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),
            application_name="poverty_bank_readonly_verification",
        )
        # psycopg2 sends BEGIN with these explicit properties on the first query.
        # All assertions therefore read one consistent, server-enforced read-only
        # transaction. ROLLBACK at exit releases the snapshot without a commit.
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True,
                               autocommit=False)
        with connection.cursor() as cursor:
            if os.environ.get('DATA_PROFILE') == 'showcase':
                from verify_showcase import verify_showcase
                verify_showcase(cursor, checks)
            else:
                verify(cursor, checks)
    except Exception as error:
        checks.failed += 1
        print(f"FAIL verification operation: {type(error).__name__}: {error}", file=sys.stderr)
    finally:
        if connection is not None:
            try:
                connection.rollback()
            except Exception as error:
                checks.failed += 1
                print(f"FAIL read-only transaction cleanup: {type(error).__name__}", file=sys.stderr)
            finally:
                connection.close()
    print(f"RESULT: {checks.passed} passed; {checks.failed} failed")
    print("Scope: supplied seed demo only; no claim of complete authorisation or change-handling tests.")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
