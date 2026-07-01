DROP TABLE IF EXISTS users CASCADE;
CREATE TABLE users (
  id                    SERIAL PRIMARY KEY,
  email                 VARCHAR(120) NOT NULL UNIQUE,
  full_name             VARCHAR(100) NULL,
  google_id             VARCHAR(50)  NULL,
  google_email          VARCHAR(120) NULL,
  google_refresh_token  VARCHAR(255) NULL,
  otp_code              VARCHAR(6)   NULL,
  otp_expiry            TIMESTAMP    NULL,
  created_at            TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  avatar_url            VARCHAR(512) NULL,
  notifications_enabled BOOLEAN      DEFAULT TRUE
);
