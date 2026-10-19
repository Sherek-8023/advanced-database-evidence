"""HTTP boundary tests without a database, network listener or browser."""
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SPEC = importlib.util.spec_from_file_location("report_server", Path(__file__).resolve().parents[1] / "web" / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class MemorySocket:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = bytearray()

    def makefile(self, *args, **kwargs):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


def request(path="/api/reports", method="GET", host="localhost:18080", extra_headers=None):
    headers = {"Host": host, **(extra_headers or {})}
    lines = [f"{method} {path} HTTP/1.0"] + [f"{key}: {value}" for key, value in headers.items()]
    sock = MemorySocket(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    server.ReportHandler(sock, ("127.0.0.1", 0), object())
    raw_headers, _, body = bytes(sock.response).partition(b"\r\n\r\n")
    header_lines = raw_headers.decode().split("\r\n")
    status = int(header_lines[0].split()[1])
    response_headers = dict(line.split(": ", 1) for line in header_lines[1:])
    return status, response_headers, body


class HttpTests(unittest.TestCase):
    def test_serialization_preserves_types_and_naive_timestamps(self):
        data = {"amount": Decimal("12.50"), "missing": None, "flag": False,
                "date": date(2026, 9, 20), "timestamp": datetime(2026, 9, 20, 10)}
        with patch.object(server, "all_reports", return_value=data):
            status, headers, body = request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"amount": 12.5, "missing": None, "flag": False,
                                          "date": "2026-09-20", "timestamp": "2026-09-20T10:00:00"})
        self.assertNotIn("Python", headers["Server"])
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_external_host_and_origin_are_rejected_before_db(self):
        with patch.object(server, "all_reports") as query:
            for host in ("evil.example", "localhost.evil.example", "localhost:0", "localhost:70000", "user@localhost"):
                self.assertEqual(request(host=host)[0], 403)
            self.assertEqual(request(extra_headers={"Origin": "https://evil.example"})[0], 403)
            query.assert_not_called()

    def test_same_origin_allowed(self):
        with patch.object(server, "all_reports", return_value={}):
            self.assertEqual(request(extra_headers={"Origin": "http://localhost:18080"})[0], 200)
            self.assertEqual(request(host="127.0.0.1:8080")[0], 200)
            self.assertEqual(request(host="[::1]:8080")[0], 200)

    def test_unknown_paths_and_encoded_traversal_cannot_serve_files(self):
        for path in ("/api/unknown", "/server.py", "/../server.py", "/%2e%2e/server.py", "/.env", "/docs/index.html"):
            status, _, _ = request(path)
            self.assertEqual(status, 404, path)

    def test_static_file_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "index.html").write_text("<p>Reports</p>")
            Path(directory, "icons.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
            Path(directory, "secret.txt").write_text("not public")
            with patch.object(server, "STATIC_DIR", Path(directory)):
                self.assertEqual(request("/")[2], b"<p>Reports</p>")
                status, headers, body = request("/icons.svg")
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], "image/svg+xml")
                self.assertEqual(body, b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
                self.assertEqual(request("/secret.txt")[0], 404)

    def test_mutations_are_rejected(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"):
            status, headers, _ = request(method=method)
            self.assertEqual(status, 405)
            self.assertEqual(headers["Allow"], "GET")

    def test_safe_database_failure(self):
        with patch.object(server, "all_reports", side_effect=server.psycopg2.OperationalError("private connection detail")):
            status, _, body = request()
        self.assertEqual(status, 503)
        self.assertNotIn(b"private connection detail", body)
        with patch.object(server, "reports_ready", side_effect=server.psycopg2.OperationalError("private connection detail")):
            status, _, body = request("/api/health")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"ok": False, "reports_ready": False})

    def test_export_is_allowlisted(self):
        with patch.object(server, "report_csv", return_value=b"name\r\nAlice\r\n") as export:
            status, headers, body = request("/api/export?report=customer_360")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Disposition"], 'attachment; filename="customer_360.csv"')
            self.assertEqual(body, b"name\r\nAlice\r\n")
            export.assert_called_once_with("customer_360")
            for query in ("report=src_crm.contact", "report=../.env", "report=customer_360&report=transaction_risk", "report=customer_360&sql=SELECT"):
                self.assertEqual(request("/api/export?" + query)[0], 400)
            self.assertEqual(export.call_count, 1)

    def test_customer_id_validation_and_parameter_binding(self):
        for query in ("", "customer_id=", "customer_id=CUST-ABC'OR1=1", "customer_id=CUST-0123456789&customer_id=CUST-0123456789"):
            self.assertEqual(request("/api/accounts?" + query)[0], 400)
        cursor = MagicMock()
        with patch.object(server, "database_cursor") as database, patch.object(server, "query_rows", return_value=[]) as rows:
            database.return_value.__enter__.return_value = cursor
            for prefix in ("CUST", "UNMATCHED", "AMBIGUOUS"):
                customer = prefix + "-0123456789"
                status, _, body = request("/api/accounts?customer_id=" + customer)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {"accounts": []})
                rows.assert_called_with(cursor, server.ACCOUNT_SQL, (customer,))


