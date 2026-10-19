"""Execute fixed synthetic scenarios only on the isolated quality cluster."""

import argparse
from contextlib import redirect_stdout
from datetime import date, datetime
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import sys

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_pipeline


SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
SEED_FILES = (
    "01_create_schemas_and_tables.sql", "02_load_synthetic_source_data.sql",
    "03_stage_and_resolve_customers.sql", "04_load_data_vault.sql",
    "05_load_warehouse.sql", "06_reporting_views.sql",
)
CASE_DATABASES = {
    "identity": "quality_identity", "consent": "quality_consent", "types": "quality_types",
}
SCHEMAS = ("src_deposits", "src_cards", "src_lending", "src_crm", "stg", "vault", "dw")
CLUSTER_MARKER = "poverty-bank-a2-isolated-quality-v1"
ALICE = "CUST-07E495D4EE"
REPORTS = ("vw_customer_360", "vw_transaction_risk_monitor", "vw_product_opportunity")
ACTION_NAMES = ("reset", "inject", "process", "run", "status", "evidence", "verify")


class SafetyError(RuntimeError):
    pass


class VerificationError(RuntimeError):
    pass


def json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError("Unsupported evidence value")


def portable(value):
    return json.loads(json.dumps(value, default=json_default))


def connection_options():
    host = os.environ.get("QUALITY_PGHOST", "quality-postgres")
    if host != "quality-postgres":
        raise SafetyError("Quality scenarios require the dedicated quality-postgres service.")
    return {
        "host": host, "port": 5432, "user": "student", "password": "student",
        "connect_timeout": 10, "application_name": "a2_isolated_quality_demo",
        "options": "-c statement_timeout=30000 -c lock_timeout=10000 -c timezone=UTC",
    }


def validate_case(case):
    if case not in CASE_DATABASES:
        raise SafetyError("Unknown quality scenario; only identity, consent and types are allowed.")
    return CASE_DATABASES[case]


def validate_inventory(database_names):
    allowed = {"postgres", "template0", "template1", *CASE_DATABASES.values()}
    if set(database_names) - allowed:
        raise SafetyError("Refusing a cluster containing the main database or an unexpected database.")


def connect(database, readonly=False):
    if database not in {"postgres", *CASE_DATABASES.values()}:
        raise SafetyError("Refusing a database outside the fixed quality whitelist.")
    connection = psycopg2.connect(dbname=database, **connection_options())
    connection.set_session(isolation_level="REPEATABLE READ", readonly=readonly)
    return connection


def ensure_cluster(initialize=False):
    connection = connect("postgres")
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT datname FROM pg_database")
                names = {row[0] for row in cursor.fetchall()}
                validate_inventory(names)
                cursor.execute("SELECT to_regclass('public.a2_quality_cluster')")
                exists = cursor.fetchone()[0] is not None
                if not exists:
                    if not initialize or names != {"postgres", "template0", "template1"}:
                        raise SafetyError("The isolated cluster marker is missing; no data was changed.")
                    cursor.execute("""
                        CREATE TABLE public.a2_quality_cluster (
                            singleton boolean PRIMARY KEY CHECK (singleton), marker text NOT NULL
                        );
                    """)
                    cursor.execute("INSERT INTO public.a2_quality_cluster VALUES (true, %s)",
                                   (CLUSTER_MARKER,))
                cursor.execute("SELECT marker FROM public.a2_quality_cluster WHERE singleton")
                if cursor.fetchall() != [(CLUSTER_MARKER,)]:
                    raise SafetyError("The isolated cluster marker does not match.")
                if initialize:
                    cursor.execute("""
                        DO $$ BEGIN
                          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'report_reader') THEN
                            CREATE ROLE report_reader LOGIN;
                          END IF;
                        END $$;
                        ALTER ROLE report_reader WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                          NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'local_demo_reader';
                        ALTER ROLE report_reader SET default_transaction_read_only = on;
                    """)
    finally:
        connection.close()


def query(cursor, statement, params=None):
    cursor.execute(statement, params)
    columns = [column.name for column in cursor.description]
    return portable([dict(zip(columns, row)) for row in cursor.fetchall()])


