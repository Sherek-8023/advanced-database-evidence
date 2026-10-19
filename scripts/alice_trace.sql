-- Read-only Alice walkthrough. Run after the original SQL 01-06 pipeline.
-- From workspace: docker compose exec -T postgres psql -X -U student -d poverty_bank < scripts/alice_trace.sql
-- This script only reads a consistent snapshot. It does not rerun loads.
-- Surrogate keys and snapshot counts may differ across databases/runs.
\set ON_ERROR_STOP on
\pset pager off
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;

-- STEP 02
\echo 'SOURCE CUSTOMER RECORDS: four source IDs for Alice'
SELECT 'SRC_DEPOSITS' AS source_system, customer_id AS source_customer_id, email, date_of_birth
FROM src_deposits.customer WHERE customer_id='DEP-101'
UNION ALL SELECT 'SRC_CARDS', cardholder_id, email, date_of_birth FROM src_cards.cardholder WHERE cardholder_id='CARD-C81'
UNION ALL SELECT 'SRC_LENDING', borrower_id, email, date_of_birth FROM src_lending.borrower WHERE borrower_id='LN-B11'
UNION ALL SELECT 'SRC_CRM', crm_contact_id, email, date_of_birth FROM src_crm.contact WHERE crm_contact_id='CRM-301';

\echo 'SOURCE ACCOUNTS: balances are source snapshots'
SELECT 'SRC_DEPOSITS' AS source_system, account_id AS source_account_id, account_type AS product_name, current_balance
FROM src_deposits.account WHERE customer_id='DEP-101'
UNION ALL SELECT 'SRC_CARDS', card_id, product_name, current_balance FROM src_cards.card WHERE cardholder_id='CARD-C81'
UNION ALL SELECT 'SRC_LENDING', loan_id, loan_type, principal_balance FROM src_lending.loan WHERE borrower_id='LN-B11';

\echo 'SOURCE TRANSACTIONS: only deposits and cards go to fact_transaction'
SELECT 'SRC_DEPOSITS' AS source_system, transaction_id, account_id, transaction_ts, amount
FROM src_deposits.deposit_transaction WHERE account_id IN ('DDA-1001','SAV-1002')
UNION ALL SELECT 'SRC_CARDS', transaction_id, card_id, transaction_ts, amount FROM src_cards.card_transaction WHERE card_id='CC-7001'
ORDER BY transaction_ts;

SELECT repayment_id, loan_id, repayment_ts, amount FROM src_lending.repayment WHERE loan_id='HOME-501';
SELECT interaction_id, interaction_ts, interaction_type FROM src_crm.interaction WHERE crm_contact_id='CRM-301';
SELECT consent_id, consent_type, consent_status, updated_at FROM src_crm.consent WHERE crm_contact_id='CRM-301';

-- STEP 03
\echo 'STAGING IDENTITY: four source records, one canonical ID'
SELECT s.source_system, s.source_customer_id, s.normalized_email, s.normalized_phone,
       i.canonical_customer_id, i.match_status
FROM stg.customer_source s
JOIN stg.customer_identity i USING (source_system,source_customer_id)
WHERE i.canonical_customer_id='CUST-07E495D4EE'
ORDER BY s.source_system;

SELECT source_system, source_account_id, product_type, product_name, current_balance, canonical_customer_id
FROM stg.account_source WHERE canonical_customer_id='CUST-07E495D4EE'
ORDER BY source_system,source_account_id;

SELECT source_transaction_id, source_account_id, transaction_ts, transaction_type, amount, canonical_customer_id
FROM stg.transaction_source WHERE canonical_customer_id='CUST-07E495D4EE'
ORDER BY transaction_ts;
SELECT source_repayment_id, source_account_id, amount, canonical_customer_id
FROM stg.repayment_source WHERE canonical_customer_id='CUST-07E495D4EE';

-- STEP 04
\echo 'VAULT CUSTOMER HUB: one business identity'
SELECT * FROM vault.hub_customer WHERE canonical_customer_id='CUST-07E495D4EE';

\echo 'VAULT RELATIONSHIPS: four accounts linked to Alice'
SELECT h.canonical_customer_id, a.account_bk, l.customer_account_hk
FROM vault.hub_customer h JOIN vault.link_customer_account l USING (customer_hk)
JOIN vault.hub_account a USING (account_hk)
WHERE h.canonical_customer_id='CUST-07E495D4EE'
ORDER BY a.account_bk;

