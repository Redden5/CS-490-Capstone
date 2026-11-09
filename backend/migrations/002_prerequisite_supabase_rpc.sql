-- OPTIONAL ADDITIVE MIGRATION. Requires the ALREADY APPLIED migration 001.
-- Does not recreate/alter tables, identities, RLS policies or existing data.
-- Review and deploy manually as postgres; never from the Python seeder.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '120s';

-- Fail before creating an interface if migration 001 is absent/incompatible.
SELECT catalog_year_id,source_url,parse_status,review_flags,source_metadata,content_sha256
FROM public.courserule WHERE false;

CREATE FUNCTION public.prerequisite_snapshot_v1()
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog
SET row_security = off
SET statement_timeout = '120s'
AS $$
DECLARE result jsonb;
BEGIN
    -- Do not trust a caller-supplied JWT claim. PostgREST must assume this role.
    IF current_user <> 'service_role' OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=current_user AND rolbypassrls
    ) THEN
        RAISE EXCEPTION 'Service-role BYPASSRLS access required' USING ERRCODE='42501';
    END IF;
    -- One statement/MVCC snapshot, one scalar JSON result (no API row cap).
    WITH atoms AS (
        SELECT clause_id, jsonb_agg(jsonb_build_object(
            'required_course_id',required_course_id,'minimum_grade',minimum_grade,
            'concurrent_allowed',concurrent_allowed) ORDER BY rule_course_id) AS items
        FROM public.rulecourse GROUP BY clause_id
    ), clauses AS (
        SELECT c.rule_id, jsonb_agg(COALESCE(a.items,'[]'::jsonb) ORDER BY c.clause_number) AS items,
            bool_or(c.condition_text IS NOT NULL) AS has_condition,
            jsonb_agg(c.clause_number ORDER BY c.clause_number) AS numbers,
            min(c.clause_number)=1 AND max(c.clause_number)=count(*) AS consecutive
        FROM public.ruleclause c LEFT JOIN atoms a USING(clause_id) GROUP BY c.rule_id
    ), rules AS (
        SELECT r.rule_id, jsonb_build_object(
            'course_id',r.course_id,'rule_type',r.rule_type,'catalog_year_id',r.catalog_year_id,
            'raw_text',r.raw_text,'source_url',r.source_url,'machine_checkable',r.machine_checkable,
            'parse_status',r.parse_status,'review_flags',r.review_flags,'source_metadata',r.source_metadata,
            'content_sha256',r.content_sha256,'clauses',COALESCE(c.items,'[]'::jsonb))
            || CASE WHEN c.has_condition THEN '{"unexpected_condition_text":true}'::jsonb ELSE '{}'::jsonb END
            || CASE WHEN NOT c.consecutive THEN jsonb_build_object('unexpected_clause_numbers',c.numbers) ELSE '{}'::jsonb END AS item
        FROM public.courserule r LEFT JOIN clauses c USING(rule_id)
    )
    SELECT jsonb_build_object(
        'courses',(SELECT COALESCE(jsonb_agg(jsonb_build_object('course_id',course_id,'course_code',course_code) ORDER BY course_id),'[]'::jsonb) FROM public.course),
        'catalog_years',(SELECT COALESCE(jsonb_agg(jsonb_build_object('catalog_year_id',catalog_year_id,'catalog_label',catalog_label) ORDER BY catalog_year_id),'[]'::jsonb) FROM public.catalogyear),
        'rules',(SELECT COALESCE(jsonb_agg(item ORDER BY rule_id),'[]'::jsonb) FROM rules)) INTO result;
    RETURN result;
END $$;

CREATE FUNCTION public.prerequisite_import_v1(
    p_expected_snapshot jsonb, p_rules jsonb,
    p_input_sha256 text, p_snapshot_sha256 text,
    p_approved_input_sha256 text, p_approved_snapshot_sha256 text
)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog
SET row_security = off
SET lock_timeout = '10s'
SET statement_timeout = '120s'
AS $$
DECLARE
    before_state jsonb; existing jsonb; after_rules jsonb;
    r jsonb; clause jsonb; atom jsonb; old jsonb; key text;
    rid bigint; cid bigint; number integer;
    inserted integer := 0; updated integer := 0; skipped integer := 0;
