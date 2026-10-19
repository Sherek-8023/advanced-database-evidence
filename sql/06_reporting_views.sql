-- Report 1: unified customer profile and aggregated products/activities.
CREATE OR REPLACE VIEW dw.vw_customer_360 AS
WITH account_totals AS (
    SELECT a.canonical_customer_id,
           COUNT(*) FILTER (WHERE a.account_status = 'OPEN' OR a.account_status = 'ACTIVE') AS active_product_count,
           SUM(a.current_balance) FILTER (WHERE p.product_type = 'DEPOSIT') AS deposit_balance,
           SUM(a.current_balance) FILTER (WHERE p.product_type IN ('CREDIT_CARD', 'LOAN')) AS credit_and_loan_balance
    FROM dw.dim_account a
    JOIN dw.dim_product p ON p.product_key = a.product_key
    GROUP BY a.canonical_customer_id
), activity AS (
    SELECT canonical_customer_id, MAX(activity_ts) AS latest_activity
    FROM (
        SELECT c.canonical_customer_id, t.transaction_ts AS activity_ts
        FROM dw.fact_transaction t
        JOIN dw.dim_customer c ON c.customer_key = t.customer_key
        UNION ALL
        SELECT c.canonical_customer_id, r.repayment_ts
        FROM dw.fact_loan_repayment r
        JOIN dw.dim_customer c ON c.customer_key = r.customer_key
        UNION ALL
        SELECT c.canonical_customer_id, i.interaction_ts
        FROM dw.fact_customer_interaction i
        JOIN dw.dim_customer c ON c.customer_key = i.customer_key
    ) events
    GROUP BY canonical_customer_id
)
SELECT c.canonical_customer_id, c.customer_name, c.email, c.phone,
       c.identity_status, COALESCE(a.active_product_count, 0) AS active_product_count,
       COALESCE(a.deposit_balance, 0)::numeric(14,2) AS deposit_balance,
       COALESCE(a.credit_and_loan_balance, 0)::numeric(14,2) AS credit_and_loan_balance,
       co.consent_status AS cdr_consent_status, co.updated_at AS consent_updated_at,
       ac.latest_activity
FROM dw.dim_customer c
LEFT JOIN account_totals a USING (canonical_customer_id)
-- Keep one row per customer. Other consent types must not be labelled as CDR;
-- the ON filter preserves customers who do not have a CDR consent record.
LEFT JOIN dw.customer_consent co
  ON co.canonical_customer_id = c.canonical_customer_id
 AND co.consent_type = 'CDR_DATA_SHARING'
LEFT JOIN activity ac ON ac.canonical_customer_id = c.canonical_customer_id;

-- Report 2: daily transaction monitoring. Thresholds are illustrative only.
CREATE OR REPLACE VIEW dw.vw_transaction_risk_monitor AS
SELECT c.canonical_customer_id, c.customer_name, d.calendar_date,
       COUNT(*) AS transaction_count,
       SUM(ABS(t.amount))::numeric(14,2) AS gross_transaction_value,
       MAX(ABS(t.amount))::numeric(14,2) AS largest_transaction,
       CASE
           WHEN COUNT(*) >= 3 OR MAX(ABS(t.amount)) >= 1500 THEN 'REVIEW'
           ELSE 'NORMAL'
       END AS review_flag
FROM dw.fact_transaction t
JOIN dw.dim_customer c ON c.customer_key = t.customer_key
JOIN dw.dim_date d ON d.date_key = t.date_key
GROUP BY c.canonical_customer_id, c.customer_name, d.calendar_date;

