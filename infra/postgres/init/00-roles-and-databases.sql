\set ON_ERROR_STOP on

CREATE ROLE relaypay_migrator LOGIN PASSWORD 'relaypay_migrator_dev';
CREATE ROLE relaypay_app LOGIN PASSWORD 'relaypay_app_dev';
CREATE ROLE provider_migrator LOGIN PASSWORD 'provider_migrator_dev';
CREATE ROLE provider_app LOGIN PASSWORD 'provider_app_dev';
CREATE ROLE receiver_app LOGIN PASSWORD 'receiver_app_dev';

CREATE DATABASE relaypay OWNER relaypay_migrator;
CREATE DATABASE provider OWNER provider_migrator;

REVOKE CONNECT ON DATABASE relaypay FROM PUBLIC;
REVOKE CONNECT ON DATABASE provider FROM PUBLIC;
GRANT CONNECT ON DATABASE relaypay TO relaypay_migrator, relaypay_app, receiver_app;
GRANT CONNECT ON DATABASE provider TO provider_migrator, provider_app;

\connect relaypay

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO relaypay_app;
ALTER DEFAULT PRIVILEGES FOR ROLE relaypay_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO relaypay_app;
ALTER DEFAULT PRIVILEGES FOR ROLE relaypay_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO relaypay_app;

CREATE SCHEMA receiver AUTHORIZATION receiver_app;
REVOKE ALL ON SCHEMA receiver FROM PUBLIC;
REVOKE ALL ON SCHEMA receiver FROM relaypay_migrator, relaypay_app;

\connect provider

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO provider_app;
ALTER DEFAULT PRIVILEGES FOR ROLE provider_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO provider_app;
ALTER DEFAULT PRIVILEGES FOR ROLE provider_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO provider_app;

