-- ============================================================
-- Aurestra — SQLite Migration
-- Run once on a fresh SQLite database.
-- Usage:  sqlite3 aurestra.db < sqlite.sql
-- ============================================================

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── users ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    email                 VARCHAR(120) NOT NULL UNIQUE,
    full_name             VARCHAR(100),
    google_id             VARCHAR(50),
    google_email          VARCHAR(120),
    google_refresh_token  VARCHAR(255),
    otp_code              VARCHAR(6),
    otp_expiry            DATETIME,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    avatar_url            VARCHAR(512),
    notifications_enabled INTEGER DEFAULT 1
);

-- ── categories ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       VARCHAR(100) NOT NULL UNIQUE,
    icon       VARCHAR(50)  NOT NULL DEFAULT 'cash',
    color      VARCHAR(20)  NOT NULL DEFAULT '#64748B',
    cat_type   VARCHAR(20)  NOT NULL DEFAULT 'spending',
    is_default INTEGER DEFAULT 0
);

-- ── categorization_rules ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS categorization_rules (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    merchant_pattern VARCHAR(255) NOT NULL,
    category_id      INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    hit_count        INTEGER DEFAULT 0
);

-- ── account_balances ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS account_balances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          VARCHAR(50) NOT NULL UNIQUE,
    current_balance REAL NOT NULL DEFAULT 0.0,
    last_updated    DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_manual       INTEGER DEFAULT 0
);

-- ── transactions ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transactions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source                VARCHAR(20)  NOT NULL,
    date                  DATETIME     NOT NULL,
    purpose               VARCHAR(255),
    amount                REAL         NOT NULL,
    sender                VARCHAR(255),
    receiver              VARCHAR(255),
    transaction_id        VARCHAR(50)  UNIQUE,
    transaction_hash      VARCHAR(64)  UNIQUE,
    sms_hash              VARCHAR(64),
    notes                 VARCHAR(255),
    type                  VARCHAR(10)  NOT NULL,
    categorization_status VARCHAR(20)  DEFAULT 'pending',
    category_id           INTEGER      REFERENCES categories(id) ON DELETE SET NULL,
    is_deleted            INTEGER DEFAULT 0,
    is_spam               INTEGER DEFAULT 0,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transactions_sms_hash ON transactions(sms_hash);

-- ── budgets ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS budgets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    month          VARCHAR(7) NOT NULL UNIQUE,
    total_budget   REAL NOT NULL,
    needs          REAL NOT NULL DEFAULT 0.0,
    wants          REAL NOT NULL DEFAULT 0.0,
    saving         REAL NOT NULL DEFAULT 0.0,
    total_expenses REAL NOT NULL DEFAULT 0.0,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── savings_goals ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS savings_goals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           VARCHAR(100) NOT NULL,
    target_amount  REAL NOT NULL,
    current_amount REAL DEFAULT 0.0,
    emoji          VARCHAR(20) DEFAULT '💰',
    deadline       DATE,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── monthly_balances ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monthly_balances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          VARCHAR(20) NOT NULL,
    month           VARCHAR(7) NOT NULL UNIQUE,
    opening_balance REAL NOT NULL,
    closing_balance REAL NOT NULL,
    expense         REAL,
    savings         REAL,
    fetched_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── sms_history ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sms_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    device_sms_id    VARCHAR(100),
    sender           VARCHAR(50),
    body             TEXT,
    device_timestamp DATETIME,
    sms_hash         VARCHAR(64) NOT NULL UNIQUE,
    status           VARCHAR(20) DEFAULT 'pending',
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── device_tokens ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS device_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      VARCHAR(255) NOT NULL UNIQUE,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── financial_insights ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial_insights (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    month        VARCHAR(7) NOT NULL,
    content      TEXT NOT NULL,
    metrics_json TEXT,
    tags         VARCHAR(255),
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── statement_analysis ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS statement_analysis (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    month             VARCHAR(7) NOT NULL UNIQUE,
    opening_balance   REAL DEFAULT 0.0,
    closing_balance   REAL DEFAULT 0.0,
    total_income      REAL DEFAULT 0.0,
    total_expense     REAL DEFAULT 0.0,
    net_result        REAL DEFAULT 0.0,
    status            VARCHAR(20),
    breakdown_json    TEXT,
    analysis_date     DATETIME DEFAULT CURRENT_TIMESTAMP,
    balance_applied   INTEGER DEFAULT 0,
    reviewed_at       DATETIME,
    statement_id      VARCHAR(64),
    transaction_ids   TEXT,
    processing_status VARCHAR(20) DEFAULT 'success',
    processing_notes  TEXT
);
