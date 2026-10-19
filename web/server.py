"""Read-only local reporting service; no ETL or database mutation endpoints."""
from contextlib import contextmanager
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg2


STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/icons.svg": ("icons.svg", "image/svg+xml"),
}
REPORTS = {
    "customer_360": (
        "dw.vw_customer_360",
        "canonical_customer_id, customer_name, email, phone, identity_status, "
        "active_product_count, deposit_balance, credit_and_loan_balance, "
        "cdr_consent_status, consent_updated_at, latest_activity",
        "canonical_customer_id",
    ),
    "transaction_risk": (
        "dw.vw_transaction_risk_monitor",
        "canonical_customer_id, customer_name, calendar_date, transaction_count, "
        "gross_transaction_value, largest_transaction, review_flag",
        "canonical_customer_id, calendar_date",
    ),
    "product_opportunity": (
        "dw.vw_product_opportunity",
        "canonical_customer_id, customer_name, total_deposits, card_balance, "
        "has_credit_card, has_loan, suggested_action, identity_status, review_readiness, rule_reason",
        "canonical_customer_id",
    ),
}
ACCOUNT_SQL = """
SELECT a.source_system, a.source_account_id, p.product_type, p.product_name,
       a.opened_on, a.current_balance, a.account_status
FROM dw.dim_account a
JOIN dw.dim_product p ON p.product_key = a.product_key
WHERE a.canonical_customer_id = %s
ORDER BY a.source_system, a.source_account_id
"""
CUSTOMER_ID = re.compile(r"(?:CUST|UNMATCHED|AMBIGUOUS)-[A-F0-9]{10}\Z")
LOCAL_HOST = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?\Z", re.I)


def json_value(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite report value")
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError("Unsupported report value")


def csv_value(value):
    """Escape formulas only in text; signed numeric values stay numeric."""
    if isinstance(value, str):
        significant = value.lstrip()
        if (significant and significant[0] in "=+-@") or value.startswith(("\t", "\r", "\n")):
            return "'" + value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


@contextmanager
def database_cursor():
    connection = psycopg2.connect(
        host=os.environ.get("PGHOST", "postgres"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "poverty_bank"),
        user=os.environ.get("PGUSER", "student"),
        password=os.environ.get("PGPASSWORD", "student"),
        connect_timeout=4,
        application_name="a2_report_reader",
        options="-c statement_timeout=5000 -c lock_timeout=2000 -c default_transaction_read_only=on",
    )
    try:
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            yield cursor
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def report_query(name):
    table, columns, ordering = REPORTS[name]
    return f"SELECT {columns} FROM {table} ORDER BY {ordering}"


def query_rows(cursor, sql, params=None):
    cursor.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def all_reports():
    with database_cursor() as cursor:
        reports = {name: query_rows(cursor, report_query(name)) for name in REPORTS}
        loads = query_rows(cursor, '''SELECT profile,completed_at,data_as_of,source_profiles,transactions
            FROM dw.pipeline_load ORDER BY load_id DESC LIMIT 1''')
    dates = [row["calendar_date"] for row in reports["transaction_risk"] if row["calendar_date"] is not None]
    return {
        "meta": {
            "demo_label": {
                "Identity conflict": "Identity conflict",
                "Consent events": "Consent events",
                "Consent types": "Consent types",
            }.get(os.environ.get("REPORT_DEMO_LABEL")),
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "currency": "AUD",
            "data_period": {"from": min(dates) if dates else None, "to": max(dates) if dates else None},
            "source": "PostgreSQL reporting views",
            "last_load": loads[0] if loads else None,
        },
        **reports,
    }


def reports_ready():
    with database_cursor() as cursor:
        for name in REPORTS:
            cursor.execute(report_query(name) + " LIMIT 1")
            cursor.fetchall()


def report_csv(name):
    with database_cursor() as cursor:
        cursor.execute(report_query(name))
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)
    writer.writerows([[csv_value(value) for value in row] for row in rows])
    return output.getvalue().encode("utf-8")


