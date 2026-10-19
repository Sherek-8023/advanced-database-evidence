-- Run these read-only checks after loading the staging, vault, warehouse and views.

-- Source inventory: each named source system has its own independently keyed records.
SELECT 'SRC_DEPOSITS' AS source_system, COUNT(*) AS customer_records FROM src_deposits.customer
UNION ALL SELECT 'SRC_CARDS', COUNT(*) FROM src_cards.cardholder
UNION ALL SELECT 'SRC_LENDING', COUNT(*) FROM src_lending.borrower
UNION ALL SELECT 'SRC_CRM', COUNT(*) FROM src_crm.contact;

-- Identity matching outcomes. UNMATCHED rows are retained for review, not silently merged.
SELECT match_status, COUNT(*) AS source_customer_records
FROM stg.customer_identity
GROUP BY match_status
ORDER BY match_status;

SELECT source_system, source_customer_id, canonical_customer_id, match_method
FROM stg.customer_identity
WHERE match_status <> 'MATCHED'
ORDER BY source_system, source_customer_id;

-- Data Vault row counts and source lineage coverage.
SELECT 'hub_customer' AS object_name, COUNT(*) AS row_count FROM vault.hub_customer
UNION ALL SELECT 'hub_account', COUNT(*) FROM vault.hub_account
UNION ALL SELECT 'link_customer_account', COUNT(*) FROM vault.link_customer_account
UNION ALL SELECT 'sat_customer_profile', COUNT(*) FROM vault.sat_customer_profile
UNION ALL SELECT 'sat_account_details', COUNT(*) FROM vault.sat_account_details;

SELECT record_source, COUNT(*) AS profile_versions
FROM vault.sat_customer_profile
GROUP BY record_source
ORDER BY record_source;

-- Source-to-warehouse transaction reconciliation by source system.
WITH source_totals AS (
    SELECT source_system, COUNT(*) AS row_count, SUM(amount)::numeric(14,2) AS amount_total
    FROM stg.transaction_source GROUP BY source_system
), warehouse_totals AS (
    SELECT source_system, COUNT(*) AS row_count, SUM(amount)::numeric(14,2) AS amount_total
    FROM dw.fact_transaction GROUP BY source_system
)
SELECT COALESCE(s.source_system, w.source_system) AS source_system,
       COALESCE(s.row_count, 0) AS staged_rows, COALESCE(w.row_count, 0) AS warehouse_rows,
       COALESCE(s.amount_total, 0) AS staged_amount, COALESCE(w.amount_total, 0) AS warehouse_amount,
       COALESCE(s.row_count, 0) = COALESCE(w.row_count, 0)
           AND COALESCE(s.amount_total, 0) = COALESCE(w.amount_total, 0) AS reconciles
FROM source_totals s
FULL JOIN warehouse_totals w USING (source_system)
ORDER BY source_system;

-- Demonstrate the three business-facing outputs.
SELECT * FROM dw.vw_customer_360 ORDER BY customer_name;
SELECT * FROM dw.vw_transaction_risk_monitor ORDER BY calendar_date, gross_transaction_value DESC;
SELECT * FROM dw.vw_product_opportunity ORDER BY suggested_action, total_deposits DESC;

