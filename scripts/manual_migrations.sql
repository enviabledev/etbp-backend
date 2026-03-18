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
-- NOTIFICATION CAMPAIGNS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS notification_campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(100) NOT NULL,
    body TEXT NOT NULL,
    channel VARCHAR(20) NOT NULL DEFAULT 'push',
    target_type VARCHAR(50) NOT NULL,
    target_value TEXT,
    target_description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    total_recipients INTEGER DEFAULT 0,
    sent_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    scheduled_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notification_campaigns_status ON notification_campaigns(status);

-- ═══════════════════════════════════════════════════════
-- BOOKING ADDONS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS booking_addons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    addon_type VARCHAR(50) NOT NULL DEFAULT 'extra_luggage',
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price DECIMAL(12,2) NOT NULL,
    total_price DECIMAL(12,2) NOT NULL,
    currency VARCHAR(10) DEFAULT 'NGN',
    status VARCHAR(20) DEFAULT 'pending',
    payment_id UUID REFERENCES payments(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_booking_addons_booking ON booking_addons(booking_id);

-- ═══════════════════════════════════════════════════════
-- BOOKINGS TABLE — transfer and reschedule tracking
-- ═══════════════════════════════════════════════════════

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS transferred_from_user_id UUID;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS transferred_at TIMESTAMPTZ;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS rescheduled_from_trip_id UUID;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS rescheduled_at TIMESTAMPTZ;

-- ═══════════════════════════════════════════════════════
-- ROUTES TABLE — extra luggage pricing
-- ═══════════════════════════════════════════════════════

ALTER TABLE routes ADD COLUMN IF NOT EXISTS extra_luggage_price DECIMAL(12,2) DEFAULT 2000.00;

-- ═══════════════════════════════════════════════════════
-- PROMO USAGES TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS promo_usages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promo_id UUID NOT NULL REFERENCES promo_codes(id),
    user_id UUID NOT NULL REFERENCES users(id),
    booking_id UUID REFERENCES bookings(id),
    discount_applied DECIMAL(12,2) NOT NULL,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_promo_usages_promo ON promo_usages(promo_id);
CREATE INDEX IF NOT EXISTS idx_promo_usages_user ON promo_usages(user_id);

-- ═══════════════════════════════════════════════════════
-- BOOKINGS TABLE — price breakdown
-- ═══════════════════════════════════════════════════════

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS price_breakdown JSONB;

-- ═══════════════════════════════════════════════════════
-- REVIEWS TABLE (drop old if exists, create full version)
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS trip_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id UUID NOT NULL UNIQUE REFERENCES bookings(id),
    user_id UUID NOT NULL REFERENCES users(id),
    trip_id UUID NOT NULL REFERENCES trips(id),
    driver_id UUID REFERENCES drivers(id),
    overall_rating INTEGER NOT NULL CHECK (overall_rating BETWEEN 1 AND 5),
    driver_rating INTEGER CHECK (driver_rating BETWEEN 1 AND 5),
    bus_condition_rating INTEGER CHECK (bus_condition_rating BETWEEN 1 AND 5),
    punctuality_rating INTEGER CHECK (punctuality_rating BETWEEN 1 AND 5),
    comfort_rating INTEGER CHECK (comfort_rating BETWEEN 1 AND 5),
    comment TEXT,
    is_anonymous BOOLEAN DEFAULT FALSE,
    admin_response TEXT,
    admin_responded_at TIMESTAMPTZ,
    admin_responded_by UUID REFERENCES users(id),
    is_flagged BOOLEAN DEFAULT FALSE,
    is_visible BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reviews_trip ON trip_reviews(trip_id);
CREATE INDEX IF NOT EXISTS idx_reviews_driver ON trip_reviews(driver_id);
CREATE INDEX IF NOT EXISTS idx_reviews_user ON trip_reviews(user_id);
CREATE INDEX IF NOT EXISTS idx_reviews_booking ON trip_reviews(booking_id);

-- Add missing columns if table already exists
ALTER TABLE trip_reviews ADD COLUMN IF NOT EXISTS driver_id UUID REFERENCES drivers(id);
ALTER TABLE trip_reviews ADD COLUMN IF NOT EXISTS comfort_rating INTEGER;
ALTER TABLE trip_reviews ADD COLUMN IF NOT EXISTS is_anonymous BOOLEAN DEFAULT FALSE;
ALTER TABLE trip_reviews ADD COLUMN IF NOT EXISTS admin_response TEXT;
ALTER TABLE trip_reviews ADD COLUMN IF NOT EXISTS admin_responded_at TIMESTAMPTZ;
ALTER TABLE trip_reviews ADD COLUMN IF NOT EXISTS admin_responded_by UUID;
ALTER TABLE trip_reviews ADD COLUMN IF NOT EXISTS is_flagged BOOLEAN DEFAULT FALSE;
ALTER TABLE trip_reviews ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS review_prompted BOOLEAN DEFAULT FALSE;
ALTER TABLE trips ADD COLUMN IF NOT EXISTS summary_data JSONB;
ALTER TABLE trips ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

-- ═══════════════════════════════════════════════════════
-- USERS TABLE — social auth fields
-- ═══════════════════════════════════════════════════════

ALTER TABLE users ADD COLUMN IF NOT EXISTS apple_id VARCHAR(255) UNIQUE;
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_apple_id ON users(apple_id) WHERE apple_id IS NOT NULL;

-- ═══════════════════════════════════════════════════════
-- TERMINAL COORDINATES (seed existing terminals)
-- ═══════════════════════════════════════════════════════

UPDATE terminals SET latitude = 6.6018, longitude = 3.3515 WHERE name ILIKE '%Berger%' AND latitude IS NULL;
UPDATE terminals SET latitude = 6.5244, longitude = 3.3792 WHERE name ILIKE '%Jibowu%' AND latitude IS NULL;
UPDATE terminals SET latitude = 9.0579, longitude = 7.4951 WHERE (name ILIKE '%Abuja%' OR name ILIKE '%Utako%') AND latitude IS NULL;
UPDATE terminals SET latitude = 6.3350, longitude = 5.6037 WHERE name ILIKE '%Benin%' AND latitude IS NULL;
UPDATE terminals SET latitude = 4.8156, longitude = 7.0498 WHERE name ILIKE '%Port Harcourt%' AND latitude IS NULL;

-- ═══════════════════════════════════════════════════════
-- MAINTENANCE RECORDS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS maintenance_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    maintenance_type VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
    priority VARCHAR(20) NOT NULL DEFAULT 'medium',
    scheduled_date DATE NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    mileage_at_service DECIMAL(12,2),
    cost DECIMAL(12,2),
    currency VARCHAR(10) DEFAULT 'NGN',
    vendor_name VARCHAR(200),
    vendor_contact VARCHAR(100),
    parts_replaced JSONB,
    notes TEXT,
    next_service_due_date DATE,
    next_service_due_mileage DECIMAL(12,2),
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_maintenance_records_vehicle ON maintenance_records(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_maintenance_records_status ON maintenance_records(status);
CREATE INDEX IF NOT EXISTS idx_maintenance_records_scheduled ON maintenance_records(scheduled_date);

-- ═══════════════════════════════════════════════════════
-- MAINTENANCE SCHEDULES TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS maintenance_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_type_id UUID REFERENCES vehicle_types(id),
    vehicle_id UUID REFERENCES vehicles(id),
    maintenance_type VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    interval_km INTEGER,
    interval_days INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════
-- VEHICLE DOCUMENTS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS vehicle_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    document_type VARCHAR(50) NOT NULL,
    document_number VARCHAR(100),
    issued_date DATE,
    expiry_date DATE NOT NULL,
    status VARCHAR(20) DEFAULT 'valid',
    notes TEXT,
    alert_30d_sent BOOLEAN DEFAULT FALSE,
    alert_7d_sent BOOLEAN DEFAULT FALSE,
    alert_expired_sent BOOLEAN DEFAULT FALSE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vehicle_documents_vehicle ON vehicle_documents(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_vehicle_documents_expiry ON vehicle_documents(expiry_date);

-- ═══════════════════════════════════════════════════════
-- CORPORATE ACCOUNTS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS corporate_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name VARCHAR(200) NOT NULL,
    company_email VARCHAR(200) NOT NULL,
    company_phone VARCHAR(50),
    company_address TEXT,
    registration_number VARCHAR(100),
    tax_id VARCHAR(100),
    contact_person_name VARCHAR(200),
    contact_person_email VARCHAR(200),
    contact_person_phone VARCHAR(50),
    credit_limit DECIMAL(14,2) NOT NULL DEFAULT 0,
    current_balance DECIMAL(14,2) NOT NULL DEFAULT 0,
    billing_cycle VARCHAR(20) NOT NULL DEFAULT 'monthly',
    billing_day INTEGER NOT NULL DEFAULT 1,
    payment_terms_days INTEGER NOT NULL DEFAULT 30,
    discount_percentage DECIMAL(5,2) DEFAULT 0,
    rate_agreement JSONB,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    suspended_reason TEXT,
    notes TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════
-- CORPORATE EMPLOYEES TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS corporate_employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corporate_account_id UUID NOT NULL REFERENCES corporate_accounts(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id),
    employee_id VARCHAR(50),
    department VARCHAR(100),
    is_admin BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    added_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_corp_employee UNIQUE (corporate_account_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_corporate_employees_account ON corporate_employees(corporate_account_id);
CREATE INDEX IF NOT EXISTS idx_corporate_employees_user ON corporate_employees(user_id);

-- ═══════════════════════════════════════════════════════
-- INVOICES TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(50) NOT NULL UNIQUE,
    corporate_account_id UUID NOT NULL REFERENCES corporate_accounts(id),
    billing_period_start DATE NOT NULL,
    billing_period_end DATE NOT NULL,
    subtotal DECIMAL(14,2) NOT NULL DEFAULT 0,
    discount_amount DECIMAL(14,2) NOT NULL DEFAULT 0,
    tax_amount DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_amount DECIMAL(14,2) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    due_date DATE NOT NULL,
    paid_amount DECIMAL(14,2) DEFAULT 0,
    paid_at TIMESTAMPTZ,
    payment_reference VARCHAR(200),
    notes TEXT,
    line_items JSONB,
    sent_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_invoices_corporate ON invoices(corporate_account_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);

-- ═══════════════════════════════════════════════════════
-- BOOKINGS TABLE — corporate account link
-- ═══════════════════════════════════════════════════════

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS corporate_account_id UUID REFERENCES corporate_accounts(id);

-- ═══════════════════════════════════════════════════════
-- BANNERS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS banners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(200) NOT NULL,
    heading VARCHAR(200),
    body_text TEXT,
    image_url TEXT,
    background_color VARCHAR(10),
    text_color VARCHAR(10),
    cta_text VARCHAR(100),
    cta_action VARCHAR(20),
    cta_value TEXT,
    placement VARCHAR(50) NOT NULL DEFAULT 'home_hero',
    target_audience VARCHAR(50) DEFAULT 'all',
    start_date TIMESTAMPTZ NOT NULL,
    end_date TIMESTAMPTZ NOT NULL,
    priority INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    impressions INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_banners_placement ON banners(placement);

-- ═══════════════════════════════════════════════════════
-- EMAIL TEMPLATES TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS email_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    subject VARCHAR(500) NOT NULL,
    body_html TEXT NOT NULL,
    body_text TEXT,
    variables JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    last_edited_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════
-- LOST & FOUND REPORTS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS lost_found_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_number VARCHAR(50) NOT NULL UNIQUE,
    reporter_user_id UUID NOT NULL REFERENCES users(id),
    report_type VARCHAR(10) NOT NULL,
    booking_ref VARCHAR(50),
    trip_id UUID REFERENCES trips(id),
    route_id UUID REFERENCES routes(id),
    terminal_id UUID REFERENCES terminals(id),
    item_description TEXT NOT NULL,
    item_category VARCHAR(50) NOT NULL,
    color VARCHAR(50),
    distinguishing_features TEXT,
    date_lost_found DATE NOT NULL,
    location_details TEXT,
    contact_phone VARCHAR(50) NOT NULL,
    contact_email VARCHAR(200),
    status VARCHAR(20) NOT NULL DEFAULT 'reported',
    assigned_to UUID REFERENCES users(id),
    resolution_notes TEXT,
    resolved_at TIMESTAMPTZ,
    images JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lost_found_reporter ON lost_found_reports(reporter_user_id);
CREATE INDEX IF NOT EXISTS idx_lost_found_terminal ON lost_found_reports(terminal_id);
CREATE INDEX IF NOT EXISTS idx_lost_found_status ON lost_found_reports(status);

-- ═══════════════════════════════════════════════════════
-- DASHBOARD WIDGETS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dashboard_widgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    widget_type VARCHAR(50) NOT NULL,
    data_source VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    config JSONB DEFAULT '{}',
    position JSONB DEFAULT '{"x":0,"y":0,"w":6,"h":4}',
    is_visible BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_user ON dashboard_widgets(user_id);

ALTER TABLE routes ADD COLUMN IF NOT EXISTS estimated_operating_cost DECIMAL(12,2);

-- ═══════════════════════════════════════════════════════
-- ROUTE STOPS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS route_stops (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    route_id UUID NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    terminal_id UUID NOT NULL REFERENCES terminals(id),
    stop_order INTEGER NOT NULL,
    duration_from_origin_minutes INTEGER,
    price_from_origin DECIMAL(12,2),
    is_pickup_point BOOLEAN DEFAULT TRUE,
    is_dropoff_point BOOLEAN DEFAULT TRUE,
    CONSTRAINT uq_route_stop_order UNIQUE (route_id, stop_order)
);
CREATE INDEX IF NOT EXISTS idx_route_stops_route ON route_stops(route_id);

-- ═══════════════════════════════════════════════════════
-- CONVERSATIONS TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_type VARCHAR(30) NOT NULL,
    subject VARCHAR(300),
    trip_id UUID REFERENCES trips(id),
    booking_id UUID REFERENCES bookings(id),
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    assigned_to UUID REFERENCES users(id),
    participant_ids JSONB NOT NULL DEFAULT '[]',
    last_message_at TIMESTAMPTZ,
    last_message_preview VARCHAR(200),
    resolved_at TIMESTAMPTZ,
    resolved_by UUID REFERENCES users(id),
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversations_type ON conversations(conversation_type);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status);
CREATE INDEX IF NOT EXISTS idx_conversations_assigned ON conversations(assigned_to);

-- ═══════════════════════════════════════════════════════
-- MESSAGES TABLE
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id UUID NOT NULL REFERENCES users(id),
    content TEXT NOT NULL,
    message_type VARCHAR(20) NOT NULL DEFAULT 'text',
    metadata JSONB,
    is_read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

-- ═══════════════════════════════════════════════════════
-- Confirm success
-- ═══════════════════════════════════════════════════════

SELECT 'Manual migrations complete' AS status;
