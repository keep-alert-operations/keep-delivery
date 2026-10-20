-- FRESH, disposable Appchi test database only, after the complete migration Job.
-- Run as appchi_migrator. Passwords are assigned with interactive \password later.
-- Published migrations remain the authority for group privileges and RLS.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE appchi_api_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE appchi_processor_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE appchi_indexer_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE appchi_correlation_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE appchi_identity_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE appchi_operator_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE appchi_proof_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE appchi_proof_login SET default_transaction_read_only = on;
CREATE ROLE appchi_recovery_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT appchi_api TO appchi_api_login;
GRANT appchi_worker TO appchi_processor_login, appchi_indexer_login, appchi_correlation_login,
                       appchi_identity_login, appchi_operator_login, appchi_proof_login;
GRANT appchi_recovery TO appchi_recovery_login;
COMMIT;
