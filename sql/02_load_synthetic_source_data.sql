-- Synthetic records deliberately use different IDs and email/phone formats
-- across systems. No real customer data is used.

INSERT INTO src_deposits.customer VALUES
('DEP-101','Alice Chen','alice.chen@example.test','0412 345 678','1988-04-12'),
('DEP-102','Ben Wong','ben.wong@example.test','0400 111 222','1990-09-03'),
('DEP-103','Carla Singh','carla.singh@example.test','0422 333 444','1985-01-27'),
('DEP-104','David Li','david.li@example.test','0433 444 555','1979-06-18'),
('DEP-105','Emma Brown','emma.brown@example.test','0444 555 666','1993-12-02'),
('DEP-106','Farah Khan',NULL,'0455 666 777','1987-08-14')
ON CONFLICT DO NOTHING;

INSERT INTO src_deposits.account VALUES
('DDA-1001','DEP-101','TRANSACTION','2021-02-10',8450.00,'OPEN'),
('SAV-1002','DEP-101','SAVINGS','2022-05-01',12500.00,'OPEN'),
('DDA-1003','DEP-102','TRANSACTION','2020-11-22',2300.00,'OPEN'),
('SAV-1004','DEP-103','SAVINGS','2023-01-14',17600.00,'OPEN'),
('DDA-1005','DEP-104','TRANSACTION','2019-08-09',950.00,'OPEN'),
('DDA-1006','DEP-105','TRANSACTION','2024-03-20',6200.00,'OPEN'),
('SAV-1007','DEP-106','SAVINGS','2022-09-05',3100.00,'OPEN')
ON CONFLICT DO NOTHING;

INSERT INTO src_deposits.deposit_transaction VALUES
('DT-9001','DDA-1001','2026-09-20 09:12','PURCHASE',-86.40,'Northside Grocer','MOBILE'),
('DT-9002','DDA-1001','2026-09-20 12:45','TRANSFER',-1250.00,'Rent transfer','ONLINE'),
('DT-9003','SAV-1002','2026-09-21 08:00','CREDIT',500.00,'Salary','PAYROLL'),
('DT-9004','DDA-1003','2026-09-21 16:22','PURCHASE',-42.80,'City Pharmacy','CARD'),
('DT-9005','SAV-1004','2026-09-22 10:03','TRANSFER',-3200.00,'External transfer','ONLINE'),
('DT-9006','DDA-1005','2026-09-22 13:40','PURCHASE',-34.20,'Cafe Lane','MOBILE'),
('DT-9007','DDA-1006','2026-09-23 18:10','PURCHASE',-2450.00,'Travel World','CARD'),
('DT-9008','SAV-1007','2026-09-24 09:30','CREDIT',120.00,'Interest','SYSTEM')
ON CONFLICT DO NOTHING;

INSERT INTO src_cards.cardholder VALUES
('CARD-C81','Alice Chen',' ALICE.CHEN@example.test ','+61 412345678','1988-04-12'),
('CARD-C82','Ben W Wong','ben.wong@example.test','+61 400111222','1990-09-03'),
('CARD-C83','Carla Singh','carla.singh@example.test','0422-333-444','1985-01-27'),
('CARD-C84','Emma Brown','emma.brown@example.test','0444 555 666','1993-12-02')
ON CONFLICT DO NOTHING;

INSERT INTO src_cards.card VALUES
('CC-7001','CARD-C81','Rewards Visa','2022-04-12',12000.00,1850.00,'ACTIVE'),
('CC-7002','CARD-C82','Low Rate Mastercard','2021-09-03',8000.00,620.00,'ACTIVE'),
('CC-7003','CARD-C83','Rewards Visa','2023-01-27',10000.00,0.00,'ACTIVE'),
('CC-7004','CARD-C84','Low Rate Mastercard','2024-12-02',6000.00,970.00,'ACTIVE')
ON CONFLICT DO NOTHING;

