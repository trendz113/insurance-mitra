-- Insurance Mitra database schema
-- Run this once against the Railway Postgres instance to set up tables.
--
-- No login/accounts. user_id is now an anonymous UUID string generated
-- per browser session (see get_anon_user_id() in app.py) -- there is no
-- "users" table backing it anymore, so it's a plain VARCHAR with no
-- foreign key constraint.
--
-- This version drops and recreates every table that referenced the old
-- users table, since there is no production data to preserve yet.

DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS feedback;
DROP TABLE IF EXISTS policy_recommendations;
DROP TABLE IF EXISTS case_events;
DROP TABLE IF EXISTS disclosure_summaries;
DROP TABLE IF EXISTS life_claim_cases;
DROP TABLE IF EXISTS claim_cases;
DROP TABLE IF EXISTS users;

CREATE TABLE IF NOT EXISTS claim_cases (
    id SERIAL PRIMARY KEY,
    case_ref VARCHAR(20) UNIQUE NOT NULL, -- e.g. IM-2026-4821
    user_id VARCHAR(36) NOT NULL,         -- anonymous per-session UUID, no login
    insurer VARCHAR(255),
    policy_name VARCHAR(255),
    claim_amount NUMERIC,
    hospital VARCHAR(255),
    diagnosis TEXT,
    rejection_reason TEXT,
    matched_rule_id VARCHAR(50),
    secondary_rule_ids TEXT[], -- array of rule ids flagged as secondary issues
    answers JSONB,             -- the yes/no & numeric answers given for scoring
    score INTEGER,
    letter_text TEXT,          -- last generated letter, if any

    -- tracking fields
    stage VARCHAR(30) DEFAULT 'drafted', -- drafted | gro_sent | irdai_filed | ombudsman_filed | resolved_won | resolved_lost | abandoned
    status VARCHAR(20) DEFAULT 'open',   -- open | resolved | abandoned
    gro_sent_date DATE,
    gro_followup_due DATE,     -- auto: gro_sent_date + 15 days
    irdai_filed_date DATE,
    irdai_followup_due DATE,   -- auto: irdai_filed_date + 15 days (rough heuristic)
    ombudsman_filed_date DATE,
    resolved_date DATE,
    resolution_amount NUMERIC, -- amount actually recovered, if resolved_won
    notes TEXT,                -- free-text case notes the user adds over time

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Life/death claim cases, kept separate from health claims since the escalation
-- ladder, stages, and tone are distinct (grieving family, legal heir docs, etc.)
CREATE TABLE IF NOT EXISTS life_claim_cases (
    id SERIAL PRIMARY KEY,
    case_ref VARCHAR(20) UNIQUE NOT NULL, -- e.g. IL-2026-4821
    user_id VARCHAR(36) NOT NULL,
    insurer VARCHAR(255),
    policy_name VARCHAR(255),
    deceased_name VARCHAR(255),
    date_of_death DATE,
    rejection_reason TEXT,
    matched_rule_id VARCHAR(50),
    secondary_rule_ids TEXT[],
    answers JSONB,
    score INTEGER,
    letter_text TEXT,
    stage VARCHAR(30) DEFAULT 'drafted',
    status VARCHAR(20) DEFAULT 'open',
    gro_sent_date DATE,
    gro_followup_due DATE,
    irdai_filed_date DATE,
    irdai_followup_due DATE,
    ombudsman_filed_date DATE,
    resolved_date DATE,
    resolution_amount NUMERIC,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Timeline log: every status change / action recorded, for both case types.
-- case_type distinguishes which table case_ref belongs to.
CREATE TABLE IF NOT EXISTS case_events (
    id SERIAL PRIMARY KEY,
    case_ref VARCHAR(20) NOT NULL,
    case_type VARCHAR(30) NOT NULL,  -- 'health' | 'life'
    user_id VARCHAR(36) NOT NULL,
    event_type VARCHAR(30) NOT NULL, -- letter_drafted | gro_sent | irdai_filed | ombudsman_filed | note_added | resolved_won | resolved_lost | abandoned
    event_note TEXT,
    event_date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_case_events_case_ref ON case_events(case_ref);
CREATE INDEX IF NOT EXISTS idx_life_claim_cases_user_id ON life_claim_cases(user_id);

-- Payment records for the paid letter-generation feature. One row per
-- Razorpay order. status moves: created -> paid (verified via signature
-- check in payments.verify_payment, called from /api/payments/verify).
CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    case_ref VARCHAR(20) NOT NULL,
    case_type VARCHAR(30) NOT NULL DEFAULT 'health',
    razorpay_order_id VARCHAR(100) UNIQUE NOT NULL,
    razorpay_payment_id VARCHAR(100),
    amount_paise INTEGER NOT NULL, -- amount in paise (₹99 = 9900)
    status VARCHAR(20) DEFAULT 'created', -- created | paid | failed
    created_at TIMESTAMP DEFAULT NOW(),
    verified_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_payments_case_ref ON payments(case_ref);
CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);

CREATE TABLE IF NOT EXISTS disclosure_summaries (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    insurer VARCHAR(255),
    profile_name VARCHAR(255),
    checked_items JSONB, -- { itemId: true, ... }
    notes JSONB,         -- { itemId: "note text", ... }
    summary_text TEXT,   -- last generated summary text
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Age-based policy recommendations (paid, ₹99, Claude-generated). Not tied
-- to a claim case -- this is a forward-looking "what should I buy" request,
-- so it gets its own ref/table, but uses the same payments table for the
-- Razorpay flow (case_type = 'policy_recommendation').
CREATE TABLE IF NOT EXISTS policy_recommendations (
    id SERIAL PRIMARY KEY,
    recommendation_ref VARCHAR(20) UNIQUE NOT NULL, -- e.g. IR-2026-4821
    user_id VARCHAR(36) NOT NULL,
    inputs JSONB,            -- age, dependents, hasExistingConditions, monthlyBudget, city
    recommendation_text TEXT, -- last generated recommendation, if any
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_claim_cases_user_id ON claim_cases(user_id);
CREATE INDEX IF NOT EXISTS idx_disclosure_summaries_user_id ON disclosure_summaries(user_id);
CREATE INDEX IF NOT EXISTS idx_policy_recommendations_user_id ON policy_recommendations(user_id);

-- Free-text feedback from users, checked manually for now (no admin UI yet).
CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    message TEXT NOT NULL,
    page_context VARCHAR(50), -- which tab the person was on when they sent it, e.g. 'analyzer', 'life', 'checklist'
    created_at TIMESTAMP DEFAULT NOW()
);
