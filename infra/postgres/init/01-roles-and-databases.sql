-- Runs once, on first boot of the postgres volume, as the superuser.
--
-- Two roles, and the separation is the whole point. PostgreSQL does not apply
-- row level security to a table's owner. An application that connects as the
-- role which ran the migrations therefore has no tenant isolation at all, and
-- every isolation test passes for the wrong reason. So:
--
--   ledger_migrator  owns the schema. Alembic uses it. The application never
--                    does.
--   ledger_app       owns nothing. It is the only role the API and worker use,
--                    which means RLS is enforced against it exactly as it will
--                    be in production.
--
-- The managed-database equivalent of this file is the checklist item "create
-- the two roles" in the cloud section of the plan.

CREATE ROLE ledger_migrator WITH LOGIN PASSWORD 'ledger_migrator_pw';
CREATE ROLE ledger_app WITH LOGIN PASSWORD 'ledger_app_pw';

CREATE DATABASE ledger OWNER ledger_migrator;
CREATE DATABASE ledger_test OWNER ledger_migrator;

REVOKE ALL ON DATABASE ledger FROM PUBLIC;
REVOKE ALL ON DATABASE ledger_test FROM PUBLIC;

GRANT CONNECT ON DATABASE ledger TO ledger_app;
GRANT CONNECT ON DATABASE ledger_test TO ledger_app;
