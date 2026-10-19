-- Deterministic, additive showcase data. Run AFTER 02 and BEFORE 03, only in
-- the main showcase database. Quality fixtures continue to use original 02.
-- Fixed coverage: 1 July to 15 October 2026. Stable natural keys make replay safe.
-- Balances are synthetic current snapshots; these example events are not a
-- complete account ledger and must not be used to infer historical balances.
CREATE TEMP TABLE showcase_people ON COMMIT DROP AS
SELECT n,
       (ARRAY['Ava Patel','Liam Nguyen','Mia Wilson','Noah Hassan','Isla Thompson',
        'Oliver Martin','Amelia Scott','Ethan Kim','Zoe Anderson','Lucas Garcia',
        'Chloe Davis','Arjun Mehta','Ruby Clarke','Leo Williams','Nadia Ali',
        'Henry Brooks','Aisha Rahman','Samuel Cooper','Iris Zhang','Jack Murphy',
        'Priya Nair','Thomas Green','Ella Taylor','Marcus Wong','Layla Ahmed',
        'Finn Brown','Hannah Price','Daniel Lee','Mei Robinson','Ryan Mitchell',
        'Sara Ibrahim','Oscar King','Sofia Evans','James Turner','Emily Wright',
        'Adam Hughes','Lucy Campbell','Owen Edwards'])[n] AS full_name,
       'showcase.' || lpad(n::text,3,'0') || '@example.test' AS email,
       '0411200' || lpad(n::text,3,'0') AS phone,
       DATE '1974-01-01' + n * 277 AS dob
FROM generate_series(1,38) n;

INSERT INTO src_deposits.customer
SELECT 'EXT-DEP-'||n, full_name, email, phone, dob FROM showcase_people WHERE n%5<>0
ON CONFLICT DO NOTHING;
INSERT INTO src_cards.cardholder
SELECT 'EXT-CARD-'||n, full_name, ' '||upper(email)||' ', '+61 '||substr(phone,2), dob
FROM showcase_people WHERE n%3<>0 ON CONFLICT DO NOTHING;
INSERT INTO src_lending.borrower
SELECT 'EXT-LOAN-'||n, full_name, email, phone, dob
FROM showcase_people WHERE n%4=0 OR n=1 ON CONFLICT DO NOTHING;
INSERT INTO src_crm.contact
SELECT 'EXT-CRM-'||n, full_name, email, phone, dob
FROM showcase_people WHERE n%4<>1 OR n=1 ON CONFLICT DO NOTHING;

-- A mix of current products and closed historical products.
INSERT INTO src_deposits.account
SELECT 'EXT-DDA-'||n, 'EXT-DEP-'||n, 'TRANSACTION', DATE '2023-01-01'+n*7,
       CASE WHEN n%13=0 THEN 0 WHEN n%7=0 THEN 14500 ELSE 1800+n*337 END,
       CASE WHEN n%13=0 THEN 'CLOSED' ELSE 'OPEN' END
FROM showcase_people WHERE n%5<>0 ON CONFLICT DO NOTHING;
INSERT INTO src_deposits.account
SELECT 'EXT-SAV-'||n, 'EXT-DEP-'||n, 'SAVINGS', DATE '2024-01-01'+n*3,
       3500+n*213, 'OPEN'
FROM showcase_people WHERE n%5<>0 AND n%4=0 ON CONFLICT DO NOTHING;
INSERT INTO src_cards.card
SELECT 'EXT-CC-'||n, 'EXT-CARD-'||n,
       CASE WHEN n%2=0 THEN 'Rewards Visa' ELSE 'Low Rate Mastercard' END,
       DATE '2024-01-01'+n*3, 8000+n*150,
       CASE WHEN n%11=0 OR n%4=0 THEN 0 ELSE 250+n*119 END,
       CASE WHEN n%11=0 THEN 'CLOSED' ELSE 'ACTIVE' END
