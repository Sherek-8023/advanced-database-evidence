"""Read-only live HTTP checks against the running local dashboard, not mocks."""
import csv
from decimal import Decimal
import io
import json
import os
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen


BASE = os.environ.get('REPORT_BASE_URL', 'http://127.0.0.1:8080').rstrip('/')
target = urlsplit(BASE)
if target.scheme != 'http' or target.hostname not in ('localhost', '127.0.0.1', '::1'):
    raise ValueError('Live checks are restricted to the local demo')


def read(path, **parameters):
    url = BASE + path + ('?' + urlencode(parameters) if parameters else '')
    with urlopen(url, timeout=15) as response:
        return response.read().decode('utf-8-sig')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    data = json.loads(read('/api/reports'))
    require(json.loads(read('/api/health'))['reports_ready'], 'Reports not ready')
    for report in ('customer_360', 'transaction_risk', 'product_opportunity'):
        exported = list(csv.DictReader(io.StringIO(read('/api/export', report=report))))
        require(len(exported) == len(data[report]), f'{report}: export row count')
        require([r['canonical_customer_id'] for r in exported] ==
                [r['canonical_customer_id'] for r in data[report]], f'{report}: export identities')
    print('PASS all three live reports and full CSV exports')
    for customer in data['customer_360']:
        key = customer['canonical_customer_id']
        accounts = json.loads(read('/api/accounts', customer_id=key))['accounts']
        require(sum(a['account_status'] in ('OPEN','ACTIVE') for a in accounts) ==
                customer['active_product_count'], 'Account active count mismatch')
        for kind in ('identity', 'activity'):
            records = json.loads(read('/api/'+kind, customer_id=key))['rows']
            require(all(r['canonical_customer_id'] == key for r in records), 'Cross-customer detail leak')
            if kind == 'identity':
                require(bool(records), 'Source lineage missing')
            else:
                require(len(records) <= 20, 'Activity limit not respected')
                require([r['event_ts'] for r in records] == sorted(
                    (r['event_ts'] for r in records), reverse=True), 'Activity order')
    print(f"PASS accounts, source lineage and recent activity for all {len(data['customer_360'])} identities")
    risks = data['transaction_risk']
    examples = risks[:10] + [max(risks,key=lambda r:r['transaction_count']),
                            max(risks,key=lambda r:r['largest_transaction'])]
    for row in examples:
        details = json.loads(read('/api/transactions', customer_id=row['canonical_customer_id'],
                                 date=row['calendar_date']))['rows']
        amounts = [abs(Decimal(str(r['amount']))) for r in details]
        require(len(details) == row['transaction_count'], 'Drilldown count mismatch')
        require(sum(amounts) == Decimal(str(row['gross_transaction_value'])), 'Drilldown gross mismatch')
        require(max(amounts) == Decimal(str(row['largest_transaction'])), 'Drilldown largest mismatch')
        require(all(r['canonical_customer_id'] == row['canonical_customer_id'] and
                    r['transaction_ts'][:10] == row['calendar_date'] for r in details), 'Drilldown scope')
    print(f'PASS live drilldown counts, gross values, largest amounts and scope for {len(examples)} customer-days')
    print('LIVE HTTP SMOKE CHECKS PASSED (no database mutations)')


if __name__ == '__main__':
    main()
