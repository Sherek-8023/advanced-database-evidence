-- Load hubs, links and satellites. Hash keys are deterministic MD5 values
-- over the canonical business key used in this compact teaching example.

INSERT INTO vault.hub_customer (customer_hk, canonical_customer_id, load_timestamp, record_source)
SELECT DISTINCT md5(canonical_customer_id), canonical_customer_id, current_timestamp, 'STG_CUSTOMER_IDENTITY'
FROM stg.customer_identity
ON CONFLICT (customer_hk) DO NOTHING;

INSERT INTO vault.hub_account (account_hk, account_bk, load_timestamp, record_source)
SELECT DISTINCT md5(source_system || ':' || source_account_id),
       source_system || ':' || source_account_id, current_timestamp, source_system
FROM stg.account_source
ON CONFLICT (account_hk) DO NOTHING;

INSERT INTO vault.link_customer_account
    (customer_account_hk, customer_hk, account_hk, load_timestamp, record_source)
SELECT DISTINCT md5(a.canonical_customer_id || '|' || a.source_system || ':' || a.source_account_id),
       md5(a.canonical_customer_id), md5(a.source_system || ':' || a.source_account_id),
       current_timestamp, a.source_system
FROM stg.account_source a
ON CONFLICT (customer_account_hk) DO NOTHING;

INSERT INTO vault.sat_customer_profile
    (customer_hk, load_timestamp, record_source, source_customer_id,
     full_name, email, phone, date_of_birth)
SELECT md5(i.canonical_customer_id), current_timestamp, i.source_system,
       c.source_customer_id, c.full_name, c.email, c.phone, c.date_of_birth
FROM stg.customer_source c
JOIN stg.customer_identity i
  ON i.source_system = c.source_system AND i.source_customer_id = c.source_customer_id
ON CONFLICT DO NOTHING;

INSERT INTO vault.sat_account_details
    (account_hk, load_timestamp, record_source, product_type, product_name,
     opened_on, current_balance, account_status)
SELECT md5(source_system || ':' || source_account_id), current_timestamp,
       source_system, product_type, product_name, opened_on, current_balance, account_status
FROM stg.account_source
ON CONFLICT DO NOTHING;

INSERT INTO vault.sat_customer_consent
    (customer_hk, load_timestamp, record_source, consent_id, consent_type,
     consent_status, consent_updated_at)
SELECT md5(i.canonical_customer_id), current_timestamp, 'SRC_CRM', c.consent_id,
       c.consent_type, c.consent_status, c.updated_at
FROM src_crm.consent c
JOIN stg.customer_identity i
  ON i.source_system = 'SRC_CRM' AND i.source_customer_id = c.crm_contact_id
ON CONFLICT DO NOTHING;

