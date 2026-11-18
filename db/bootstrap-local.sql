-- Local schema smoke tests only. Supabase provides these objects in hosted projects.
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE SCHEMA auth;
CREATE TABLE auth.users (
    id uuid PRIMARY KEY
);