BEGIN
    IF current_user <> 'service_role' OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=current_user AND rolbypassrls
    ) THEN
        RAISE EXCEPTION 'Service-role BYPASSRLS access required' USING ERRCODE='42501';
    END IF;
    IF p_input_sha256 IS NULL OR p_snapshot_sha256 IS NULL
        OR p_input_sha256 !~ '^[0-9a-f]{64}$' OR p_snapshot_sha256 !~ '^[0-9a-f]{64}$'
        OR p_input_sha256 IS DISTINCT FROM p_approved_input_sha256
        OR p_snapshot_sha256 IS DISTINCT FROM p_approved_snapshot_sha256 THEN
        RAISE EXCEPTION 'Both reviewed approval hashes are required';
    END IF;
    IF jsonb_typeof(p_rules) IS DISTINCT FROM 'array' OR p_rules = '[]'::jsonb THEN
        RAISE EXCEPTION 'Expected nonempty rules array';
    END IF;
    -- Same rule-writer lock order as direct PostgreSQL seeding. Catalog mappings
    -- cannot change while importing. No HTTP chunk commits or autonomous commits.
    LOCK TABLE public.courserule,public.ruleclause,public.rulecourse IN SHARE ROW EXCLUSIVE MODE;
    LOCK TABLE public.course,public.catalogyear IN SHARE MODE;
    before_state := public.prerequisite_snapshot_v1();
    IF before_state IS DISTINCT FROM p_expected_snapshot THEN
        RAISE EXCEPTION 'Database changed since validation; rebuild and review the plan' USING ERRCODE='40001';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_rules) x
        GROUP BY x->>'course_id',x->>'rule_type',x->>'catalog_year_id' HAVING count(*)>1) THEN
        RAISE EXCEPTION 'Duplicate incoming rule key';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_rules) x
        GROUP BY x->>'course_id',x->>'catalog_year_id'
        HAVING count(DISTINCT x->>'machine_checkable')>1) THEN
        RAISE EXCEPTION 'Mixed resolved/unresolved rules for a course are forbidden';
    END IF;
    SELECT COALESCE(jsonb_object_agg(concat_ws('/',x->>'course_id',x->>'rule_type',COALESCE(x->>'catalog_year_id','legacy')),x),'{}'::jsonb)
        INTO existing FROM jsonb_array_elements(before_state->'rules') x;
    FOR r IN SELECT value FROM jsonb_array_elements(p_rules) LOOP
        -- Structural fail-closed defense. Academic parsing/provenance remains the
        -- reviewed Python CLI's responsibility; service credentials are trusted.
        IF jsonb_typeof(r->'machine_checkable') IS DISTINCT FROM 'boolean'
            OR jsonb_typeof(r->'clauses') IS DISTINCT FROM 'array'
            OR jsonb_typeof(r->'review_flags') IS DISTINCT FROM 'array'
            OR jsonb_typeof(r->'source_metadata') IS DISTINCT FROM 'object'
            OR jsonb_typeof(r->'source_metadata'->'course') IS DISTINCT FROM 'object'
            OR r->>'raw_text' IS NULL OR r->>'source_url' IS NULL
            OR COALESCE(r->>'content_sha256','') !~ '^[0-9a-f]{64}$'
            OR COALESCE(r->>'rule_type','') NOT IN ('PREREQUISITE','COREQUISITE') THEN
            RAISE EXCEPTION 'Invalid rule structure/provenance';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.course c WHERE c.course_id=(r->>'course_id')::bigint
            AND c.course_code=r->'source_metadata'->'course'->>'course_code')
            OR NOT EXISTS (SELECT 1 FROM public.catalogyear y WHERE y.catalog_year_id=(r->>'catalog_year_id')::bigint
            AND y.catalog_label=r->'source_metadata'->'course'->>'catalog_year') THEN
            RAISE EXCEPTION 'Target or catalog year does not match preserved source';
        END IF;
        IF (r->>'machine_checkable')::boolean THEN
            IF r->>'parse_status' IS DISTINCT FROM 'READY_FOR_SEED_REVIEW' OR r->'review_flags' <> '[]'::jsonb
                OR r->'clauses' = '[]'::jsonb
                OR r->'source_metadata'->'course'->'complete_enrollment_conditions_machine_checkable' IS DISTINCT FROM 'true'::jsonb
                OR r->'source_metadata'->'missing_database_references' IS DISTINCT FROM '[]'::jsonb THEN
                RAISE EXCEPTION 'Incomplete rules cannot be machine-checkable';
            END IF;
        ELSIF r->>'parse_status' IS DISTINCT FROM 'NEEDS_REVIEW' OR r->'clauses' <> '[]'::jsonb THEN
            RAISE EXCEPTION 'Unresolved rules must retain review status and no children';
        END IF;
        key := concat_ws('/',r->>'course_id',r->>'rule_type',r->>'catalog_year_id');
        old := existing->key;
        IF old = r THEN
            skipped := skipped+1;
            CONTINUE;
        END IF;
        IF old IS NULL THEN inserted := inserted+1; ELSE updated := updated+1; END IF;
        INSERT INTO public.courserule(course_id,rule_type,catalog_year_id,raw_text,source_url,
            machine_checkable,parse_status,review_flags,source_metadata,content_sha256)
        VALUES ((r->>'course_id')::bigint,r->>'rule_type',(r->>'catalog_year_id')::bigint,
            r->>'raw_text',r->>'source_url',(r->>'machine_checkable')::boolean,r->>'parse_status',
            r->'review_flags',r->'source_metadata',r->>'content_sha256')
        ON CONFLICT(course_id,rule_type,catalog_year_id) DO UPDATE SET
            raw_text=excluded.raw_text,source_url=excluded.source_url,machine_checkable=excluded.machine_checkable,
            parse_status=excluded.parse_status,review_flags=excluded.review_flags,
            source_metadata=excluded.source_metadata,content_sha256=excluded.content_sha256
        RETURNING rule_id INTO rid;
        DELETE FROM public.rulecourse WHERE clause_id IN (SELECT clause_id FROM public.ruleclause WHERE rule_id=rid);
        DELETE FROM public.ruleclause WHERE rule_id=rid;
        number := 0;
        FOR clause IN SELECT value FROM jsonb_array_elements(r->'clauses') LOOP
            IF jsonb_typeof(clause) IS DISTINCT FROM 'array' OR clause='[]'::jsonb THEN
                RAISE EXCEPTION 'Empty or invalid DNF conjunction';
            END IF;
            number := number+1;
            INSERT INTO public.ruleclause(rule_id,clause_number,condition_text) VALUES(rid,number,NULL) RETURNING clause_id INTO cid;
            FOR atom IN SELECT value FROM jsonb_array_elements(clause) LOOP
                IF jsonb_typeof(atom->'concurrent_allowed') IS DISTINCT FROM 'boolean'
                    OR COALESCE(jsonb_typeof(atom->'minimum_grade'),'missing') NOT IN ('string','null') THEN
                    RAISE EXCEPTION 'Invalid grade/concurrency atom';
                END IF;
                INSERT INTO public.rulecourse(clause_id,required_course_id,minimum_grade,concurrent_allowed)
                VALUES(cid,(atom->>'required_course_id')::bigint,atom->>'minimum_grade',(atom->>'concurrent_allowed')::boolean);
            END LOOP;
        END LOOP;
    END LOOP;
    -- Verify actual parent and child contents before commit, not just hashes.
    SELECT COALESCE(jsonb_object_agg(concat_ws('/',x->>'course_id',x->>'rule_type',COALESCE(x->>'catalog_year_id','legacy')),x),'{}'::jsonb)
        INTO after_rules FROM jsonb_array_elements(public.prerequisite_snapshot_v1()->'rules') x;
    FOR r IN SELECT value FROM jsonb_array_elements(p_rules) LOOP
        key := concat_ws('/',r->>'course_id',r->>'rule_type',r->>'catalog_year_id');
        IF after_rules->key IS DISTINCT FROM r THEN
            RAISE EXCEPTION 'Stored payload verification failed; import rolled back';
        END IF;
    END LOOP;
    RETURN jsonb_build_object('insert',inserted,'update',updated,'skip',skipped);
END $$;

-- Default PUBLIC EXECUTE must be revoked in the same transaction as creation.
REVOKE ALL ON FUNCTION public.prerequisite_snapshot_v1() FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.prerequisite_import_v1(jsonb,jsonb,text,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.prerequisite_snapshot_v1() TO service_role;
GRANT EXECUTE ON FUNCTION public.prerequisite_import_v1(jsonb,jsonb,text,text,text,text) TO service_role;
-- Existing service_role table/sequence ACLs and BYPASSRLS are prerequisites;
-- this migration never broadens table privileges or adds permissive RLS policies.
NOTIFY pgrst, 'reload schema';
COMMIT;
