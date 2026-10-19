# Poverty Bank A2 — Optimized Prototype

This version runs in an isolated Docker project, database, and volume. It does not overwrite the original prototype.

## Start the application

Run from this directory:

```sh
docker compose up --build -d --wait web
```

Open <http://localhost:18080>. On first startup, the application creates the schema, generates the demo data, runs the integration pipeline and validation, and creates a read-only reporting account.

- PostgreSQL: `localhost:15434`
- Database: `poverty_bank`
- Local teaching credentials: `student` / `student`
- The dashboard connects with the restricted `report_reader` account, not the student account.
- Ports are bound to localhost only.

## Implemented features

- Four simulated business sources: Deposits, Cards, Lending, and CRM. They are represented by separate source schemas in one PostgreSQL instance; they are not four independently deployed external systems.
- An end-to-end pipeline from source data through staging and identity matching, then into parallel Data Vault and dimensional warehouse paths, followed by three dashboard reports. CRM consent and interactions are identity-mapped into the warehouse.
- 120 source customer profiles (Deposits 39, Cards 30, Lending 14, CRM 37), integrated into 50 canonical identities. This does not mean there are 50 confirmed distinct real-world people. The dataset also contains 92 accounts, 1,968 deposit/card transactions, 62 repayments, and 92 CRM activities. Transactions span 107 days, from 2026-07-01 to 2026-10-15.
- **Customer 360:** search, identity-status filtering, pagination, product distribution, consent status, the latest 20 transactions/repayments/CRM activities, and source-identity details.
- **Transaction Risk:** date presets, customer and flag filters, daily transaction trends, pagination, and customer/day drill-down to underlying transactions.
- **Product Opportunities:** recommendation-category chart and explainable rule evidence. Closed holdings are excluded; no recommendation is made when identity is unresolved or account data is unavailable.
- All three reports support filtered CSV exports (including all matching pages) and full-report CSV exports. The page retrieval timestamp is shown separately from the most recent successful data-load timestamp.
- The data retains cases such as missing email, different dates of birth for the same email, CRM-only identities, varied product combinations, closed accounts, different channels/merchants, CDR withdrawals, and historical consent events.

The dataset is reproducible synthetic data. Balances are configured current snapshots; transactions are sample activity, not a complete ledger, and cannot be used to reconstruct balance history.

`REVIEW` is a classroom threshold rule, not an anti-money-laundering model, fraud finding, credit decision, or real marketing permission. CDR consent is not the same as marketing consent. Identity matching uses normalized email, with date-of-birth conflicts kept separate; this is not production-grade KYC.

## Verification

Run these commands while the web service is running:

```sh
docker compose exec -T web python scripts/verify_pipeline.py
docker compose exec -T web python scripts/smoke_reports.py
docker compose exec -T web env TMPDIR=/dev/shm python -m unittest discover -s tests -p 'test_*api.py' -v
docker compose exec -T web python -m unittest discover -s tests -p 'test_consent_sql.py' -v
```

Pipeline verification independently checks source records, identity mappings, warehouse facts, drill-down records, and aggregates rather than merely checking that SQL ran successfully. The SQL regression tests create a randomly named temporary database and remove only that test database when finished; they do not operate on the main database.

To rerun the pipeline:

```sh
docker compose run --rm python python run_sql.py
```

Stable business keys prevent duplicate fact inserts, while the Vault retains load history. To export pipeline logs and the three CSV reports, run `bash scripts/run_pipeline.sh`. To package the source code, run `python scripts/package_release.py`; the package excludes the database volume, personal paths, secrets, and local machine settings.

## Isolated data-quality scenarios

Start the separate quality-demo services and check their status:

```sh
docker compose -f compose.quality.yaml up --build -d --wait web-identity web-consent web-types
docker compose -f compose.quality.yaml run --rm quality-cli python scripts/quality_demo.py status all
```

The Identity, Consent Events, and Consent Types dashboards are available at <http://localhost:18085>, <http://localhost:18086>, and <http://localhost:18087>, respectively. The quality database uses port `15435`. Each scenario starts from the original small baseline so changes before and after injection are easy to see; it is kept separate from the expanded showcase dataset.

Use `inject`, `run`, `verify`, and `reset` in this isolated environment (for example, `... quality_demo.py inject identity`), and check `status` first. This configuration does not change the original prototype's database or ports `18081`–`18083`.

## Suggested demo route

1. In Customer 360, show the overall counts and source mapping, then open a multi-product customer and review the activity timeline.
2. Open the transaction report from a customer, select a date, and compare the summary with the underlying transaction rows.
3. Switch between all history and the recent 7-/30-day views; observe the trend and totals, then export a filtered CSV.
4. In Product Opportunities, review the different suggestions and their explanations, then show an identity-conflict, CRM-only, or closed-card example.
5. If time allows, use the isolated quality environment to demonstrate fault injection, reload, verification, and reset.

The recorded dashboard demo is available at [demo/A2-dashboard-demo.mp4](demo/A2-dashboard-demo.mp4).

To stop the application while preserving its data, run `docker compose stop`. Avoid `docker compose down -v` unless you intentionally want to delete this version's database volume.
