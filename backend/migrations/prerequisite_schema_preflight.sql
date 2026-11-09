-- READ ONLY. For a separately authorized operator; NOT executed during review.
-- Run before migration using the intended deployment role; retain results securely.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL row_security = off;
SELECT current_database(), current_user, current_setting('server_version') AS server_version;
SELECT table_name,column_name,data_type,is_nullable,column_default,is_identity,identity_generation
FROM information_schema.columns WHERE table_schema='public'
 AND table_name IN ('course','catalogyear','courserule','ruleclause','rulecourse')
ORDER BY table_name,ordinal_position;
SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,
 pg_catalog.pg_get_userbyid(c.relowner) AS owner
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname IN ('course','catalogyear','courserule','ruleclause','rulecourse');
SELECT conname,conrelid::regclass AS source_table,confrelid::regclass AS referenced_table,
 contype,convalidated,pg_catalog.pg_get_constraintdef(oid) AS definition
FROM pg_catalog.pg_constraint WHERE conrelid IN
 ('public.course'::regclass,'public.catalogyear'::regclass,'public.courserule'::regclass,
  'public.ruleclause'::regclass,'public.rulecourse'::regclass)
 OR confrelid IN ('public.courserule'::regclass,'public.ruleclause'::regclass,'public.rulecourse'::regclass);
SELECT tablename,indexname,indexdef FROM pg_catalog.pg_indexes WHERE schemaname='public'
 AND tablename IN ('course','catalogyear','courserule','ruleclause','rulecourse');
SELECT schemaname,tablename,policyname,roles,cmd,qual,with_check FROM pg_catalog.pg_policies
WHERE schemaname='public' AND tablename IN ('course','catalogyear','courserule','ruleclause','rulecourse');
SELECT c.relname,t.tgname,pg_catalog.pg_get_triggerdef(t.oid) AS definition
FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
WHERE NOT t.tgisinternal AND t.tgrelid IN
 ('public.course'::regclass,'public.catalogyear'::regclass,'public.courserule'::regclass,
  'public.ruleclause'::regclass,'public.rulecourse'::regclass);
SELECT table_name,grantee,privilege_type FROM information_schema.table_privileges
WHERE table_schema='public' AND table_name IN ('course','catalogyear','courserule','ruleclause','rulecourse');
SELECT rolname,rolsuper,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user;
SELECT catalog_year_id,catalog_label,start_year,end_year,active_status FROM public.catalogyear;
SELECT course_code,count(*) FROM public.course GROUP BY course_code HAVING count(*)>1;
SELECT course_id,rule_type,count(*) FROM public.courserule GROUP BY course_id,rule_type HAVING count(*)>1;
SELECT 'courserule' AS table_name,count(*) AS rows FROM public.courserule
UNION ALL SELECT 'ruleclause',count(*) FROM public.ruleclause
UNION ALL SELECT 'rulecourse',count(*) FROM public.rulecourse;
SELECT c.relname AS table_name,a.attname AS pk_column,
 pg_catalog.pg_get_serial_sequence('public.' || c.relname,a.attname) AS owned_sequence
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
WHERE n.nspname='public' AND (c.relname,a.attname) IN
 (('courserule','rule_id'),('ruleclause','clause_id'),('rulecourse','rule_course_id'));
-- Inspect returned sequence defaults/ownership/current positions and intended
-- role privileges without calling nextval/setval. Custom default functions and
-- non-owned sequences require separate review. RLS can affect the data queries
-- above: verify full visibility before treating counts as authoritative.
ROLLBACK;
