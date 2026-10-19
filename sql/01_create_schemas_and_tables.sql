-- Poverty Bank prototype: PostgreSQL schemas and table structures.
-- Run once in the PostgreSQL database used for the lab.

CREATE SCHEMA IF NOT EXISTS src_deposits;
CREATE SCHEMA IF NOT EXISTS src_cards;
CREATE SCHEMA IF NOT EXISTS src_lending;
CREATE SCHEMA IF NOT EXISTS src_crm;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS vault;
CREATE SCHEMA IF NOT EXISTS dw;

-- Successful, verified loads only. Retrieval time is a separate API timestamp.
CREATE TABLE IF NOT EXISTS dw.pipeline_load (
    load_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile text NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT current_timestamp,
    data_as_of date NOT NULL,
    source_profiles integer NOT NULL,
    transactions integer NOT NULL
);

-- Source system 1: deposits
CREATE TABLE IF NOT EXISTS src_deposits.customer (
    customer_id       text PRIMARY KEY,
    full_name         text NOT NULL,
    email             text,
    phone             text,
    date_of_birth     date
);
CREATE TABLE IF NOT EXISTS src_deposits.account (
    account_id        text PRIMARY KEY,
    customer_id       text NOT NULL REFERENCES src_deposits.customer(customer_id),
    account_type      text NOT NULL,
    opened_on         date NOT NULL,
    current_balance   numeric(14,2) NOT NULL,
    status            text NOT NULL
);
CREATE TABLE IF NOT EXISTS src_deposits.deposit_transaction (
    transaction_id    text PRIMARY KEY,
    account_id        text NOT NULL REFERENCES src_deposits.account(account_id),
    transaction_ts    timestamp NOT NULL,
    transaction_type  text NOT NULL,
    amount            numeric(14,2) NOT NULL,
    merchant          text,
    channel           text NOT NULL
);

-- Source system 2: cards
CREATE TABLE IF NOT EXISTS src_cards.cardholder (
    cardholder_id     text PRIMARY KEY,
    full_name         text NOT NULL,
    email             text,
    phone             text,
    date_of_birth     date
);
CREATE TABLE IF NOT EXISTS src_cards.card (
    card_id           text PRIMARY KEY,
    cardholder_id     text NOT NULL REFERENCES src_cards.cardholder(cardholder_id),
    product_name      text NOT NULL,
    opened_on         date NOT NULL,
    credit_limit      numeric(14,2) NOT NULL,
    current_balance   numeric(14,2) NOT NULL,
    status            text NOT NULL
);
CREATE TABLE IF NOT EXISTS src_cards.card_transaction (
    transaction_id    text PRIMARY KEY,
    card_id           text NOT NULL REFERENCES src_cards.card(card_id),
    transaction_ts    timestamp NOT NULL,
    amount            numeric(14,2) NOT NULL,
    merchant          text NOT NULL,
    channel           text NOT NULL
);

-- Source system 3: lending
CREATE TABLE IF NOT EXISTS src_lending.borrower (
    borrower_id       text PRIMARY KEY,
    full_name         text NOT NULL,
    email             text,
    phone             text,
    date_of_birth     date
);
CREATE TABLE IF NOT EXISTS src_lending.loan (
    loan_id           text PRIMARY KEY,
    borrower_id       text NOT NULL REFERENCES src_lending.borrower(borrower_id),
    loan_type         text NOT NULL,
    opened_on         date NOT NULL,
    principal_balance numeric(14,2) NOT NULL,
    status            text NOT NULL
);
CREATE TABLE IF NOT EXISTS src_lending.repayment (
    repayment_id      text PRIMARY KEY,
    loan_id           text NOT NULL REFERENCES src_lending.loan(loan_id),
    repayment_ts      timestamp NOT NULL,
    amount            numeric(14,2) NOT NULL,
    channel           text NOT NULL
);

-- Source system 4: CRM and CDR consent
CREATE TABLE IF NOT EXISTS src_crm.contact (
    crm_contact_id    text PRIMARY KEY,
    full_name         text NOT NULL,
    email             text,
    phone             text,
    date_of_birth     date
);
CREATE TABLE IF NOT EXISTS src_crm.interaction (
    interaction_id    text PRIMARY KEY,
    crm_contact_id    text NOT NULL REFERENCES src_crm.contact(crm_contact_id),
    interaction_ts    timestamp NOT NULL,
    interaction_type  text NOT NULL,
    channel           text NOT NULL,
    notes             text
);
CREATE TABLE IF NOT EXISTS src_crm.consent (
    consent_id        text PRIMARY KEY,
    crm_contact_id    text NOT NULL REFERENCES src_crm.contact(crm_contact_id),
    consent_type      text NOT NULL,
    consent_status    text NOT NULL,
    updated_at        timestamp NOT NULL
);

