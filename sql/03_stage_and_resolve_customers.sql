-- Refresh the derived staging layer from all four source systems.
DELETE FROM stg.transaction_source;
DELETE FROM stg.repayment_source;
DELETE FROM stg.account_source;
DELETE FROM stg.customer_identity;
DELETE FROM stg.customer_source;

INSERT INTO stg.customer_source
    (source_system, source_customer_id, full_name, email, phone, date_of_birth,
     normalized_email, normalized_phone)
SELECT 'SRC_DEPOSITS', customer_id, full_name, email, phone, date_of_birth,
       NULLIF(lower(btrim(email)), ''), NULLIF(regexp_replace(phone, '[^0-9]', '', 'g'), '')
FROM src_deposits.customer
UNION ALL
SELECT 'SRC_CARDS', cardholder_id, full_name, email, phone, date_of_birth,
       NULLIF(lower(btrim(email)), ''), NULLIF(regexp_replace(phone, '[^0-9]', '', 'g'), '')
FROM src_cards.cardholder
UNION ALL
SELECT 'SRC_LENDING', borrower_id, full_name, email, phone, date_of_birth,
       NULLIF(lower(btrim(email)), ''), NULLIF(regexp_replace(phone, '[^0-9]', '', 'g'), '')
FROM src_lending.borrower
UNION ALL
SELECT 'SRC_CRM', crm_contact_id, full_name, email, phone, date_of_birth,
       NULLIF(lower(btrim(email)), ''), NULLIF(regexp_replace(phone, '[^0-9]', '', 'g'), '')
FROM src_crm.contact;

-- Matching policy for this prototype:
-- 1) normalised email with no conflicting dates of birth = deterministic match;
-- 2) missing email = unmatched, kept under a source-specific identity;
-- 3) conflicting dates of birth for one normalised email = ambiguous exception.
WITH email_quality AS (
    SELECT normalized_email, COUNT(DISTINCT date_of_birth) AS distinct_dob_count
    FROM stg.customer_source
    WHERE normalized_email IS NOT NULL
    GROUP BY normalized_email
)
INSERT INTO stg.customer_identity
    (source_system, source_customer_id, canonical_customer_id,
     match_status, match_method, normalized_email)
SELECT c.source_system,
       c.source_customer_id,
       CASE
           WHEN c.normalized_email IS NOT NULL AND q.distinct_dob_count <= 1
               THEN 'CUST-' || upper(substr(md5(c.normalized_email), 1, 10))
           WHEN c.normalized_email IS NOT NULL
               THEN 'AMBIGUOUS-' || upper(substr(md5(c.source_system || ':' || c.source_customer_id), 1, 10))
           ELSE 'UNMATCHED-' || upper(substr(md5(c.source_system || ':' || c.source_customer_id), 1, 10))
       END AS canonical_customer_id,
       CASE
           WHEN c.normalized_email IS NULL THEN 'UNMATCHED'
           WHEN q.distinct_dob_count > 1 THEN 'AMBIGUOUS'
           ELSE 'MATCHED'
       END AS match_status,
       CASE
           WHEN c.normalized_email IS NULL THEN 'NO_EMAIL_REVIEW_REQUIRED'
           WHEN q.distinct_dob_count > 1 THEN 'EMAIL_HAS_CONFLICTING_DOB_REVIEW_REQUIRED'
           ELSE 'NORMALIZED_EMAIL'
       END AS match_method,
       c.normalized_email
FROM stg.customer_source c
LEFT JOIN email_quality q ON q.normalized_email = c.normalized_email;

INSERT INTO stg.account_source
    (source_system, source_account_id, source_customer_id, canonical_customer_id,
     product_type, product_name, opened_on, current_balance, account_status)
SELECT 'SRC_DEPOSITS', a.account_id, a.customer_id, i.canonical_customer_id,
       'DEPOSIT', a.account_type, a.opened_on, a.current_balance, a.status
FROM src_deposits.account a
JOIN stg.customer_identity i
  ON i.source_system = 'SRC_DEPOSITS' AND i.source_customer_id = a.customer_id
UNION ALL
SELECT 'SRC_CARDS', c.card_id, c.cardholder_id, i.canonical_customer_id,
       'CREDIT_CARD', c.product_name, c.opened_on, c.current_balance, c.status
FROM src_cards.card c
JOIN stg.customer_identity i
  ON i.source_system = 'SRC_CARDS' AND i.source_customer_id = c.cardholder_id
UNION ALL
SELECT 'SRC_LENDING', l.loan_id, l.borrower_id, i.canonical_customer_id,
       'LOAN', l.loan_type, l.opened_on, l.principal_balance, l.status
FROM src_lending.loan l
JOIN stg.customer_identity i
  ON i.source_system = 'SRC_LENDING' AND i.source_customer_id = l.borrower_id;

INSERT INTO stg.transaction_source
    (source_system, source_transaction_id, source_account_id, canonical_customer_id,
     transaction_ts, transaction_type, amount, merchant, channel)
SELECT 'SRC_DEPOSITS', t.transaction_id, t.account_id, a.canonical_customer_id,
       t.transaction_ts, t.transaction_type, t.amount, t.merchant, t.channel
FROM src_deposits.deposit_transaction t
JOIN stg.account_source a
  ON a.source_system = 'SRC_DEPOSITS' AND a.source_account_id = t.account_id
UNION ALL
SELECT 'SRC_CARDS', t.transaction_id, t.card_id, a.canonical_customer_id,
       t.transaction_ts, 'CARD_PURCHASE', t.amount, t.merchant, t.channel
FROM src_cards.card_transaction t
JOIN stg.account_source a
  ON a.source_system = 'SRC_CARDS' AND a.source_account_id = t.card_id;

INSERT INTO stg.repayment_source
    (source_system, source_repayment_id, source_account_id, canonical_customer_id,
     repayment_ts, amount, channel)
SELECT 'SRC_LENDING', r.repayment_id, r.loan_id, a.canonical_customer_id,
       r.repayment_ts, r.amount, r.channel
FROM src_lending.repayment r
JOIN stg.account_source a
  ON a.source_system = 'SRC_LENDING' AND a.source_account_id = r.loan_id;
