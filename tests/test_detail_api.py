"""Validation and parameter binding for the new read-only detail routes."""
import unittest
from unittest.mock import MagicMock, patch
from test_report_api import request, server


class DetailApiTests(unittest.TestCase):
    def test_transaction_date_is_strict_and_never_interpolated(self):
        customer = 'CUST-0123456789'
        with patch.object(server,'database_cursor') as db, patch.object(server,'query_rows',return_value=[]) as query:
            for value in ('2026-02-30', 'not-a-date', '2026-01-01%27', '2026-1-1'):
                self.assertEqual(request('/api/transactions?customer_id='+customer+'&date='+value)[0],400)
            query.assert_not_called()
            self.assertEqual(request('/api/transactions?customer_id='+customer+'&date=2026-10-01')[0],200)
            sql, params = query.call_args.args[1:]
            self.assertNotIn(customer,sql)
            self.assertEqual(params[0],customer)

    def test_detail_routes_reject_unknown_or_duplicate_filters(self):
        with patch.object(server,'query_rows') as query:
            for route in ('activity','identity','transactions'):
                for suffix in ('', '?customer_id=bad', '?customer_id=CUST-0123456789&sql=DELETE'):
                    self.assertEqual(request('/api/'+route+suffix)[0],400)
            query.assert_not_called()

    def test_source_context_uses_parameterized_read_queries(self):
        with patch.object(server,'database_cursor'), patch.object(server,'query_rows',return_value=[]) as query:
            for route in ('activity','identity'):
                self.assertEqual(request('/api/'+route+'?customer_id=CUST-0123456789')[0],200)
                self.assertEqual(query.call_args.args[2],('CUST-0123456789',))
