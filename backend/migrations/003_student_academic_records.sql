BEGIN;

CREATE TABLE public.student_profiles (
    user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    program_key text NOT NULL CHECK (btrim(program_key) <> ''),
    catalog_year text NOT NULL CHECK (catalog_year ~ '^[0-9]{4}-[0-9]{4}$'),
    revision uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE public.completed_course_attempts (
    user_id uuid REFERENCES public.student_profiles(user_id) ON DELETE CASCADE,
    id uuid,
    position integer NOT NULL CHECK (position >= 0),
    course_code text NOT NULL CHECK (length(btrim(course_code)) BETWEEN 1 AND 40),
    grade text CHECK (length(grade) BETWEEN 1 AND 20 AND upper(btrim(grade)) <> 'F'),
    reported_credits numeric CHECK (reported_credits > 0 AND reported_credits < 'Infinity'::numeric),
    equivalency_verified boolean NOT NULL,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, position)
);

ALTER TABLE public.student_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.completed_course_attempts ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.student_profiles, public.completed_course_attempts FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.student_profiles, public.completed_course_attempts TO authenticated;

CREATE POLICY read_own_profile ON public.student_profiles
    FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY read_own_attempts ON public.completed_course_attempts
    FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE FUNCTION public.read_student_academics()
RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = ''
AS $$
    SELECT jsonb_build_object(
        'revision', p.revision,
        'program_key', p.program_key,
        'catalog_year', p.catalog_year,
        'attempts', coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'id', a.id, 'course_code', a.course_code, 'grade', a.grade,
                'reported_credits', a.reported_credits,
                'equivalency_verified', a.equivalency_verified
            ) ORDER BY a.position)
            FROM public.completed_course_attempts AS a WHERE a.user_id = p.user_id
        ), '[]'::jsonb)
    )
    FROM public.student_profiles AS p WHERE p.user_id = (SELECT auth.uid());
$$;

-- Direct writes are revoked so attempts and the profile revision always change together.
CREATE FUNCTION public.save_student_academics(
    expected_revision uuid, selected_program text, selected_year text, attempts jsonb
)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
DECLARE
    student_id uuid := auth.uid();
    saved_revision uuid;
BEGIN
    IF student_id IS NULL THEN
        RAISE EXCEPTION 'Authentication required' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(attempts) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid coursework' USING ERRCODE = '22023';
    END IF;

    IF expected_revision IS NULL THEN
        INSERT INTO public.student_profiles (user_id, program_key, catalog_year)
        VALUES (student_id, selected_program, selected_year)
        ON CONFLICT (user_id) DO NOTHING
        RETURNING revision INTO saved_revision;
    ELSE
        UPDATE public.student_profiles
        SET program_key = selected_program, catalog_year = selected_year, revision = gen_random_uuid()
        WHERE user_id = student_id AND revision = expected_revision
        RETURNING revision INTO saved_revision;
    END IF;

    IF saved_revision IS NULL THEN
        RAISE EXCEPTION 'Academic record changed' USING ERRCODE = 'PT409';
    END IF;

    DELETE FROM public.completed_course_attempts WHERE user_id = student_id;
    INSERT INTO public.completed_course_attempts
        (user_id, id, position, course_code, grade, reported_credits, equivalency_verified)
    SELECT student_id, (entry->>'id')::uuid, (position - 1)::integer, entry->>'course_code',
           entry->>'grade', (entry->>'reported_credits')::numeric,
           (entry->>'equivalency_verified')::boolean
    FROM jsonb_array_elements(attempts) WITH ORDINALITY AS entries(entry, position);

    RETURN saved_revision;
END;
$$;

REVOKE ALL ON FUNCTION public.read_student_academics() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.save_student_academics(uuid, text, text, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.read_student_academics() TO authenticated;
GRANT EXECUTE ON FUNCTION public.save_student_academics(uuid, text, text, jsonb) TO authenticated;

COMMIT;