FROM showcase_people WHERE n%3<>0 ON CONFLICT DO NOTHING;
INSERT INTO src_lending.loan
SELECT 'EXT-LN-'||n, 'EXT-LOAN-'||n,
       CASE WHEN n%8=0 OR n=1 THEN 'HOME_LOAN' ELSE 'PERSONAL_LOAN' END,
       DATE '2022-01-01'+n*8,
       CASE WHEN n%12=0 THEN 0 WHEN n%8=0 OR n=1 THEN 210000+n*4700 ELSE 5000+n*390 END,
       CASE WHEN n%12=0 THEN 'CLOSED' ELSE 'ACTIVE' END
FROM showcase_people WHERE n%4=0 OR n=1 ON CONFLICT DO NOTHING;

-- Everyday purchases are spread irregularly across weekdays and weekends.
INSERT INTO src_deposits.deposit_transaction
SELECT 'EXT-DT-'||p.n||'-'||d, 'EXT-DDA-'||p.n,
       (DATE '2026-07-01'+d) + make_time(8+(p.n+d)%13,(p.n*7+d*11)%60,0),
       'PURCHASE',
       -CASE WHEN (p.n+d)%47=0 THEN 2000+p.n*11
             ELSE round(((p.n*37+d*19)%45000)::numeric/100+4.2,2) END,
       (ARRAY['Central Market','Metro Fuel','Cafe Lane','City Pharmacy','Outdoor Store','Home Supplies'])[(p.n+d)%6+1],
       (ARRAY['MOBILE','CARD','ONLINE','CONTACTLESS'])[(p.n+d)%4+1]
FROM showcase_people p CROSS JOIN generate_series(0,106) d
WHERE p.n%5<>0 AND (p.n*17+d*7)%19<6 AND (p.n%13<>0 OR d<50)
ON CONFLICT DO NOTHING;
INSERT INTO src_deposits.deposit_transaction
SELECT 'EXT-SAL-'||p.n||'-'||d, 'EXT-DDA-'||p.n,
       (DATE '2026-07-01'+d)+TIME '07:00', 'CREDIT', 2200+p.n*45,'Salary','PAYROLL'
FROM showcase_people p CROSS JOIN (VALUES (22),(53),(83)) days(d)
WHERE p.n%5<>0 AND p.n%13<>0 ON CONFLICT DO NOTHING;
-- Deliberate frequency cases: three small purchases on one date, not three
-- copies of the same natural key. Amount and frequency rules can be inspected.
INSERT INTO src_deposits.deposit_transaction
SELECT 'EXT-BURST-'||p.n||'-'||d||'-'||k, 'EXT-DDA-'||p.n,
       (DATE '2026-07-01'+d)+make_time(12+k,15,0), 'PURCHASE', -(12.5+k*7),
       'Neighbourhood shops','MOBILE'
FROM showcase_people p CROSS JOIN (VALUES (23),(56),(92)) days(d)
CROSS JOIN generate_series(1,3) k
WHERE p.n%9=0 AND p.n%5<>0 ON CONFLICT DO NOTHING;
INSERT INTO src_cards.card_transaction
SELECT 'EXT-CT-'||p.n||'-'||d, 'EXT-CC-'||p.n,
       (DATE '2026-07-01'+d)+make_time(9+(p.n+d)%12,(p.n*13+d*3)%60,0),
       CASE WHEN (p.n+d)%41=0 THEN 1700+p.n*19
            ELSE round(((p.n*83+d*29)%72000)::numeric/100+8.5,2) END,
       (ARRAY['Harbour Electronics','Fresh Market','Fuel Stop','Bookshop','Travel World','Restaurant 88'])[(p.n+d)%6+1],
       (ARRAY['CONTACTLESS','ONLINE','MOBILE'])[(p.n+d)%3+1]
