"""Independent source-to-warehouse checks for the extended deterministic dataset."""
from collections import defaultdict
from datetime import date
import hashlib

from verify_pipeline import rows, compare_records


def verify_showcase(cursor, checks):
    profiles = rows(cursor, '''
        SELECT 'SRC_DEPOSITS',customer_id,email,date_of_birth FROM src_deposits.customer
        UNION ALL SELECT 'SRC_CARDS',cardholder_id,email,date_of_birth FROM src_cards.cardholder
        UNION ALL SELECT 'SRC_LENDING',borrower_id,email,date_of_birth FROM src_lending.borrower
        UNION ALL SELECT 'SRC_CRM',crm_contact_id,email,date_of_birth FROM src_crm.contact''')
    birthdays = defaultdict(set)
    for _, _, email, dob in profiles:
        if email and dob:
            birthdays[email.strip().lower()].add(dob)
    mapping, expected = {}, []
    for source, key, email, _ in profiles:
        normalized = email.strip().lower() if email else None
        status = 'UNMATCHED' if not normalized else 'AMBIGUOUS' if len(birthdays[normalized]) > 1 else 'MATCHED'
        prefix = 'CUST-' if status == 'MATCHED' else status + '-'
        token = normalized if status == 'MATCHED' else source + ':' + key
        canonical = prefix + hashlib.md5(token.encode()).hexdigest()[:10].upper()
        mapping[source, key] = canonical
        expected.append((source, key, canonical, status))
    compare_records(checks, 'all source identities independently reconstructed',
                    rows(cursor, 'SELECT source_system,source_customer_id,canonical_customer_id,match_status FROM stg.customer_identity'), expected)
    checks.equal('showcase has 50 distinct identities', len(set(mapping.values())), 50)
    compare_records(checks, 'exactly one warehouse customer for each integrated identity',
                    rows(cursor, 'SELECT canonical_customer_id FROM dw.dim_customer'), [(x,) for x in set(mapping.values())])
    compare_records(checks, 'Customer 360 preserves identity grain',
                    rows(cursor, 'SELECT canonical_customer_id FROM dw.vw_customer_360'), [(x,) for x in set(mapping.values())])
    compare_records(checks, 'Vault customer business keys and hashes',
                    rows(cursor, 'SELECT canonical_customer_id,customer_hk FROM vault.hub_customer'),
                    [(key, hashlib.md5(key.encode()).hexdigest()) for key in set(mapping.values())])
    accounts = rows(cursor, '''SELECT source_system,source_account_id,canonical_customer_id,
        product_type,product_name,opened_on,current_balance,account_status FROM stg.account_source''')
    account_hash = lambda source, key: hashlib.md5((source+':'+key).encode()).hexdigest()
    compare_records(checks, 'Vault account business keys and hashes',
                    rows(cursor, 'SELECT account_bk,account_hk FROM vault.hub_account'),
                    [(source+':'+key,account_hash(source,key)) for source,key,*_ in accounts])
    compare_records(checks, 'Vault customer-account relationships', rows(cursor, '''
        SELECT customer_account_hk,customer_hk,account_hk,record_source FROM vault.link_customer_account'''),
        [(hashlib.md5((canonical+'|'+source+':'+key).encode()).hexdigest(),
          hashlib.md5(canonical.encode()).hexdigest(),account_hash(source,key),source)
         for source,key,canonical,*_ in accounts])
    compare_records(checks, 'latest Vault account snapshots preserve staged values', rows(cursor, '''
        SELECT DISTINCT ON (account_hk) account_hk,record_source,product_type,product_name,
               opened_on,current_balance,account_status FROM vault.sat_account_details
        ORDER BY account_hk,load_timestamp DESC'''),
        [(account_hash(source,key),source,*details) for source,key,canonical,*details in accounts])
    staged_profiles = rows(cursor, '''SELECT source_system,source_customer_id,full_name,email,phone,date_of_birth
        FROM stg.customer_source''')
    compare_records(checks, 'latest Vault profile snapshots retain every source profile', rows(cursor, '''
        SELECT DISTINCT ON (record_source,source_customer_id) customer_hk,record_source,
        source_customer_id,full_name,email,phone,date_of_birth FROM vault.sat_customer_profile
        ORDER BY record_source,source_customer_id,load_timestamp DESC'''),
        [(hashlib.md5(mapping[source,key].encode()).hexdigest(),source,key,*details)
         for source,key,*details in staged_profiles])
    raw_consents = rows(cursor, '''SELECT crm_contact_id,consent_id,consent_type,consent_status,updated_at
        FROM src_crm.consent''')
    compare_records(checks, 'Vault retains all consent events including historical types', rows(cursor, '''
        SELECT DISTINCT ON (record_source,consent_id) customer_hk,consent_id,consent_type,
        consent_status,consent_updated_at FROM vault.sat_customer_consent
        ORDER BY record_source,consent_id,load_timestamp DESC'''),
        [(hashlib.md5(mapping['SRC_CRM',contact].encode()).hexdigest(),*details)
         for contact,*details in raw_consents])
    raw = rows(cursor, '''
        SELECT 'SRC_DEPOSITS',t.transaction_id,t.account_id,a.customer_id,t.transaction_ts,t.amount,t.merchant,t.channel
        FROM src_deposits.deposit_transaction t JOIN src_deposits.account a USING(account_id)
        UNION ALL SELECT 'SRC_CARDS',t.transaction_id,t.card_id,a.cardholder_id,t.transaction_ts,t.amount,t.merchant,t.channel
        FROM src_cards.card_transaction t JOIN src_cards.card a USING(card_id)''')
    expected_transactions = [(s,k,a,mapping[s,c],ts,amount,merchant,channel) for s,k,a,c,ts,amount,merchant,channel in raw]
    compare_records(checks, 'every source transaction and association reaches DW', rows(cursor, '''
        SELECT t.source_system,t.source_transaction_id,a.source_account_id,c.canonical_customer_id,
               t.transaction_ts,t.amount,t.merchant,t.channel FROM dw.fact_transaction t
        JOIN dw.dim_customer c USING(customer_key) JOIN dw.dim_account a USING(account_key)'''), expected_transactions)
    compare_records(checks, 'every source transaction reaches staging', rows(cursor, '''
        SELECT source_system,source_transaction_id,source_account_id,canonical_customer_id,
        transaction_ts,amount,merchant,channel FROM stg.transaction_source'''), expected_transactions)
    raw_repayments = rows(cursor, '''SELECT r.repayment_id,r.loan_id,l.borrower_id,r.repayment_ts,r.amount,r.channel
        FROM src_lending.repayment r JOIN src_lending.loan l USING(loan_id)''')
    compare_records(checks, 'all source repayments and identities reach DW', rows(cursor, '''
        SELECT r.source_repayment_id,a.source_account_id,c.canonical_customer_id,r.repayment_ts,r.amount,r.channel
        FROM dw.fact_loan_repayment r JOIN dw.dim_account a USING(account_key) JOIN dw.dim_customer c USING(customer_key)'''),
        [(k,a,mapping['SRC_LENDING',c],ts,amount,ch) for k,a,c,ts,amount,ch in raw_repayments])
    raw_interactions = rows(cursor, 'SELECT interaction_id,crm_contact_id,interaction_ts,interaction_type,channel,notes FROM src_crm.interaction')
    compare_records(checks, 'all source CRM interactions reach DW', rows(cursor, '''
        SELECT i.source_interaction_id,c.canonical_customer_id,i.interaction_ts,i.interaction_type,i.channel,i.notes
        FROM dw.fact_customer_interaction i JOIN dw.dim_customer c USING(customer_key)'''),
        [(k,mapping['SRC_CRM',c],ts,kind,ch,note) for k,c,ts,kind,ch,note in raw_interactions])
    checks.equal('three suggestion branches and no-suggestion all represented',
                 {x[0] for x in rows(cursor,'SELECT DISTINCT suggested_action FROM dw.vw_product_opportunity')},
                 {'HOME_OR_PERSONAL_LOAN_REVIEW','CARD_REPAYMENT_SUPPORT_REVIEW','CREDIT_CARD_OFFER_REVIEW','NO_RULE_BASED_SUGGESTION'})
    checks.equal('uncertain identity/account data never produces a product suggestion', rows(cursor, '''
        SELECT count(*) FROM dw.vw_product_opportunity WHERE review_readiness <> 'READY'
        AND suggested_action <> 'NO_RULE_BASED_SUGGESTION' ''')[0][0], 0)
    checks.equal('closed-only cards are not current holdings', rows(cursor, '''
        SELECT p.has_credit_card FROM dw.vw_product_opportunity p
        JOIN stg.customer_identity i USING(canonical_customer_id)
        WHERE i.source_system='SRC_CARDS' AND i.source_customer_id='EXT-CARD-11' ''')[0][0], False)
    checks.equal('fixed transaction coverage dates', rows(cursor,'SELECT min(transaction_ts)::date,max(transaction_ts)::date FROM dw.fact_transaction'),
                 [(date(2026,7,1),date(2026,10,15))])
    checks.at_least('representative transaction volume',len(raw),1500)
    checks.at_least('transaction dates span the full period',rows(cursor,'SELECT count(DISTINCT transaction_ts::date) FROM dw.fact_transaction')[0][0],100)
    checks.equal('all active statuses and closed cases represented',
                 {x[0] for x in rows(cursor,'SELECT DISTINCT account_status FROM dw.dim_account')},{'OPEN','ACTIVE','CLOSED'})
    checks.equal('risk drilldown recomputes each aggregate',rows(cursor,'''WITH detail AS (
        SELECT canonical_customer_id,transaction_ts::date AS calendar_date,count(*) AS n,
        sum(abs(amount)) AS gross,max(abs(amount)) AS largest FROM dw.vw_transaction_detail GROUP BY 1,2)
        SELECT count(*) FROM detail d FULL JOIN dw.vw_transaction_risk_monitor r USING(canonical_customer_id,calendar_date)
        WHERE d.n IS DISTINCT FROM r.transaction_count OR d.gross IS DISTINCT FROM r.gross_transaction_value
        OR d.largest IS DISTINCT FROM r.largest_transaction''')[0][0],0)
    checks.equal('activity timeline covers all three fact types',rows(cursor,'SELECT count(*) FROM dw.vw_customer_activity')[0][0],
                 len(raw)+len(raw_repayments)+len(raw_interactions))
    # Date, customer and account must agree; totals alone cannot detect mislinks.
    checks.equal('fact date and account ownership agree',rows(cursor,'''SELECT count(*) FROM dw.fact_transaction t
        JOIN dw.dim_account a USING(account_key) JOIN dw.dim_customer c USING(customer_key) JOIN dw.dim_date d USING(date_key)
        WHERE c.canonical_customer_id<>a.canonical_customer_id OR d.calendar_date<>t.transaction_ts::date''')[0][0],0)