class ReportHandler(BaseHTTPRequestHandler):
    server_version = "A2Reports"
    sys_version = ""

    def version_string(self):
        return self.server_version

    def log_message(self, format_string, *args):
        # Do not log URLs, customer IDs, connection details or exception text.
        pass

    def send_error(self, code, message=None, explain=None):
        # The stdlib parser can call this before routing; never echo its input.
        self.send_json(code, {"error": HTTPStatus(code).phrase})

    def send_bytes(self, status, payload, content_type, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def send_json(self, status, data):
        payload = json.dumps(data, default=json_value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_bytes(status, payload, "application/json; charset=utf-8")

    def local_request(self):
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1:
            return False
        host = hosts[0]
        match = LOCAL_HOST.fullmatch(host)
        if not match or (match.group(1) and not 1 <= int(match.group(1)) <= 65535):
            return False
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1:
            return False
        if origins:
            try:
                origin = urlsplit(origins[0])
                if origin.scheme not in ("http", "https") or origin.netloc.lower() != host.lower():
                    return False
                if origin.path or origin.query or origin.fragment:
                    return False
            except ValueError:
                return False
        return True

    def do_GET(self):
        if not self.local_request():
            self.send_json(403, {"error": "Only local, same-origin access is allowed."})
            return
        try:
            self.route_get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except psycopg2.Error:
            self.send_json(503, {"error": "Reports are unavailable. Check database readiness and retry."})
        except Exception:
            self.send_json(500, {"error": "The report request could not be completed."})

    def route_get(self):
        try:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment or len(self.path) > 2048:
                raise ValueError
            path = unquote(parsed.path, errors="strict")
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "Invalid request URL."})
            return
        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            try:
                data = (STATIC_DIR / filename).read_bytes()
            except OSError:
                self.send_json(404, {"error": "Page not found."})
                return
            self.send_bytes(200, data, content_type)
        elif path == "/api/health" and not query:
            try:
                reports_ready()
            except psycopg2.Error:
                self.send_json(503, {"ok": False, "reports_ready": False})
                return
            self.send_json(200, {"ok": True, "reports_ready": True})
        elif path == "/api/reports" and not query:
            self.send_json(200, all_reports())
        elif path == "/api/accounts":
            values = query.get("customer_id", [])
            if set(query) != {"customer_id"} or len(values) != 1 or not CUSTOMER_ID.fullmatch(values[0]):
                self.send_json(400, {"error": "Provide one valid customer ID."})
                return
            with database_cursor() as cursor:
                accounts = query_rows(cursor, ACCOUNT_SQL, (values[0],))
            self.send_json(200, {"accounts": accounts})
        elif path in ("/api/activity", "/api/identity", "/api/transactions"):
            identifiers = query.get('customer_id', [])
            allowed = {'customer_id','date'} if path == '/api/transactions' else {'customer_id'}
            if set(query) != allowed or len(identifiers) != 1 or not CUSTOMER_ID.fullmatch(identifiers[0]):
                self.send_json(400, {'error':'Provide one valid customer ID and the required filters.'})
                return
            params = (identifiers[0],)
            if path == '/api/transactions':
                values = query.get('date', [])
                try:
                    if len(values) != 1 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',values[0]):
                        raise ValueError
                    day = date.fromisoformat(values[0])
                except ValueError:
                    self.send_json(400, {'error':'Choose one valid ISO calendar date.'})
                    return
                statement = '''SELECT * FROM dw.vw_transaction_detail WHERE canonical_customer_id=%s
                    AND transaction_ts >= %s::date AND transaction_ts < %s::date + interval '1 day'
                    ORDER BY transaction_ts,source_system,source_transaction_id'''
                params = (identifiers[0],day,day)
            elif path == '/api/activity':
                statement = '''SELECT * FROM dw.vw_customer_activity WHERE canonical_customer_id=%s
                    ORDER BY event_ts DESC,event_type,event_id LIMIT 20'''
            else:
                statement = '''SELECT * FROM dw.vw_identity_lineage WHERE canonical_customer_id=%s
                    ORDER BY source_system,source_customer_id'''
            with database_cursor() as cursor:
                records = query_rows(cursor,statement,params)
            self.send_json(200, {'rows':records})
        elif path == "/api/export":
            values = query.get("report", [])
            if set(query) != {"report"} or len(values) != 1 or values[0] not in REPORTS:
                self.send_json(400, {"error": "Choose an available report to export."})
                return
            name = values[0]
            self.send_bytes(200, report_csv(name), "text/csv; charset=utf-8", {
                "Content-Disposition": f'attachment; filename="{name}.csv"',
            })
        elif path in ("/api/health", "/api/reports"):
            self.send_json(400, {"error": "This endpoint does not accept query parameters."})
        else:
            self.send_json(404, {"error": "Page not found."})

    def method_not_allowed(self):
        self.close_connection = True
        self.send_bytes(405, b'{"error":"Only GET requests are supported."}', "application/json; charset=utf-8", {"Allow": "GET"})

    do_POST = method_not_allowed
    do_PUT = method_not_allowed
    do_PATCH = method_not_allowed
    do_DELETE = method_not_allowed
    do_OPTIONS = method_not_allowed
    do_HEAD = method_not_allowed
    do_TRACE = method_not_allowed
    do_CONNECT = method_not_allowed


class ReportServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Avoid default traceback logging, which can contain local paths.
        pass


def main():
    try:
        server = ReportServer((os.environ.get("WEB_HOST", "0.0.0.0"), int(os.environ.get("WEB_PORT", "8080"))), ReportHandler)
        print("Local reporting service started.", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        print("Reporting service could not start. Check local service configuration.", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
