-- Manual migrations: add columns that may be missing on staging.
-- Safe to re-run (uses IF NOT EXISTS / DO blocks).

-- ═══════════════════════════════════════════════════════
-- ENUM TYPES (add missing values)
-- ═══════════════════════════════════════════════════════

DO $$ BEGIN ALTER TYPE trip_status ADD VALUE IF NOT EXISTS 'completed'; EXCEPTION WHEN OTHERS THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE trip_status ADD VALUE IF NOT EXISTS 'en_route'; EXCEPTION WHEN OTHERS THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE trip_status ADD VALUE IF NOT EXISTS 'boarding'; EXCEPTION WHEN OTHERS THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE trip_status ADD VALUE IF NOT EXISTS 'delayed'; EXCEPTION WHEN OTHERS THEN NULL; END $$;

DO $$ BEGIN ALTER TYPE booking_status ADD VALUE IF NOT EXISTS 'expired'; EXCEPTION WHEN OTHERS THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE booking_status ADD VALUE IF NOT EXISTS 'no_show'; EXCEPTION WHEN OTHERS THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE booking_status ADD VALUE IF NOT EXISTS 'checked_in'; EXCEPTION WHEN OTHERS THEN NULL; END $$;

-- ═══════════════════════════════════════════════════════
-- USERS TABLE — new columns
-- ═══════════════════════════════════════════════════════

ALTER TABLE users ADD COLUMN IF NOT EXISTS has_logged_in BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS assigned_terminal_id UUID REFERENCES terminals(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS emergency_contact_name VARCHAR(200);
ALTER TABLE users ADD COLUMN IF NOT EXISTS emergency_contact_phone VARCHAR(20);

-- ═══════════════════════════════════════════════════════
-- BOOKINGS TABLE — new columns
-- ═══════════════════════════════════════════════════════

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booked_by_user_id UUID REFERENCES users(id);
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_method_hint VARCHAR(20);
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_deadline TIMESTAMPTZ;

-- ═══════════════════════════════════════════════════════
-- TRIPS TABLE — new columns
-- ═══════════════════════════════════════════════════════

ALTER TABLE trips ADD COLUMN IF NOT EXISTS inspection_data JSONB;

-- ═══════════════════════════════════════════════════════
-- PAYMENTS TABLE — nullable booking_id
-- ═══════════════════════════════════════════════════════

ALTER TABLE payments ALTER COLUMN booking_id DROP NOT NULL;

-- ═══════════════════════════════════════════════════════
-- NEW TABLES (create if not exists)
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS trip_incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    driver_id UUID NOT NULL REFERENCES drivers(id),
    type VARCHAR(50) NOT NULL,
    description TEXT,
    severity VARCHAR(10) DEFAULT 'low',
    reported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolution_notes TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    resource_id VARCHAR(255),
    details JSONB,
    ip_address VARCHAR(45),
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at);

-- ═══════════════════════════════════════════════════════
-- DATA CLEANUP: release seats stuck on expired/cancelled bookings
-- ═══════════════════════════════════════════════════════

UPDATE trip_seats SET status = 'available', locked_by_user_id = NULL, locked_until = NULL
WHERE id IN (
    SELECT bp.seat_id FROM booking_passengers bp
    JOIN bookings b ON bp.booking_id = b.id
    WHERE b.status IN ('expired', 'cancelled') AND bp.seat_id IS NOT NULL
) AND status != 'available';

-- Also release any orphaned locked seats whose lock has expired
UPDATE trip_seats SET status = 'available', locked_by_user_id = NULL, locked_until = NULL
WHERE status = 'locked' AND locked_until < NOW();

-- ═══════════════════════════════════════════════════════
-- DEVICE TOKENS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS device_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token VARCHAR(500) NOT NULL,
    device_type VARCHAR(20) NOT NULL DEFAULT 'android',
    app_type VARCHAR(20) NOT NULL DEFAULT 'customer',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_device_token UNIQUE (user_id, token)
);
CREATE INDEX IF NOT EXISTS idx_device_tokens_user ON device_tokens(user_id);

-- ═══════════════════════════════════════════════════════
-- BOOKINGS TABLE — reminder tracking fields
-- ═══════════════════════════════════════════════════════

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_24h_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_1h_sent BOOLEAN DEFAULT FALSE;

-- ═══════════════════════════════════════════════════════
-- Confirm success
-- ═══════════════════════════════════════════════════════

SELECT 'Manual migrations complete' AS status;