-- Report 3: simple cross-sell candidates for business discussion.
CREATE OR REPLACE VIEW dw.vw_product_opportunity AS
WITH products AS (
    SELECT c.canonical_customer_id,
           COUNT(a.account_key) AS loaded_accounts,
           CASE WHEN COUNT(a.account_key) = 0 THEN NULL ELSE
             BOOL_OR(p.product_type = 'CREDIT_CARD' AND a.account_status IN ('OPEN','ACTIVE')) END AS has_credit_card,
           CASE WHEN COUNT(a.account_key) = 0 THEN NULL ELSE
             BOOL_OR(p.product_type = 'LOAN' AND a.account_status IN ('OPEN','ACTIVE')) END AS has_loan,
           SUM(a.current_balance) FILTER (WHERE p.product_type = 'DEPOSIT' AND a.account_status IN ('OPEN','ACTIVE')) AS total_deposits,
           SUM(a.current_balance) FILTER (WHERE p.product_type = 'CREDIT_CARD' AND a.account_status IN ('OPEN','ACTIVE')) AS card_balance
    FROM dw.dim_customer c
    LEFT JOIN dw.dim_account a ON a.canonical_customer_id = c.canonical_customer_id
    LEFT JOIN dw.dim_product p ON p.product_key = a.product_key
    GROUP BY c.canonical_customer_id
), qualified AS (
SELECT c.*, p.has_credit_card, p.has_loan, p.total_deposits, p.card_balance,
       CASE WHEN c.identity_status <> 'MATCHED' THEN 'IDENTITY_REVIEW_REQUIRED'
            WHEN p.loaded_accounts = 0 THEN 'NO_ACCOUNT_DATA' ELSE 'READY' END AS review_readiness
FROM dw.dim_customer c JOIN products p USING (canonical_customer_id)
), evaluated AS (
SELECT *, CASE
           WHEN review_readiness <> 'READY' THEN 'NO_RULE_BASED_SUGGESTION'
           WHEN COALESCE(total_deposits,0) >= 10000 AND has_loan IS FALSE
               THEN 'HOME_OR_PERSONAL_LOAN_REVIEW'
           WHEN COALESCE(card_balance,0) >= 1000 THEN 'CARD_REPAYMENT_SUPPORT_REVIEW'
           WHEN COALESCE(total_deposits,0) >= 5000 AND has_credit_card IS FALSE
               THEN 'CREDIT_CARD_OFFER_REVIEW'
           ELSE 'NO_RULE_BASED_SUGGESTION' END AS suggested_action
FROM qualified
)
SELECT canonical_customer_id, customer_name,
       COALESCE(total_deposits,0)::numeric(14,2) AS total_deposits,
       COALESCE(card_balance,0)::numeric(14,2) AS card_balance,
       has_credit_card, has_loan, suggested_action, identity_status, review_readiness,
       CASE WHEN review_readiness <> 'READY' THEN 'Confirm identity and account coverage before product review.'
            WHEN suggested_action = 'HOME_OR_PERSONAL_LOAN_REVIEW' THEN 'Active deposits meet A$10,000; no active loan is recorded in the loaded data.'
            WHEN suggested_action = 'CARD_REPAYMENT_SUPPORT_REVIEW' THEN 'Active card balance meets the illustrative A$1,000 review threshold; this does not establish repayment difficulty.'
            WHEN suggested_action = 'CREDIT_CARD_OFFER_REVIEW' THEN 'Active deposits meet A$5,000; no active credit card is recorded in the loaded data.'
            ELSE 'No ordered rule matches the available active-account data.' END AS rule_reason
FROM evaluated;

-- Read-only drilldown views keep the web role away from raw sources.
CREATE OR REPLACE VIEW dw.vw_transaction_detail AS
SELECT c.canonical_customer_id, t.source_system, t.source_transaction_id,
       a.source_account_id, t.transaction_ts, t.transaction_type,
       t.amount, t.merchant, t.channel
FROM dw.fact_transaction t
JOIN dw.dim_customer c USING (customer_key)
JOIN dw.dim_account a USING (account_key);

CREATE OR REPLACE VIEW dw.vw_customer_activity AS
SELECT canonical_customer_id, 'TRANSACTION'::text AS event_type,
       source_transaction_id AS event_id, transaction_ts AS event_ts,
       source_system, source_account_id AS account_id,
       COALESCE(merchant,transaction_type) AS description, amount, channel
FROM dw.vw_transaction_detail
UNION ALL
SELECT c.canonical_customer_id,'REPAYMENT',r.source_repayment_id,r.repayment_ts,
       r.source_system,a.source_account_id,'Loan repayment',r.amount,r.channel
FROM dw.fact_loan_repayment r JOIN dw.dim_customer c USING (customer_key)
JOIN dw.dim_account a USING (account_key)
UNION ALL
SELECT c.canonical_customer_id,'CRM',i.source_interaction_id,i.interaction_ts,
       'SRC_CRM',NULL,i.interaction_type || ': ' || i.notes,NULL,i.channel
FROM dw.fact_customer_interaction i JOIN dw.dim_customer c USING (customer_key);

CREATE OR REPLACE VIEW dw.vw_identity_lineage AS
SELECT c.source_system,c.source_customer_id,c.full_name,c.email,c.phone,
       c.normalized_email,c.date_of_birth,i.canonical_customer_id,i.match_status,i.match_method
FROM stg.customer_source c JOIN stg.customer_identity i
USING (source_system,source_customer_id);