-- Staging: standardised customer records and source-to-canonical identity map.
CREATE TABLE IF NOT EXISTS stg.customer_source (
    source_system       text NOT NULL,
    source_customer_id  text NOT NULL,
    full_name           text NOT NULL,
    email               text,
    phone               text,
    date_of_birth       date,
    normalized_email    text,
    normalized_phone    text,
    extracted_at        timestamp NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (source_system, source_customer_id)
);
CREATE TABLE IF NOT EXISTS stg.customer_identity (
    source_system       text NOT NULL,
    source_customer_id  text NOT NULL,
    canonical_customer_id text NOT NULL,
    match_status        text NOT NULL CHECK (match_status IN ('MATCHED', 'UNMATCHED', 'AMBIGUOUS')),
    match_method        text NOT NULL,
    normalized_email   text,
    PRIMARY KEY (source_system, source_customer_id)
);
CREATE TABLE IF NOT EXISTS stg.account_source (
    source_system       text NOT NULL,
    source_account_id   text NOT NULL,
    source_customer_id  text NOT NULL,
    canonical_customer_id text NOT NULL,
    product_type        text NOT NULL,
    product_name        text NOT NULL,
    opened_on           date NOT NULL,
    current_balance     numeric(14,2) NOT NULL,
    account_status      text NOT NULL,
    PRIMARY KEY (source_system, source_account_id)
);
CREATE TABLE IF NOT EXISTS stg.transaction_source (
    source_system       text NOT NULL,
    source_transaction_id text NOT NULL,
    source_account_id   text NOT NULL,
    canonical_customer_id text NOT NULL,
    transaction_ts      timestamp NOT NULL,
    transaction_type    text NOT NULL,
    amount              numeric(14,2) NOT NULL,
    merchant            text,
    channel             text NOT NULL,
    PRIMARY KEY (source_system, source_transaction_id)
);
CREATE TABLE IF NOT EXISTS stg.repayment_source (
    source_system       text NOT NULL,
    source_repayment_id  text NOT NULL,
    source_account_id   text NOT NULL,
    canonical_customer_id text NOT NULL,
    repayment_ts        timestamp NOT NULL,
    amount              numeric(14,2) NOT NULL,
    channel             text NOT NULL,
    PRIMARY KEY (source_system, source_repayment_id)
);

-- Compact Data Vault 2.0-inspired integration layer.
CREATE TABLE IF NOT EXISTS vault.hub_customer (
    customer_hk          text PRIMARY KEY,
    canonical_customer_id text NOT NULL UNIQUE,
    load_timestamp       timestamp NOT NULL,
    record_source        text NOT NULL
);
CREATE TABLE IF NOT EXISTS vault.hub_account (
    account_hk           text PRIMARY KEY,
    account_bk           text NOT NULL UNIQUE,
    load_timestamp       timestamp NOT NULL,
    record_source        text NOT NULL
);
CREATE TABLE IF NOT EXISTS vault.link_customer_account (
    customer_account_hk  text PRIMARY KEY,
    customer_hk          text NOT NULL REFERENCES vault.hub_customer(customer_hk),
    account_hk           text NOT NULL REFERENCES vault.hub_account(account_hk),
    load_timestamp       timestamp NOT NULL,
    record_source        text NOT NULL,
    UNIQUE (customer_hk, account_hk)
);
CREATE TABLE IF NOT EXISTS vault.sat_customer_profile (
    customer_hk          text NOT NULL REFERENCES vault.hub_customer(customer_hk),
    load_timestamp       timestamp NOT NULL,
    record_source        text NOT NULL,
    source_customer_id   text NOT NULL,
    full_name            text NOT NULL,
    email                text,
    phone                text,
    date_of_birth        date,
    PRIMARY KEY (customer_hk, load_timestamp, record_source, source_customer_id)
);
CREATE TABLE IF NOT EXISTS vault.sat_account_details (
    account_hk           text NOT NULL REFERENCES vault.hub_account(account_hk),
    load_timestamp       timestamp NOT NULL,
    record_source        text NOT NULL,
    product_type         text NOT NULL,
    product_name         text NOT NULL,
    opened_on            date NOT NULL,
    current_balance      numeric(14,2) NOT NULL,
    account_status       text NOT NULL,
    PRIMARY KEY (account_hk, load_timestamp, record_source)
);
CREATE TABLE IF NOT EXISTS vault.sat_customer_consent (
    customer_hk          text NOT NULL REFERENCES vault.hub_customer(customer_hk),
    load_timestamp       timestamp NOT NULL,
    record_source        text NOT NULL,
    consent_id           text NOT NULL,
    consent_type         text NOT NULL,
    consent_status       text NOT NULL,
    consent_updated_at   timestamp NOT NULL,
    PRIMARY KEY (customer_hk, load_timestamp, record_source, consent_id)
);

