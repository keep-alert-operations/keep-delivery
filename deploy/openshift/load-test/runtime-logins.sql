-- FRESH, disposable Keep test database only, after the complete migration Job.
-- Run as keep_migrator. Passwords are assigned with interactive \password later.
-- Published migrations remain the authority for group privileges and RLS.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE keep_api_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE keep_processor_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE keep_indexer_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE keep_correlation_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE keep_identity_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE keep_operator_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE keep_proof_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE keep_proof_login SET default_transaction_read_only = on;
CREATE ROLE keep_recovery_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT keep_api TO keep_api_login;
GRANT keep_worker TO keep_processor_login, keep_indexer_login, keep_correlation_login,
                       keep_identity_login, keep_operator_login, keep_proof_login;
GRANT keep_recovery TO keep_recovery_login;
COMMIT;
