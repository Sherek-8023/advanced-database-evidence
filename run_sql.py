"""Run the Poverty Bank SQL setup and ETL files in order from the lab Python container."""

from pathlib import Path
import os
import sys

import psycopg2


SQL_DIR = Path(__file__).resolve().parent / "sql"
SQL_FILES = [
    "01_create_schemas_and_tables.sql",
    "02_load_synthetic_source_data.sql",
    "03_stage_and_resolve_customers.sql",
    "04_load_data_vault.sql",
    "05_load_warehouse.sql",
    "06_reporting_views.sql",
]


def main():
    profile = os.environ.get('DATA_PROFILE', 'baseline')
    if profile not in ('baseline', 'showcase'):
        raise ValueError('Choose baseline or showcase DATA_PROFILE')
    filenames = list(SQL_FILES)
    if profile == 'showcase':
        filenames.insert(2, '02b_load_showcase_data.sql')
    connection = psycopg2.connect(
        dbname="poverty_bank",
        user="student",
        password="student",
        host="postgres",
        port=5432,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database();")
            print(f"Connected to database: {cursor.fetchone()[0]}")

            for filename in filenames:
                sql_path = SQL_DIR / filename
                print(f"Running {filename} ...")
                try:
                    cursor.execute(sql_path.read_text(encoding="utf-8"))
                    connection.commit()
                except Exception:
                    connection.rollback()
                    print(f"Stopped at {filename}; that file was rolled back.")
                    raise
                print(f"Finished {filename}.")
            # Publish a successful-load timestamp only after data assertions pass.
            connection.set_session(isolation_level='REPEATABLE READ')
            sys.path.insert(0, str(SQL_DIR.parent / 'scripts'))
            from verify_pipeline import Checks, verify
            checks = Checks()
            if profile == 'showcase':
                from verify_showcase import verify_showcase
                verify_showcase(cursor, checks)
            else:
                verify(cursor, checks, expected_read_only='off')
            if checks.failed:
                raise RuntimeError('Loaded data failed verification; no successful load was recorded')
            cursor.execute('''INSERT INTO dw.pipeline_load(profile,data_as_of,source_profiles,transactions)
                SELECT %s,MAX(transaction_ts)::date,
                (SELECT count(*) FROM stg.customer_source),COUNT(*) FROM dw.fact_transaction''', (profile,))
            connection.commit()
            print(f'VERIFIED {profile}: {checks.passed} checks passed.')
    finally:
        connection.close()


if __name__ == "__main__":
    main()