def snapshots(cursor):
    return {name: query(cursor, sql.SQL("SELECT * FROM dw.{} ORDER BY canonical_customer_id{}").format(
        sql.Identifier(name), sql.SQL(", calendar_date" if name == "vw_transaction_risk_monitor" else "")
    )) for name in REPORTS}


def create_metadata(connection, case):
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('quality_meta.state')")
            if cursor.fetchone()[0] is None:
                cursor.execute("""
                    SELECT count(*) FROM pg_namespace
                    WHERE nspname NOT IN ('public', 'information_schema') AND nspname NOT LIKE 'pg_%'
                """)
                if cursor.fetchone()[0]:
                    raise SafetyError("Refusing to adopt an existing unmarked scenario database.")
                cursor.execute("SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace")
                if cursor.fetchone()[0]:
                    raise SafetyError("Refusing to adopt a nonempty scenario database.")
                cursor.execute("""
                    CREATE SCHEMA quality_meta;
                    CREATE TABLE quality_meta.state (
                        singleton boolean PRIMARY KEY CHECK (singleton), scenario text NOT NULL,
                        phase text NOT NULL CHECK (phase IN ('initializing','baseline','injected','processed')),
                        baseline_reports jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
                    )
                """)
                cursor.execute("INSERT INTO quality_meta.state VALUES (true, %s, 'initializing', '{}'::jsonb, now())", (case,))


def read_state(cursor, case, lock=False):
    if lock:
        cursor.execute("SELECT pg_advisory_xact_lock(20261013, 32113)")
    cursor.execute("SELECT current_database()")
    if cursor.fetchone()[0] != validate_case(case):
        raise SafetyError("Connected database does not match the selected scenario.")
    cursor.execute("SELECT scenario, phase, baseline_reports FROM quality_meta.state WHERE singleton")
    records = cursor.fetchall()
    if len(records) != 1 or records[0][0] != case:
        raise SafetyError("The scenario ownership marker does not match.")
    return records[0][1], records[0][2]


def set_phase(cursor, phase):
    cursor.execute("UPDATE quality_meta.state SET phase = %s, updated_at = now() WHERE singleton", (phase,))


def run_files(cursor, names):
    for name in names:
        cursor.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def grant_reports(cursor, database):
    cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO report_reader").format(sql.Identifier(database)))
    cursor.execute("""
        GRANT USAGE ON SCHEMA dw TO report_reader;
        GRANT SELECT ON dw.vw_customer_360, dw.vw_transaction_risk_monitor,
          dw.vw_product_opportunity, dw.dim_account, dw.dim_product, dw.pipeline_load,
          dw.vw_transaction_detail, dw.vw_customer_activity, dw.vw_identity_lineage TO report_reader;
    """)


def baseline_checks(cursor, case):
    checks = verify_pipeline.Checks()
    captured = io.StringIO()
    cursor.execute("SELECT current_setting('transaction_read_only')")
    readonly = cursor.fetchone()[0]
    with redirect_stdout(captured):
        verify_pipeline.verify(cursor, checks, expected_database=validate_case(case), expected_read_only=readonly)
    if checks.failed:
        raise VerificationError("Baseline verification failed; the operation was rolled back.")
    return {"passed": checks.passed, "failed": checks.failed}


