#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=relaypay_migrator_password="$RELAYPAY_MIGRATOR_DB_PASSWORD" \
  --set=relaypay_app_password="$RELAYPAY_APP_DB_PASSWORD" \
  --set=provider_migrator_password="$PROVIDER_MIGRATOR_DB_PASSWORD" \
  --set=provider_app_password="$PROVIDER_APP_DB_PASSWORD" \
  --set=receiver_app_password="$RECEIVER_APP_DB_PASSWORD" <<'SQL'
CREATE ROLE relaypay_migrator LOGIN PASSWORD :'relaypay_migrator_password';
CREATE ROLE relaypay_app LOGIN PASSWORD :'relaypay_app_password';
CREATE ROLE provider_migrator LOGIN PASSWORD :'provider_migrator_password';
CREATE ROLE provider_app LOGIN PASSWORD :'provider_app_password';
CREATE ROLE receiver_app LOGIN PASSWORD :'receiver_app_password';

CREATE DATABASE relaypay OWNER relaypay_migrator;
CREATE DATABASE provider OWNER provider_migrator;

REVOKE CONNECT ON DATABASE relaypay FROM PUBLIC;
REVOKE CONNECT ON DATABASE provider FROM PUBLIC;
GRANT CONNECT ON DATABASE relaypay TO relaypay_migrator, relaypay_app, receiver_app;
GRANT CONNECT ON DATABASE provider TO provider_migrator, provider_app;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname relaypay <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO relaypay_app;
ALTER DEFAULT PRIVILEGES FOR ROLE relaypay_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO relaypay_app;
ALTER DEFAULT PRIVILEGES FOR ROLE relaypay_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO relaypay_app;

CREATE SCHEMA receiver AUTHORIZATION receiver_app;
REVOKE ALL ON SCHEMA receiver FROM PUBLIC;
REVOKE ALL ON SCHEMA receiver FROM relaypay_migrator, relaypay_app;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname provider <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO provider_app;
ALTER DEFAULT PRIVILEGES FOR ROLE provider_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO provider_app;
ALTER DEFAULT PRIVILEGES FOR ROLE provider_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO provider_app;
SQL