class DataTests(unittest.TestCase):
    def test_readonly_transaction_and_close_on_failure(self):
        connection = MagicMock()
        with patch.object(server.psycopg2, "connect", return_value=connection) as connect:
            with self.assertRaises(RuntimeError):
                with server.database_cursor():
                    raise RuntimeError("test")
        connection.set_session.assert_called_once_with(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()
        self.assertIn("statement_timeout=5000", connect.call_args.kwargs["options"])
        self.assertIn("default_transaction_read_only=on", connect.call_args.kwargs["options"])

    def test_reports_share_one_snapshot_and_period_comes_from_risk(self):
        records = [[], [{"calendar_date": date(2026, 9, 21)}, {"calendar_date": date(2026, 9, 20)}], [], []]
        before = datetime.now(timezone.utc)
        with patch.object(server, "database_cursor") as database, patch.object(server, "query_rows", side_effect=records):
            data = server.all_reports()
        after = datetime.now(timezone.utc)
        database.assert_called_once()
        self.assertEqual(data["meta"]["data_period"], {"from": date(2026, 9, 20), "to": date(2026, 9, 21)})
        self.assertTrue(data["meta"]["generated_at"].endswith("Z"))
        refreshed = datetime.fromisoformat(data["meta"]["generated_at"].replace("Z", "+00:00"))
        self.assertGreaterEqual(refreshed, before)
        self.assertLessEqual(refreshed, after)

    def test_empty_reports_have_null_period(self):
        with patch.object(server, "database_cursor"), patch.object(server, "query_rows", return_value=[]):
            self.assertEqual(server.all_reports()["meta"]["data_period"], {"from": None, "to": None})

    def test_demo_context_only_exposes_known_labels(self):
        for configured, expected in (("Identity conflict", "Identity conflict"),
                                     ("Consent events", "Consent events"),
                                     ("Consent types", "Consent types"),
                                     ("private configuration", None), ("", None)):
            with self.subTest(configured=configured), \
                    patch.dict(server.os.environ, {"REPORT_DEMO_LABEL": configured}), \
                    patch.object(server, "database_cursor"), \
                    patch.object(server, "query_rows", return_value=[]):
                self.assertEqual(server.all_reports()["meta"]["demo_label"], expected)

    def test_csv_formula_guard_preserves_numeric_values(self):
        for value in ("=HYPERLINK()", "+phone", "-text", "@SUM()", "   =1+1", "\tvalue", "\nvalue", "\rvalue"):
            self.assertEqual(server.csv_value(value), "'" + value)
        for value in (None, -1500, Decimal("-15.25"), "Alice", "CUST-0123456789"):
            self.assertEqual(server.csv_value(value), value)
        cursor = MagicMock()
        cursor.description = [("name",), ("balance",), ("missing",)]
        cursor.fetchall.return_value = [("=formula", Decimal("-15.25"), None)]
        with patch.object(server, "database_cursor") as database:
            database.return_value.__enter__.return_value = cursor
            content = server.report_csv("customer_360").decode()
        self.assertEqual(list(csv.reader(io.StringIO(content))), [["name", "balance", "missing"], ["'=formula", "-15.25", ""]])


if __name__ == "__main__":
    unittest.main()
