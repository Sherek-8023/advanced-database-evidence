-- Event order differs from source timestamps; a tied ACTIVE must not override
-- WITHDRAWN. The original Alice event remains available as evidence.
INSERT INTO src_crm.consent
    (consent_id, crm_contact_id, consent_type, consent_status, updated_at)
VALUES
    ('DQ-CDR-OLD', 'CRM-301', 'CDR_DATA_SHARING', 'ACTIVE',
     TIMESTAMP '2026-09-15 09:00:00'),
    ('DQ-CDR-WITHDRAWN', 'CRM-301', 'CDR_DATA_SHARING', 'WITHDRAWN',
     TIMESTAMP '2026-09-25 10:00:00'),
    ('DQ-CDR-TIED-ACTIVE', 'CRM-301', 'CDR_DATA_SHARING', 'ACTIVE',
     TIMESTAMP '2026-09-25 10:00:00')
ON CONFLICT (consent_id) DO NOTHING;
