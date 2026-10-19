-- Populate dimensions and facts from the integrated staging layer.

WITH preferred_profile AS (
    SELECT DISTINCT ON (i.canonical_customer_id)
           i.canonical_customer_id, c.full_name, c.email, c.phone, c.date_of_birth,
           i.match_status
    FROM stg.customer_identity i
    JOIN stg.customer_source c
      ON c.source_system = i.source_system AND c.source_customer_id = i.source_customer_id
    ORDER BY i.canonical_customer_id,
             CASE i.source_system
                 WHEN 'SRC_CRM' THEN 1 WHEN 'SRC_DEPOSITS' THEN 2
                 WHEN 'SRC_CARDS' THEN 3 WHEN 'SRC_LENDING' THEN 4 ELSE 5
             END
), identity_quality AS (
    SELECT canonical_customer_id,
           CASE
               WHEN bool_or(match_status = 'AMBIGUOUS') THEN 'AMBIGUOUS'
               WHEN bool_or(match_status = 'UNMATCHED') THEN 'UNMATCHED'
               ELSE 'MATCHED'
           END AS identity_status
    FROM stg.customer_identity
    GROUP BY canonical_customer_id
)
INSERT INTO dw.dim_customer
    (canonical_customer_id, customer_name, email, phone, date_of_birth, identity_status)
SELECT p.canonical_customer_id, p.full_name, p.email, p.phone, p.date_of_birth, q.identity_status
FROM preferred_profile p
JOIN identity_quality q USING (canonical_customer_id)
ON CONFLICT (canonical_customer_id) DO UPDATE
SET customer_name = EXCLUDED.customer_name,
    email = EXCLUDED.email,
    phone = EXCLUDED.phone,
    date_of_birth = EXCLUDED.date_of_birth,
    identity_status = EXCLUDED.identity_status;

INSERT INTO dw.dim_product (product_type, product_name)
SELECT DISTINCT product_type, product_name
FROM stg.account_source
ON CONFLICT (product_type, product_name) DO NOTHING;

INSERT INTO dw.dim_account
    (source_system, source_account_id, canonical_customer_id, product_key,
     opened_on, current_balance, account_status)
SELECT a.source_system, a.source_account_id, a.canonical_customer_id, p.product_key,
       a.opened_on, a.current_balance, a.account_status
FROM stg.account_source a
JOIN dw.dim_product p
  ON p.product_type = a.product_type AND p.product_name = a.product_name
ON CONFLICT (source_system, source_account_id) DO UPDATE
SET canonical_customer_id = EXCLUDED.canonical_customer_id,
    product_key = EXCLUDED.product_key,
    opened_on = EXCLUDED.opened_on,
    current_balance = EXCLUDED.current_balance,
    account_status = EXCLUDED.account_status;

INSERT INTO dw.dim_date (date_key, calendar_date, year_number, quarter_number, month_number, day_number)
SELECT DISTINCT to_char(d::date, 'YYYYMMDD')::integer, d::date,
       extract(year FROM d)::integer, extract(quarter FROM d)::integer,
       extract(month FROM d)::integer, extract(day FROM d)::integer
FROM (
    SELECT transaction_ts::date AS d FROM stg.transaction_source
    UNION
    SELECT repayment_ts::date FROM stg.repayment_source
    UNION
    SELECT interaction_ts::date FROM src_crm.interaction
) dates
ON CONFLICT (date_key) DO NOTHING;

INSERT INTO dw.fact_transaction
    (source_system, source_transaction_id, customer_key, account_key, date_key,
     transaction_ts, transaction_type, amount, merchant, channel)
SELECT t.source_system, t.source_transaction_id, c.customer_key, a.account_key, d.date_key,
       t.transaction_ts, t.transaction_type, t.amount, t.merchant, t.channel
FROM stg.transaction_source t
JOIN dw.dim_customer c ON c.canonical_customer_id = t.canonical_customer_id
JOIN dw.dim_account a
  ON a.source_system = t.source_system AND a.source_account_id = t.source_account_id
JOIN dw.dim_date d ON d.calendar_date = t.transaction_ts::date
ON CONFLICT (source_system, source_transaction_id) DO NOTHING;

