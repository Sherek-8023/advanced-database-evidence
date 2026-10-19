# Verified results — 16 October 2026

实际在本地 Docker PostgreSQL 15 / Python 3.11 中执行；不是按功能列表推测通过。

## Data

- Source profiles: Deposits 39, Cards 30, Lending 14, CRM 37; total 120.
- Canonical identities: 50 = 44 MATCHED + 4 UNMATCHED + 2 AMBIGUOUS.
- Accounts 92; transaction facts 1,968; repayment facts 62; CRM interaction facts 92.
- Transaction dates: 2026-07-01 through 2026-10-15, all 107 dates represented.
- Daily risk report: 1,704 customer-days; 142 REVIEW customer-days.
- Product review: 13 loan reviews, 9 card balance reviews, 3 credit card reviews, 25 no-suggestion results.

## Automated verification

- `test_*api.py`: 17 passed. Parameterized queries, strict dates, duplicate/unknown filters, allowlisted exports/static files, mutation rejection, same-origin/Host checks and safe errors.
- `test_consent_sql.py`: 12 passed, using a randomly named disposable database. Latest consent/type/tie rules, replay, closed cards, ambiguous identity recommendation guard, and unknown CRM-only holdings.
- `test_quality_demo.py`: 12 passed (6 safety units + 6 live integration tests), using only the newly created optimized-quality cluster. Identity conflict isolation, withdrawal tie handling, additional consent types, failed-load rollback, reset stability and reader permissions.
- `verify_pipeline.py` with showcase profile: 24 passed. Independently reconstructed raw-source identities and transaction ownership, Vault hubs/links/latest satellites, full fact content and report/drilldown agreement.
- `smoke_reports.py`: passed against the running HTTP service, not a mock. All 50 identities' accounts, lineage and recent activity; all 3 full CSVs; 12 selected customer-day drilldowns including high-count/high-value cases.
- Full main-pipeline replay: before/after contents of all three reports and all three fact tables were identical. Vault load snapshots and successful-load metadata are intentionally append-only.
- JavaScript syntax check: passed.
- Baseline `run_sql.py` execution in another randomly named disposable database: all 22 baseline checks passed, confirming the original small seed profile still works.

## Browser checks

- Customer pagination changed from page 1 to page 2; AMBIGUOUS filter returned the two isolated Alex Morgan identities; source matching detail displayed.
- Aisha Rahman, 2026-10-15: 2 transactions; gross A$2,053.63; largest A$2,023.00. Both underlying source transactions displayed and reconciled.
- Recent 7 data days + all statuses: 100 customer-days, 114 transactions, gross A$6,724.04. The downloaded filtered CSV contained all 100 rows (not just the 20-row page), dates 2026-10-09–2026-10-15, and matching counts/amounts.
- Product rules display evidence and withhold suggestions for identity review/unknown coverage; browser console had no warning/error entries during these checks.
- Full-history trend displayed 1,968 transactions and gross A$418,224.17. Screenshot stored locally under `artifacts/screenshots/` (not included in source-only release).

Quality regression injections in the newly created optimized-quality cluster were restored to baseline after testing. The original project's stopped containers, source files and database volumes were not changed.

## Scope limits

These checks support the supplied synthetic teaching scenarios. They do not establish production banking readiness, regulatory compliance, comprehensive security, arbitrary source deletion/remapping support, concurrent refresh consistency, complete transaction ledgers or actual financial suitability.