-- Dimensional presentation layer.
CREATE TABLE IF NOT EXISTS dw.dim_customer (
    customer_key         bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    canonical_customer_id text NOT NULL UNIQUE,
    customer_name        text NOT NULL,
    email                text,
    phone                text,
    date_of_birth        date,
    identity_status      text NOT NULL
);
CREATE TABLE IF NOT EXISTS dw.dim_product (
    product_key          bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    product_type         text NOT NULL,
    product_name         text NOT NULL,
    UNIQUE (product_type, product_name)
);
CREATE TABLE IF NOT EXISTS dw.dim_account (
    account_key          bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    source_system        text NOT NULL,
    source_account_id    text NOT NULL,
    canonical_customer_id text NOT NULL REFERENCES dw.dim_customer(canonical_customer_id),
    product_key          bigint NOT NULL REFERENCES dw.dim_product(product_key),
    opened_on            date NOT NULL,
    current_balance      numeric(14,2) NOT NULL,
    account_status       text NOT NULL,
    UNIQUE (source_system, source_account_id)
);
CREATE TABLE IF NOT EXISTS dw.dim_date (
    date_key             integer PRIMARY KEY,
    calendar_date        date NOT NULL UNIQUE,
    year_number          integer NOT NULL,
    quarter_number       integer NOT NULL,
    month_number         integer NOT NULL,
    day_number           integer NOT NULL
);
CREATE TABLE IF NOT EXISTS dw.fact_transaction (
    transaction_key     bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    source_system       text NOT NULL,
    source_transaction_id text NOT NULL,
    customer_key        bigint NOT NULL REFERENCES dw.dim_customer(customer_key),
    account_key         bigint NOT NULL REFERENCES dw.dim_account(account_key),
    date_key            integer NOT NULL REFERENCES dw.dim_date(date_key),
    transaction_ts      timestamp NOT NULL,
    transaction_type    text NOT NULL,
    amount              numeric(14,2) NOT NULL,
    merchant            text,
    channel             text NOT NULL,
    UNIQUE (source_system, source_transaction_id)
);
CREATE TABLE IF NOT EXISTS dw.fact_loan_repayment (
    repayment_key       bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    source_system       text NOT NULL,
    source_repayment_id text NOT NULL,
    customer_key        bigint NOT NULL REFERENCES dw.dim_customer(customer_key),
    account_key         bigint NOT NULL REFERENCES dw.dim_account(account_key),
    date_key            integer NOT NULL REFERENCES dw.dim_date(date_key),
    repayment_ts        timestamp NOT NULL,
    amount              numeric(14,2) NOT NULL,
    channel             text NOT NULL,
    UNIQUE (source_system, source_repayment_id)
);
CREATE TABLE IF NOT EXISTS dw.customer_consent (
    canonical_customer_id text NOT NULL REFERENCES dw.dim_customer(canonical_customer_id),
    consent_type         text NOT NULL,
    consent_status       text NOT NULL,
    updated_at           timestamp NOT NULL,
    PRIMARY KEY (canonical_customer_id, consent_type)
);
CREATE TABLE IF NOT EXISTS dw.fact_customer_interaction (
    interaction_key     bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    source_interaction_id text NOT NULL UNIQUE,
    customer_key        bigint NOT NULL REFERENCES dw.dim_customer(customer_key),
    date_key            integer NOT NULL REFERENCES dw.dim_date(date_key),
    interaction_ts      timestamp NOT NULL,
    interaction_type    text NOT NULL,
    channel             text NOT NULL,
    notes               text
);
