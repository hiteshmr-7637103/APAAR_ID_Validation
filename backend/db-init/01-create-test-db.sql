-- Runs once, on first start of an empty Postgres volume.
-- Separate database for the pytest suite (wiped and rebuilt on every test run).
CREATE DATABASE apaar_test;
