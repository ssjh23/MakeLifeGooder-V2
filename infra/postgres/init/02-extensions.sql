-- pg_trgm backs rung three of the classification cascade (ADR-006).
-- citext backs users.email, so casing cannot create duplicate accounts.
--
-- Both must exist on the managed instance too. On RDS and Lightsail these are
-- available to the rds_superuser role; confirm before the first deploy rather
-- than during it.

\connect ledger

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;

-- The application role may use the schema but may not create in it. Table
-- level grants are issued by the migration, alongside the policies, so that
-- privileges and RLS are described in one place and travel together.
GRANT USAGE ON SCHEMA public TO ledger_app;

\connect ledger_test

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;

GRANT USAGE ON SCHEMA public TO ledger_app;