INSERT INTO dw.fact_loan_repayment
    (source_system, source_repayment_id, customer_key, account_key, date_key,
     repayment_ts, amount, channel)
SELECT r.source_system, r.source_repayment_id, c.customer_key, a.account_key, d.date_key,
       r.repayment_ts, r.amount, r.channel
FROM stg.repayment_source r
JOIN dw.dim_customer c ON c.canonical_customer_id = r.canonical_customer_id
JOIN dw.dim_account a
  ON a.source_system = r.source_system AND a.source_account_id = r.source_account_id
JOIN dw.dim_date d ON d.calendar_date = r.repayment_ts::date
ON CONFLICT (source_system, source_repayment_id) DO NOTHING;

-- Resolve source identities BEFORE selecting one current row per customer/type.
-- Multiple CRM contacts may map to one canonical customer, and a source contact
-- may have multiple consent events. Passing all events to one upsert would try to
-- update the same target key twice and PostgreSQL would reject the statement.
--
-- Technical demonstration tie policy (not a complete authorisation policy):
--   1. The greatest source updated_at wins.
--   2. At the same timestamp, WITHDRAWN wins conservatively.
--   3. Other tied statuses use descending bytewise status text; contact ID and
--      consent ID in ascending bytewise order select a stable source record.
--      These IDs do not change the stored result when status/time are identical.
-- The upsert applies the SAME timestamp/status ordering to existing DW state,
-- so an older event or a tied ACTIVE event cannot undo a stored withdrawal.
WITH mapped_consent AS (
    SELECT i.canonical_customer_id, c.consent_type, c.consent_status,
           c.updated_at, c.crm_contact_id, c.consent_id
    FROM src_crm.consent c
    JOIN stg.customer_identity i
      ON i.source_system = 'SRC_CRM' AND i.source_customer_id = c.crm_contact_id
), latest_consent AS (
    SELECT DISTINCT ON (canonical_customer_id, consent_type)
           canonical_customer_id, consent_type, consent_status, updated_at
    FROM mapped_consent
    ORDER BY canonical_customer_id, consent_type, updated_at DESC,
             CASE WHEN consent_status = 'WITHDRAWN' THEN 1 ELSE 0 END DESC,
             consent_status COLLATE "C" DESC,
             crm_contact_id COLLATE "C", consent_id COLLATE "C"
)
INSERT INTO dw.customer_consent (canonical_customer_id, consent_type, consent_status, updated_at)
SELECT canonical_customer_id, consent_type, consent_status, updated_at
FROM latest_consent
ON CONFLICT (canonical_customer_id, consent_type) DO UPDATE
SET consent_status = EXCLUDED.consent_status,
    updated_at = EXCLUDED.updated_at
WHERE EXCLUDED.updated_at > dw.customer_consent.updated_at
   OR (
       EXCLUDED.updated_at = dw.customer_consent.updated_at
       AND ROW(
           CASE WHEN EXCLUDED.consent_status = 'WITHDRAWN' THEN 1 ELSE 0 END,
           EXCLUDED.consent_status COLLATE "C"
       ) > ROW(
           CASE WHEN dw.customer_consent.consent_status = 'WITHDRAWN' THEN 1 ELSE 0 END,
           dw.customer_consent.consent_status COLLATE "C"
       )
   );

INSERT INTO dw.fact_customer_interaction
    (source_interaction_id, customer_key, date_key, interaction_ts, interaction_type, channel, notes)
SELECT i.interaction_id, c.customer_key, d.date_key, i.interaction_ts,
       i.interaction_type, i.channel, i.notes
FROM src_crm.interaction i
JOIN src_crm.contact x ON x.crm_contact_id = i.crm_contact_id
JOIN stg.customer_identity m
  ON m.source_system = 'SRC_CRM' AND m.source_customer_id = x.crm_contact_id
JOIN dw.dim_customer c ON c.canonical_customer_id = m.canonical_customer_id
JOIN dw.dim_date d ON d.calendar_date = i.interaction_ts::date
ON CONFLICT (source_interaction_id) DO NOTHING;

