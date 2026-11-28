BEGIN;

CREATE TABLE public.semester_plans (
    user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
    id uuid,
    revision uuid NOT NULL DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    term_id bigint CHECK (term_id > 0),
    term_name text,
    generated_summary jsonb CHECK (jsonb_typeof(generated_summary) = 'object'),
    PRIMARY KEY (user_id, id)
);

-- Keep offering and course snapshots so past plans survive catalog updates.
CREATE TABLE public.semester_plan_courses (
    user_id uuid,
    plan_id uuid,
    position integer CHECK (position >= 0),
    course jsonb NOT NULL CHECK (jsonb_typeof(course) = 'object'),
    PRIMARY KEY (user_id, plan_id, position),
    FOREIGN KEY (user_id, plan_id) REFERENCES public.semester_plans(user_id, id) ON DELETE CASCADE
);

CREATE TABLE public.student_generator_preferences (
    user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    term_id bigint CHECK (term_id > 0),
    target_credits numeric NOT NULL CHECK (target_credits BETWEEN 1 AND 21)
);

ALTER TABLE public.semester_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.semester_plan_courses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.student_generator_preferences ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.semester_plans, public.semester_plan_courses, public.student_generator_preferences
    FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.semester_plans, public.semester_plan_courses, public.student_generator_preferences
    TO authenticated;

CREATE POLICY read_own_plan ON public.semester_plans
    FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY read_own_plan_courses ON public.semester_plan_courses
    FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY read_own_generator_preferences ON public.student_generator_preferences
    FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE FUNCTION public.read_student_planner() RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = ''
AS $$
    SELECT jsonb_build_object(
        'semesters', coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'id', p.id, 'revision', p.revision, 'term_id', p.term_id, 'term_name', p.term_name,
                'generated_summary', p.generated_summary, 'courses', coalesce((
                    SELECT jsonb_agg(c.course ORDER BY c.position)
                    FROM public.semester_plan_courses c WHERE (c.user_id, c.plan_id) = (p.user_id, p.id)
                ), '[]'::jsonb)
            ) ORDER BY p.created_at, p.id)
            FROM public.semester_plans p WHERE p.user_id = (SELECT auth.uid())
        ), '[]'::jsonb),
        'preferences', (
            SELECT jsonb_build_object('term_id', term_id, 'target_credits', target_credits)
            FROM public.student_generator_preferences WHERE user_id = (SELECT auth.uid())
        )
    );
$$;

CREATE FUNCTION public.save_student_semester(
    plan_id uuid, expected_revision uuid, selected_term bigint, term_label text,
    summary jsonb, plan_courses jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
DECLARE
    student uuid := auth.uid();
    saved_revision uuid;
BEGIN
    IF student IS NULL THEN
        RAISE EXCEPTION 'Authentication required' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(plan_courses) IS DISTINCT FROM 'array' OR jsonb_array_length(plan_courses) > 100 THEN
        RAISE EXCEPTION 'Invalid courses' USING ERRCODE = '22023';
    END IF;
    IF expected_revision IS NULL THEN
        INSERT INTO public.semester_plans (user_id, id, term_id, term_name, generated_summary)
        VALUES (student, plan_id, selected_term, term_label, summary)
        ON CONFLICT (user_id, id) DO NOTHING RETURNING revision INTO saved_revision;
        IF saved_revision IS NULL THEN
            -- Retry a create after a lost response using the same plan ID and payload.
            SELECT p.revision INTO saved_revision
            FROM public.semester_plans p
            WHERE (p.user_id, p.id) = (student, plan_id)
                AND p.term_id IS NOT DISTINCT FROM selected_term
                AND p.term_name IS NOT DISTINCT FROM term_label
                AND p.generated_summary IS NOT DISTINCT FROM summary
                AND plan_courses = coalesce((
                    SELECT jsonb_agg(c.course ORDER BY c.position)
                    FROM public.semester_plan_courses c WHERE (c.user_id, c.plan_id) = (student, p.id)
                ), '[]'::jsonb);
            IF saved_revision IS NOT NULL THEN RETURN saved_revision; END IF;
        END IF;
    ELSE
        UPDATE public.semester_plans p
        SET term_id = selected_term, term_name = term_label, generated_summary = summary, revision = gen_random_uuid()
        WHERE (p.user_id, p.id, p.revision) = (student, plan_id, expected_revision)
        RETURNING revision INTO saved_revision;
    END IF;
    IF saved_revision IS NULL THEN
        RAISE EXCEPTION 'Semester changed' USING ERRCODE = 'PT409';
    END IF;
    DELETE FROM public.semester_plan_courses c WHERE (c.user_id, c.plan_id) = (student, save_student_semester.plan_id);
    INSERT INTO public.semester_plan_courses (user_id, plan_id, position, course)
    SELECT student, plan_id, (position - 1)::integer, course
    FROM jsonb_array_elements(plan_courses) WITH ORDINALITY AS entries(course, position);
    RETURN saved_revision;
END;
$$;

CREATE FUNCTION public.delete_student_semester(plan_id uuid, expected_revision uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
BEGIN
    DELETE FROM public.semester_plans p
    WHERE (p.user_id, p.id, p.revision) = (auth.uid(), plan_id, expected_revision);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Semester changed' USING ERRCODE = 'PT409';
    END IF;
END;
$$;

CREATE FUNCTION public.save_student_generator_preferences(selected_term bigint, credits numeric) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.student_generator_preferences (user_id, term_id, target_credits)
    VALUES (auth.uid(), selected_term, credits)
    ON CONFLICT (user_id) DO UPDATE SET term_id = excluded.term_id, target_credits = excluded.target_credits;
END;
$$;

REVOKE ALL ON FUNCTION public.read_student_planner(),
    public.save_student_semester(uuid, uuid, bigint, text, jsonb, jsonb),
    public.delete_student_semester(uuid, uuid), public.save_student_generator_preferences(bigint, numeric)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.read_student_planner(),
    public.save_student_semester(uuid, uuid, bigint, text, jsonb, jsonb),
    public.delete_student_semester(uuid, uuid), public.save_student_generator_preferences(bigint, numeric)
    TO authenticated;
COMMIT;
