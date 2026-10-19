"""Execute the read-only validation queries and print their result sets."""

from pathlib import Path

import psycopg2


SQL_PATH = Path(__file__).resolve().parent / "sql" / "07_validation_queries.sql"


def statements_from_file(text):
    # This validation file contains simple semicolon-terminated SQL statements;
    # remove full-line comments before splitting them for individual result sets.
    uncommented = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    return [statement.strip() for statement in uncommented.split(";") if statement.strip()]


def main():
    connection = psycopg2.connect(
        dbname="poverty_bank",
        user="student",
        password="student",
        host="postgres",
        port=5432,
    )
    try:
        with connection.cursor() as cursor:
            print("Connected to database: poverty_bank")
            for index, statement in enumerate(
                statements_from_file(SQL_PATH.read_text(encoding="utf-8")), start=1
            ):
                cursor.execute(statement)
                columns = [column.name for column in cursor.description]
                print(f"\nValidation query {index}:")
                print(" | ".join(columns))
                for row in cursor.fetchall():
                    print(" | ".join(str(value) for value in row))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
