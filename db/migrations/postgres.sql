-- ============================================================
-- Aurestra — PostgreSQL Migration
-- Run once on a fresh PostgreSQL database.
-- Usage:  psql -U <user> -d <dbname> -f postgres.sql
-- ============================================================

-- ── users ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                    SERIAL PRIMARY KEY,
    email                 VARCHAR(120) NOT NULL UNIQUE,
    full_name             VARCHAR(100),
    google_id             VARCHAR(50),
    google_email          VARCHAR(120),
    google_refresh_token  VARCHAR(255),
    otp_code              VARCHAR(6),
    otp_expiry            TIMESTAMP,
    created_at            TIMESTAMP DEFAULT NOW(),
    avatar_url            VARCHAR(512),
    notifications_enabled BOOLEAN DEFAULT TRUE
);

-- ── categories ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS categories (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL UNIQUE,
    icon       VARCHAR(50)  NOT NULL DEFAULT 'cash',
    color      VARCHAR(20)  NOT NULL DEFAULT '#64748B',
    cat_type   VARCHAR(20)  NOT NULL DEFAULT 'spending',
    is_default BOOLEAN DEFAULT FALSE
);

-- ── categorization_rules ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS categorization_rules (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    merchant_pattern VARCHAR(255) NOT NULL,
    category_id      INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    created_at       TIMESTAMP DEFAULT NOW(),
    hit_count        INTEGER DEFAULT 0
);

-- ── account_balances ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS account_balances (
    id              SERIAL PRIMARY KEY,
    source          VARCHAR(50) NOT NULL UNIQUE,
    current_balance FLOAT NOT NULL DEFAULT 0.0,
    last_updated    TIMESTAMP DEFAULT NOW(),
    is_manual       BOOLEAN DEFAULT FALSE
);

-- ── transactions ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transactions (
    id                   SERIAL PRIMARY KEY,
    source               VARCHAR(20)  NOT NULL,
    date                 TIMESTAMP    NOT NULL,
    purpose              VARCHAR(255),
    amount               FLOAT        NOT NULL,
    sender               VARCHAR(255),
    receiver             VARCHAR(255),
    transaction_id       VARCHAR(50)  UNIQUE,
    transaction_hash     VARCHAR(64)  UNIQUE,
    sms_hash             VARCHAR(64),
    notes                VARCHAR(255),
    type                 VARCHAR(10)  NOT NULL,
    categorization_status VARCHAR(20) DEFAULT 'pending',
    category_id          INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    is_deleted           BOOLEAN DEFAULT FALSE,
    is_spam              BOOLEAN DEFAULT FALSE,
    created_at           TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_sms_hash ON transactions(sms_hash);

-- ── budgets ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS budgets (
    id             SERIAL PRIMARY KEY,
    month          VARCHAR(7) NOT NULL UNIQUE,
    total_budget   FLOAT NOT NULL,
    needs          FLOAT NOT NULL DEFAULT 0.0,
    wants          FLOAT NOT NULL DEFAULT 0.0,
    saving         FLOAT NOT NULL DEFAULT 0.0,
    total_expenses FLOAT NOT NULL DEFAULT 0.0,
    created_at     TIMESTAMP DEFAULT NOW()
);

-- ── savings_goals ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS savings_goals (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    target_amount  FLOAT NOT NULL,
    current_amount FLOAT DEFAULT 0.0,
    emoji          VARCHAR(20) DEFAULT '💰',
    deadline       DATE,
    created_at     TIMESTAMP DEFAULT NOW()
);

-- ── monthly_balances ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monthly_balances (
    id              SERIAL PRIMARY KEY,
    source          VARCHAR(20) NOT NULL,
    month           VARCHAR(7) NOT NULL UNIQUE,
    opening_balance FLOAT NOT NULL,
    closing_balance FLOAT NOT NULL,
    expense         FLOAT,
    savings         FLOAT,
    fetched_at      TIMESTAMP DEFAULT NOW()
);

-- ── sms_history ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sms_history (
    id               SERIAL PRIMARY KEY,
    device_sms_id    VARCHAR(100),
    sender           VARCHAR(50),
    body             TEXT,
    device_timestamp TIMESTAMP,
    sms_hash         VARCHAR(64) NOT NULL UNIQUE,
    status           VARCHAR(20) DEFAULT 'pending',
    created_at       TIMESTAMP DEFAULT NOW()
);

-- ── device_tokens ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS device_tokens (
    id         SERIAL PRIMARY KEY,
    token      VARCHAR(255) NOT NULL UNIQUE,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    last_seen  TIMESTAMP DEFAULT NOW()
);

-- ── financial_insights ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial_insights (
    id           SERIAL PRIMARY KEY,
    month        VARCHAR(7) NOT NULL,
    content      TEXT NOT NULL,
    metrics_json TEXT,
    tags         VARCHAR(255),
    created_at   TIMESTAMP DEFAULT NOW()
);

-- ── statement_analysis ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS statement_analysis (
    id                SERIAL PRIMARY KEY,
    month             VARCHAR(7) NOT NULL UNIQUE,
    opening_balance   FLOAT DEFAULT 0.0,
    closing_balance   FLOAT DEFAULT 0.0,
    total_income      FLOAT DEFAULT 0.0,
    total_expense     FLOAT DEFAULT 0.0,
    net_result        FLOAT DEFAULT 0.0,
    status            VARCHAR(20),
    breakdown_json    TEXT,
    analysis_date     TIMESTAMP DEFAULT NOW(),
    balance_applied   BOOLEAN DEFAULT FALSE,
    reviewed_at       TIMESTAMP,
    statement_id      VARCHAR(64),
    transaction_ids   TEXT,
    processing_status VARCHAR(20) DEFAULT 'success',
    processing_notes  TEXT
);