def collect_evidence(cursor, case, phase, baseline):
    counts = query(cursor, """
        SELECT (SELECT count(*) FROM src_deposits.customer) + (SELECT count(*) FROM src_cards.cardholder)
             + (SELECT count(*) FROM src_lending.borrower) + (SELECT count(*) FROM src_crm.contact) AS source_profiles,
          (SELECT count(*) FROM src_crm.consent) AS source_consents,
          (SELECT count(*) FROM stg.customer_source) AS staged_profiles,
          (SELECT count(*) FROM dw.dim_customer) AS dw_customers,
          (SELECT count(*) FROM dw.dim_account) AS accounts,
          (SELECT count(*) FROM dw.fact_transaction) AS transactions,
          (SELECT count(*) FROM dw.fact_loan_repayment) AS repayments,
          (SELECT count(*) FROM dw.fact_customer_interaction) AS interactions,
          (SELECT count(*) FROM dw.vw_customer_360) AS customer_360_rows,
          (SELECT count(*) FROM dw.vw_transaction_risk_monitor) AS risk_rows,
          (SELECT count(*) FROM dw.vw_product_opportunity) AS product_rows,
          (SELECT count(*) FROM dw.vw_customer_360 WHERE canonical_customer_id = 'CUST-07E495D4EE') AS alice_report_rows
    """)[0]
    current = {
        "counts": counts,
        "source_profiles": query(cursor, """
            SELECT 'SRC_DEPOSITS' AS source_system, customer_id AS source_customer_id,
                   full_name, email, date_of_birth FROM src_deposits.customer WHERE customer_id = 'DQ-DEP-901'
            UNION ALL SELECT 'SRC_CRM', crm_contact_id, full_name, email, date_of_birth
              FROM src_crm.contact WHERE crm_contact_id = 'DQ-CRM-901'
            ORDER BY source_system
        """),
        "staging_profiles": query(cursor, """
            SELECT c.source_system, c.source_customer_id, c.normalized_email, c.date_of_birth,
                   i.canonical_customer_id, i.match_status, i.match_method
              FROM stg.customer_source c JOIN stg.customer_identity i
                USING (source_system, source_customer_id)
             WHERE c.source_customer_id IN ('DQ-DEP-901', 'DQ-CRM-901') ORDER BY c.source_system
        """),
        "source_alice_consents": query(cursor, """
            SELECT consent_id, consent_type, consent_status, updated_at
              FROM src_crm.consent WHERE crm_contact_id = 'CRM-301'
             ORDER BY consent_type, updated_at, consent_id
        """),
        "vault_demo_profiles": query(cursor, """
            SELECT h.canonical_customer_id, s.source_customer_id, s.record_source, s.email, s.date_of_birth
              FROM vault.sat_customer_profile s JOIN vault.hub_customer h USING (customer_hk)
             WHERE s.source_customer_id IN ('DQ-DEP-901', 'DQ-CRM-901')
             GROUP BY h.canonical_customer_id, s.source_customer_id, s.record_source, s.email, s.date_of_birth
             ORDER BY s.record_source
        """),
        "vault_alice_consents": query(cursor, """
            SELECT DISTINCT s.consent_id, s.consent_type, s.consent_status, s.consent_updated_at
              FROM vault.sat_customer_consent s JOIN vault.hub_customer h USING (customer_hk)
             WHERE h.canonical_customer_id = %s
             ORDER BY s.consent_type, s.consent_updated_at, s.consent_id
        """, (ALICE,)),
        "dw_alice_consents": query(cursor, """
            SELECT consent_type, consent_status, updated_at FROM dw.customer_consent
             WHERE canonical_customer_id = %s ORDER BY consent_type
        """, (ALICE,)),
        "customer_360_focus": query(cursor, """
            SELECT * FROM dw.vw_customer_360
             WHERE canonical_customer_id = %s OR identity_status = 'AMBIGUOUS'
             ORDER BY canonical_customer_id
        """, (ALICE,)),
    }
    return {
        "case": case, "database": validate_case(case), "phase": phase,
        "baseline": {"customer_360_rows": len(baseline.get(REPORTS[0], [])),
                     "risk_rows": len(baseline.get(REPORTS[1], [])),
                     "product_rows": len(baseline.get(REPORTS[2], [])),
                     "alice_cdr_status": "ACTIVE", "source_profiles": 20, "source_consents": 6},
        "current": current,
    }