FROM showcase_people p CROSS JOIN generate_series(0,106) d
WHERE p.n%3<>0 AND (p.n*11+d*5)%23<7 AND (p.n%11<>0 OR d<60)
ON CONFLICT DO NOTHING;
INSERT INTO src_lending.repayment
SELECT 'EXT-RP-'||p.n||'-'||g, 'EXT-LN-'||p.n,
       (DATE '2026-07-01'+14+14*g)+TIME '07:00',
       CASE WHEN p.n%8=0 OR p.n=1 THEN 1100+p.n*9 ELSE 180+p.n*4 END, 'DIRECT_DEBIT'
FROM showcase_people p CROSS JOIN generate_series(0,6) g
WHERE (p.n%4=0 OR p.n=1) AND (p.n%12<>0 OR g<3)
ON CONFLICT DO NOTHING;
INSERT INTO src_crm.interaction
SELECT 'EXT-INT-'||p.n||'-'||g, 'EXT-CRM-'||p.n,
       (DATE '2026-07-01'+(p.n*7+g*29)%107)+TIME '14:30',
       (ARRAY['SERVICE_CALL','PRODUCT_ENQUIRY','SERVICE_CHAT','COMPLAINT'])[(p.n+g)%4+1],
       (ARRAY['PHONE','BRANCH','APP','WEB'])[(p.n+g)%4+1],
       (ARRAY['Asked about account options','Updated contact preferences','Payment enquiry','Requested statement'])[(p.n+g)%4+1]
FROM showcase_people p CROSS JOIN generate_series(0,2) g
WHERE p.n%4<>1 OR p.n=1 ON CONFLICT DO NOTHING;

-- Preserve historical events; SQL 05 determines the current customer/type row.
INSERT INTO src_crm.consent
SELECT 'EXT-CNS-OLD-'||n,'EXT-CRM-'||n,'CDR_DATA_SHARING','ACTIVE',TIMESTAMP '2026-07-01 12:00'
FROM showcase_people WHERE n%4<>1 OR n=1 ON CONFLICT DO NOTHING;
INSERT INTO src_crm.consent
SELECT 'EXT-CNS-'||n,'EXT-CRM-'||n,'CDR_DATA_SHARING',
       CASE WHEN n%5=0 THEN 'WITHDRAWN' WHEN n%7=0 THEN 'PENDING' ELSE 'ACTIVE' END,
       TIMESTAMP '2026-10-01 12:00'+n*INTERVAL '1 hour'
FROM showcase_people WHERE n%4<>1 OR n=1 ON CONFLICT DO NOTHING;
INSERT INTO src_crm.consent
SELECT 'EXT-MKT-'||n,'EXT-CRM-'||n,'MARKETING',
       CASE WHEN n%3=0 THEN 'WITHDRAWN' ELSE 'ACTIVE' END,TIMESTAMP '2026-10-03 12:00'
FROM showcase_people WHERE n%4<>1 OR n=1 ON CONFLICT DO NOTHING;

-- Review exceptions remain separate instead of inventing a match.
INSERT INTO src_deposits.customer VALUES
('EXT-DEP-MISSING','Harper Ellis',NULL,'0411999001','1994-04-05'),
('EXT-DEP-CONFLICT','Alex Morgan','alex.shared@example.test','0411999002','1981-03-12')
ON CONFLICT DO NOTHING;
INSERT INTO src_crm.contact VALUES
('EXT-CRM-MISSING','Charlie Reid',NULL,'0411999003','1991-07-19'),
('EXT-CRM-CONFLICT','Alex Morgan',' ALEX.SHARED@example.test ','0411999002','1982-03-12')
ON CONFLICT DO NOTHING;
INSERT INTO src_deposits.account VALUES
('EXT-DDA-MISSING','EXT-DEP-MISSING','TRANSACTION','2024-01-01',1800,'OPEN'),
('EXT-DDA-CONFLICT','EXT-DEP-CONFLICT','SAVINGS','2024-01-01',15000,'OPEN')
ON CONFLICT DO NOTHING;
INSERT INTO src_crm.consent VALUES
('EXT-CNS-MISSING','EXT-CRM-MISSING','CDR_DATA_SHARING','PENDING','2026-10-02 12:00')
ON CONFLICT DO NOTHING;
