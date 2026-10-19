-- Marketing permission is a separate purpose, not another CDR status.
INSERT INTO src_crm.consent
    (consent_id, crm_contact_id, consent_type, consent_status, updated_at)
VALUES ('DQ-MARKETING-901', 'CRM-301', 'MARKETING', 'WITHDRAWN',
        TIMESTAMP '2026-09-26 09:00:00')
ON CONFLICT (consent_id) DO NOTHING;
