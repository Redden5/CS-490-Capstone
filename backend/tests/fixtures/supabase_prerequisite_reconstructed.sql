-- MANUAL reconstruction, not pg_dump. Evidence SHA256: c0e1ce06a39521236528820d5ecf084fcfffdc485e1e2549b9b90985cbc6dbc5
-- Staging reference only; NOT an executable production migration.
-- Table declaration order would need to satisfy referenced objects.
CREATE TABLE public.department (
  department_id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  department_code varchar NOT NULL UNIQUE,
  department_name varchar NOT NULL,
  active_status boolean NOT NULL DEFAULT true,
  CONSTRAINT department_pkey PRIMARY KEY (department_id)
);
CREATE TABLE public.catalogyear (
  catalog_year_id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  catalog_label varchar NOT NULL,
  start_year smallint NOT NULL,
  end_year smallint NOT NULL,
  active_status boolean NOT NULL DEFAULT true,
  CONSTRAINT catalogyear_pkey PRIMARY KEY (catalog_year_id)
);
CREATE TABLE public.course (
  course_id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  course_code varchar NOT NULL,
  subject_code varchar NOT NULL,
  course_number varchar NOT NULL,
  course_title varchar NOT NULL,
  description text,
  min_credit_hours numeric NOT NULL,
  max_credit_hours numeric NOT NULL,
  department_id bigint,
  active_status boolean NOT NULL DEFAULT true,
  source_url text,
  CONSTRAINT course_pkey PRIMARY KEY (course_id)
);
CREATE TABLE public.courserule (
  rule_id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  course_id bigint NOT NULL,
  rule_type varchar NOT NULL,
  raw_text text,
  machine_checkable boolean NOT NULL DEFAULT true,
  CONSTRAINT courserule_pkey PRIMARY KEY (rule_id)
);
CREATE TABLE public.ruleclause (
  clause_id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  rule_id bigint NOT NULL,
  clause_number integer NOT NULL,
  condition_text text,
  CONSTRAINT ruleclause_pkey PRIMARY KEY (clause_id)
);
CREATE TABLE public.rulecourse (
  rule_course_id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  clause_id bigint NOT NULL,
  required_course_id bigint NOT NULL,
  minimum_grade varchar,
  concurrent_allowed boolean NOT NULL DEFAULT false,
  CONSTRAINT rulecourse_pkey PRIMARY KEY (rule_course_id)
);