\echo 'PROFILE SNAPSHOTS BY LOAD: four profiles per load'
SELECT s.load_timestamp, count(*) AS alice_source_profiles
FROM vault.sat_customer_profile s JOIN vault.hub_customer h USING(customer_hk)
WHERE h.canonical_customer_id='CUST-07E495D4EE'
GROUP BY s.load_timestamp ORDER BY s.load_timestamp;

\echo 'LATEST PROFILE SNAPSHOTS: keep raw source values'
SELECT s.record_source,s.source_customer_id,s.email,s.load_timestamp
FROM vault.sat_customer_profile s JOIN vault.hub_customer h USING(customer_hk)
WHERE h.canonical_customer_id='CUST-07E495D4EE'
  AND s.load_timestamp=(SELECT max(load_timestamp) FROM vault.sat_customer_profile)
ORDER BY s.record_source;

\echo 'ALICE SATELLITE ROW COUNTS'
SELECT 'profile' AS satellite,count(*) FROM vault.sat_customer_profile WHERE customer_hk=md5('CUST-07E495D4EE')
UNION ALL SELECT 'account',count(*) FROM vault.sat_account_details s JOIN vault.link_customer_account l USING(account_hk)
WHERE l.customer_hk=md5('CUST-07E495D4EE')
UNION ALL SELECT 'consent',count(*) FROM vault.sat_customer_consent WHERE customer_hk=md5('CUST-07E495D4EE');

-- STEP 05
\echo 'WAREHOUSE CUSTOMER: CRM preferred, surrogate key assigned'
SELECT * FROM dw.dim_customer WHERE canonical_customer_id='CUST-07E495D4EE';

\echo 'WAREHOUSE ACCOUNTS AND PRODUCTS'
SELECT a.account_key,a.source_system,a.source_account_id,p.product_key,p.product_type,p.product_name,a.current_balance,a.account_status
FROM dw.dim_account a JOIN dw.dim_product p USING(product_key)
WHERE a.canonical_customer_id='CUST-07E495D4EE'
ORDER BY a.source_system,a.source_account_id;

\echo 'WAREHOUSE TRANSACTIONS: resolved surrogate keys'
SELECT t.source_transaction_id,t.customer_key,t.account_key,a.source_account_id,t.date_key,t.transaction_ts,t.amount
FROM dw.fact_transaction t JOIN dw.dim_customer c USING(customer_key)
JOIN dw.dim_account a USING(account_key)
WHERE c.canonical_customer_id='CUST-07E495D4EE'
ORDER BY t.transaction_ts;

SELECT r.source_repayment_id,r.customer_key,r.account_key,r.date_key,r.amount
FROM dw.fact_loan_repayment r JOIN dw.dim_customer c USING(customer_key)
WHERE c.canonical_customer_id='CUST-07E495D4EE';
SELECT i.source_interaction_id,i.customer_key,i.date_key,i.interaction_ts,i.interaction_type
FROM dw.fact_customer_interaction i JOIN dw.dim_customer c USING(customer_key)
WHERE c.canonical_customer_id='CUST-07E495D4EE';
SELECT * FROM dw.customer_consent WHERE canonical_customer_id='CUST-07E495D4EE';

-- STEP 06
\echo 'REPORT 1: Alice Customer 360'
SELECT * FROM dw.vw_customer_360 WHERE canonical_customer_id='CUST-07E495D4EE';

\echo 'REPORT 2: Alice risk output, one row per transaction date'
SELECT * FROM dw.vw_transaction_risk_monitor WHERE canonical_customer_id='CUST-07E495D4EE' ORDER BY calendar_date;

\echo 'REPORT 3: Alice product opportunity'
SELECT * FROM dw.vw_product_opportunity WHERE canonical_customer_id='CUST-07E495D4EE';

\echo 'RISK CALCULATION EVIDENCE: which transaction amounts form each day'
SELECT t.transaction_ts::date AS calendar_date,
       string_agg(t.source_transaction_id||'='||t.amount::text, ', ' ORDER BY t.transaction_ts) AS source_amounts,
       count(*) AS transaction_count,sum(abs(t.amount)) AS gross_value,max(abs(t.amount)) AS largest
FROM dw.fact_transaction t JOIN dw.dim_customer c USING(customer_key)
WHERE c.canonical_customer_id='CUST-07E495D4EE'
GROUP BY t.transaction_ts::date ORDER BY calendar_date;

ROLLBACK;