INSERT INTO src_cards.card_transaction VALUES
('CT-8001','CC-7001','2026-09-20 14:05',245.90,'Harbour Electronics','CONTACTLESS'),
('CT-8002','CC-7001','2026-09-21 19:22',68.50,'Fresh Market','MOBILE'),
('CT-8003','CC-7002','2026-09-21 10:16',112.00,'Fuel Stop','CONTACTLESS'),
('CT-8004','CC-7003','2026-09-22 11:41',1840.00,'Home Furnishings','ONLINE'),
('CT-8005','CC-7004','2026-09-23 20:03',155.20,'Restaurant 88','CONTACTLESS'),
('CT-8006','CC-7001','2026-09-24 07:30',1299.00,'Unknown Online Store','ONLINE')
ON CONFLICT DO NOTHING;

INSERT INTO src_lending.borrower VALUES
('LN-B11','Alice Chen','alice.chen@example.test','0412345678','1988-04-12'),
('LN-B12','Carla Singh','CARLA.SINGH@example.test','+61 422333444','1985-01-27'),
('LN-B13','David Li','david.li@example.test','0433 444 555','1979-06-18'),
('LN-B14','Grace Park','grace.park@example.test','0466 777 888','1991-11-09')
ON CONFLICT DO NOTHING;

INSERT INTO src_lending.loan VALUES
('HOME-501','LN-B11','HOME_LOAN','2020-06-15',385000.00,'ACTIVE'),
('PERSONAL-502','LN-B12','PERSONAL_LOAN','2024-02-01',8400.00,'ACTIVE'),
('HOME-503','LN-B13','HOME_LOAN','2018-10-12',210000.00,'ACTIVE'),
('PERSONAL-504','LN-B14','PERSONAL_LOAN','2025-01-06',12500.00,'ACTIVE')
ON CONFLICT DO NOTHING;

INSERT INTO src_lending.repayment VALUES
('RP-6001','HOME-501','2026-09-20 07:00',2450.00,'DIRECT_DEBIT'),
('RP-6002','PERSONAL-502','2026-09-21 07:00',420.00,'DIRECT_DEBIT'),
('RP-6003','HOME-503','2026-09-22 07:00',1980.00,'DIRECT_DEBIT'),
('RP-6004','PERSONAL-504','2026-09-23 07:00',550.00,'DIRECT_DEBIT')
ON CONFLICT DO NOTHING;

INSERT INTO src_crm.contact VALUES
('CRM-301','Alice Chen','alice.chen@example.test','0412-345-678','1988-04-12'),
('CRM-302','Ben Wong','ben.wong@example.test','0400 111 222','1990-09-03'),
('CRM-303','Carla Singh','carla.singh@example.test','+61 (422) 333 444','1985-01-27'),
('CRM-304','Emma Brown','emma.brown@example.test','0444555666','1993-12-02'),
('CRM-305','Farah Khan',NULL,'0455 666 777','1987-08-14'),
('CRM-306','Grace Park','grace.park@example.test','0466 777 888','1991-11-09')
ON CONFLICT DO NOTHING;

INSERT INTO src_crm.interaction VALUES
('INT-401','CRM-301','2026-09-20 15:10','SERVICE_CALL','PHONE','Asked about card payment'),
('INT-402','CRM-302','2026-09-21 13:25','PRODUCT_ENQUIRY','BRANCH','Asked about home loans'),
('INT-403','CRM-303','2026-09-22 09:40','COMPLAINT','WEB','Card transaction query'),
('INT-404','CRM-304','2026-09-23 16:15','SERVICE_CHAT','APP','Updated contact preference'),
('INT-405','CRM-306','2026-09-24 10:00','PRODUCT_ENQUIRY','PHONE','Personal loan enquiry')
ON CONFLICT DO NOTHING;

INSERT INTO src_crm.consent VALUES
('CNS-501','CRM-301','CDR_DATA_SHARING','ACTIVE','2026-09-18 12:00'),
('CNS-502','CRM-302','CDR_DATA_SHARING','WITHDRAWN','2026-09-19 12:00'),
('CNS-503','CRM-303','CDR_DATA_SHARING','ACTIVE','2026-09-20 12:00'),
('CNS-504','CRM-304','CDR_DATA_SHARING','ACTIVE','2026-09-21 12:00'),
('CNS-505','CRM-305','CDR_DATA_SHARING','PENDING','2026-09-22 12:00'),
('CNS-506','CRM-306','CDR_DATA_SHARING','ACTIVE','2026-09-23 12:00')
ON CONFLICT DO NOTHING;