def scenario_checks(cursor, case, phase, baseline):
    if phase == "baseline":
        return baseline_checks(cursor, case)
    if phase not in {"injected", "processed"}:
        raise VerificationError("The scenario is not initialized.")
    current = collect_evidence(cursor, case, phase, baseline)["current"]
    processed = phase == "processed"
    assertions = []

    def expect(label, actual, expected):
        assertions.append(actual == expected)
        if actual != expected:
            raise VerificationError("Scenario verification failed: " + label + "; operation rolled back.")

    count = current["counts"]
    identity_processed = case == "identity" and processed
    expect("source profile count", count["source_profiles"], 22 if case == "identity" else 20)
    expect("source consent count", count["source_consents"], {"identity": 6, "consent": 9, "types": 7}[case])
    expect("staging profile count", count["staged_profiles"], 22 if identity_processed else 20)
    expect("warehouse customer count", count["dw_customers"], 10 if identity_processed else 8)
    for key, expected in {"accounts": 15, "transactions": 14, "repayments": 4, "interactions": 5,
                          "customer_360_rows": 10 if identity_processed else 8,
                          "risk_rows": 8, "product_rows": 10 if identity_processed else 8,
                          "alice_report_rows": 1}.items():
        expect(key, count[key], expected)
    reports = snapshots(cursor)
    for report in REPORTS:
        expected_rows = [dict(row) for row in baseline[report]]
        if processed and case == "consent" and report == REPORTS[0]:
            for row in expected_rows:
                if row["canonical_customer_id"] == ALICE:
                    row.update(cdr_consent_status="WITHDRAWN", consent_updated_at="2026-09-25T10:00:00")
        actual_rows = reports[report]
        if identity_processed:
            actual_rows = [row for row in actual_rows if not row["canonical_customer_id"].startswith("AMBIGUOUS-")]
        expect("existing report rows: " + report, actual_rows, expected_rows)

    if case == "identity":
        expected_profiles = [
            {"source_system": "SRC_CRM", "source_customer_id": "DQ-CRM-901", "full_name": "Jordan Lee",
             "email": "jordan.shared@example.test", "date_of_birth": "1992-11-03"},
            {"source_system": "SRC_DEPOSITS", "source_customer_id": "DQ-DEP-901", "full_name": "Jordan Lee",
             "email": "  Jordan.Shared@example.test  ", "date_of_birth": "1988-04-12"},
        ]
        expect("raw profiles preserve the conflict", current["source_profiles"], expected_profiles)
        staged = current["staging_profiles"]
        expect("scenario staging rows", len(staged), 2 if processed else 0)
        expect("scenario Vault profile coverage", len(current["vault_demo_profiles"]), 2 if processed else 0)
        if processed:
            expect("two isolated canonical identities", len({row["canonical_customer_id"] for row in staged}), 2)
            for row in staged:
                expect("ambiguous status", row["match_status"], "AMBIGUOUS")
                expect("normalized shared email", row["normalized_email"], "jordan.shared@example.test")
                expect("manual review reason", row["match_method"], "EMAIL_HAS_CONFLICTING_DOB_REVIEW_REQUIRED")
            new_rows = [row for row in reports[REPORTS[0]] if row["identity_status"] == "AMBIGUOUS"]
            expect("two ambiguous report rows", len(new_rows), 2)
            for row in new_rows:
                expect("no invented accounts", row["active_product_count"], 0)
                expect("no invented CDR permission", row["cdr_consent_status"], None)
    else:
        demo_source = [row for row in current["source_alice_consents"] if row["consent_id"].startswith("DQ-")]
        expected_events = {
            "consent": [
                {"consent_id": "DQ-CDR-OLD", "consent_type": "CDR_DATA_SHARING", "consent_status": "ACTIVE", "updated_at": "2026-09-15T09:00:00"},
                {"consent_id": "DQ-CDR-TIED-ACTIVE", "consent_type": "CDR_DATA_SHARING", "consent_status": "ACTIVE", "updated_at": "2026-09-25T10:00:00"},
                {"consent_id": "DQ-CDR-WITHDRAWN", "consent_type": "CDR_DATA_SHARING", "consent_status": "WITHDRAWN", "updated_at": "2026-09-25T10:00:00"},
            ],
            "types": [{"consent_id": "DQ-MARKETING-901", "consent_type": "MARKETING", "consent_status": "WITHDRAWN", "updated_at": "2026-09-26T09:00:00"}],
        }[case]
        expect("raw scenario events", demo_source, expected_events)
        expected_dw = [{"consent_type": "CDR_DATA_SHARING", "consent_status": "ACTIVE", "updated_at": "2026-09-18T12:00:00"}]
        if processed and case == "consent":
            expected_dw[0].update(consent_status="WITHDRAWN", updated_at="2026-09-25T10:00:00")
        if processed and case == "types":
            expected_dw.append({"consent_type": "MARKETING", "consent_status": "WITHDRAWN", "updated_at": "2026-09-26T09:00:00"})
        expect("current consent rows by purpose", current["dw_alice_consents"], expected_dw)
        vault_demo = [row for row in current["vault_alice_consents"] if row["consent_id"].startswith("DQ-")]
        expected_vault = [{"consent_id": row["consent_id"], "consent_type": row["consent_type"],
                           "consent_status": row["consent_status"], "consent_updated_at": row["updated_at"]}
                          for row in expected_events] if processed else []
        expect("Vault retains every source event", vault_demo, expected_vault)
    return {"passed": len(assertions), "failed": 0}


