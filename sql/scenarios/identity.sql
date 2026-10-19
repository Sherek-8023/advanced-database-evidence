-- Fresh profiles deliberately share an email but have different birth dates.
-- They have no accounts and cannot alter any seeded customer's identity.
INSERT INTO src_deposits.customer
    (customer_id, full_name, email, phone, date_of_birth)
VALUES ('DQ-DEP-901', 'Jordan Lee', '  Jordan.Shared@example.test  ',
        '0400 100 901', DATE '1988-04-12')
ON CONFLICT (customer_id) DO NOTHING;

INSERT INTO src_crm.contact
    (crm_contact_id, full_name, email, phone, date_of_birth)
VALUES ('DQ-CRM-901', 'Jordan Lee', 'jordan.shared@example.test',
        '0400-100-902', DATE '1992-11-03')
ON CONFLICT (crm_contact_id) DO NOTHING;
