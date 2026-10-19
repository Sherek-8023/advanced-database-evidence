"""Initialize and verify the demo, then grant a dedicated reporting login."""

from pathlib import Path
import sys

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_sql
import verify_pipeline


def main():
    try:
        run_sql.main()
        connection = psycopg2.connect(
            host="postgres", port=5432, dbname="poverty_bank",
            user="student", password="student", connect_timeout=5,
        )
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("""
                        DO $$ BEGIN
                          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'report_reader') THEN
                            CREATE ROLE report_reader LOGIN;
                          END IF;
                        END $$;
                        ALTER ROLE report_reader WITH LOGIN NOSUPERUSER NOCREATEDB
                          NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
                          PASSWORD 'local_demo_reader';
                        ALTER ROLE report_reader SET default_transaction_read_only = on;
                        GRANT CONNECT ON DATABASE poverty_bank TO report_reader;
                        GRANT USAGE ON SCHEMA dw TO report_reader;
                        GRANT SELECT ON dw.vw_customer_360,
                          dw.vw_transaction_risk_monitor, dw.vw_product_opportunity,
                          dw.dim_account, dw.dim_product, dw.pipeline_load,
                          dw.vw_transaction_detail, dw.vw_customer_activity,
                          dw.vw_identity_lineage TO report_reader;
                    """)
        finally:
            connection.close()
        print("Reporting initialization complete; dedicated read-only login ready.")
        return 0
    except Exception as exc:
        # Avoid traceback paths or connection details in distributable logs.
        print(f"Reporting initialization failed ({type(exc).__name__}). Check database readiness and SQL files.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