def execute_case(action, case):
    database = validate_case(case)
    if action not in ACTION_NAMES or action == "run":
        raise SafetyError("Unknown individual scenario operation.")
    ensure_cluster()
    readonly = action in {"status", "evidence", "verify"}
    connection = connect(database, readonly=readonly)
    try:
        with connection:
            with connection.cursor() as cursor:
                phase, baseline = read_state(cursor, case, lock=not readonly)
                if action == "reset":
                    for schema in reversed(SCHEMAS):
                        cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
                    run_files(cursor, SEED_FILES)
                    baseline_checks(cursor, case)
                    baseline = snapshots(cursor)
                    cursor.execute("UPDATE quality_meta.state SET baseline_reports = %s WHERE singleton", (Json(baseline),))
                    grant_reports(cursor, database)
                    phase = "baseline"
                    set_phase(cursor, phase)
                elif action == "inject":
                    if phase == "initializing":
                        raise SafetyError("Initialize or reset this scenario first.")
                    if phase == "baseline":
                        run_files(cursor, ("scenarios/" + case + ".sql",))
                        phase = "injected"
                        set_phase(cursor, phase)
                elif action == "process":
                    if phase not in {"injected", "processed"}:
                        raise SafetyError("Inject the scenario before processing it.")
                    # All four stages, their assertions and state commit together.
                    run_files(cursor, SEED_FILES[2:])
                    phase = "processed"
                    set_phase(cursor, phase)
                checks = scenario_checks(cursor, case, phase, baseline)
                result = collect_evidence(cursor, case, phase, baseline)
                result["checks"] = checks
                return result
    finally:
        connection.close()


def initialize():
    ensure_cluster(initialize=True)
    results = []
    admin = connect("postgres")
    admin.autocommit = True
    try:
        for case, database in CASE_DATABASES.items():
            with admin.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
                if cursor.fetchone() is None:
                    cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
            connection = connect(database)
            try:
                create_metadata(connection, case)
                with connection:
                    with connection.cursor() as cursor:
                        phase, _ = read_state(cursor, case)
            finally:
                connection.close()
            if phase == "initializing":
                execute_case("reset", case)
                execute_case("inject", case)
                results.append(execute_case("process", case))
            else:
                results.append(execute_case("verify", case))
    finally:
        admin.close()
    return results


def command(action, case="all"):
    if action == "init":
        if case != "all":
            raise SafetyError("Initialization applies to the isolated cluster, not one scenario.")
        return {"action": action, "results": initialize()}
    if action not in ACTION_NAMES:
        raise SafetyError("Unknown quality scenario operation.")
    cases = list(CASE_DATABASES) if case == "all" else [case]
    for selected in cases:
        validate_case(selected)
    results = []
    for selected in cases:
        if action == "run":
            execute_case("reset", selected)
            execute_case("inject", selected)
            results.append(execute_case("process", selected))
        else:
            results.append(execute_case(action, selected))
    return {"action": action, "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Isolated, fixed synthetic quality scenarios.")
    parser.add_argument("action", choices=("init", *ACTION_NAMES))
    parser.add_argument("case", nargs="?", default="all", choices=(*CASE_DATABASES, "all"))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(command(args.action, args.case), ensure_ascii=False, indent=2, default=json_default))
        return 0
    except (SafetyError, VerificationError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
    except Exception as error:
        # SQL errors can contain source paths, credentials or raw connection details.
        print(json.dumps({"ok": False, "error": "Quality operation failed (" + type(error).__name__ + "). Check the isolated service and scenario SQL."}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
