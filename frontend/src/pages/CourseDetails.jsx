import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { supabase } from "../lib/supabaseClient.js";
import CourseDetailCard from "../components/CourseDetailCard.jsx";
import CourseRequirements from "../components/CourseRequirements.jsx";
import "../styles/Courses.css";
import "../styles/CourseDetails.css";

const DAYS = [null, "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function meetingTime(value) {
  return value ? value.slice(0, 5) : "TBA";
}

function Section({ section }) {
  return (
    <article className="course-section">
      <h3>{section.term?.term_name || "Term not listed"} · Section {section.section_number}</h3>
      <dl className="course-facts">
        <div><dt>Status</dt><dd>{section.section_status}</dd></div>
        <div><dt>Instructor</dt><dd>{section.instructor || "TBA"}</dd></div>
        <div><dt>Campus</dt><dd>{section.campus || "Not listed"}</dd></div>
        <div><dt>Delivery</dt><dd>{section.delivery_mode || "Not listed"}</dd></div>
        <div><dt>CRN</dt><dd>{section.crn || "Not listed"}</dd></div>
        <div><dt>Enrollment</dt><dd>{section.enrolled ?? "Unknown"} / {section.capacity ?? "Unknown"}</dd></div>
      </dl>
      {section.sectionmeeting?.length ? (
        <ul className="course-meetings">
          {section.sectionmeeting.map((meeting) => (
            <li key={meeting.meeting_id}>
              {DAYS[meeting.day_of_week] || "Day TBA"} · {meetingTime(meeting.start_time)}–{meetingTime(meeting.end_time)}
              {" · "}{[meeting.building, meeting.room].filter(Boolean).join(" ") || "Location TBA"}
              {meeting.meeting_type && ` (${meeting.meeting_type})`}
            </li>
          ))}
        </ul>
      ) : <p>Meeting times have not been listed.</p>}
    </article>
  );
}

function CourseDetails() {
  const { courseId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const heading = useRef(null);
  const [result, setResult] = useState({ loading: true });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      if (!/^[1-9]\d*$/.test(courseId)) {
        setResult({ loading: false, course: null });
        return;
      }
      setResult({ loading: true });
      try {
        const courseResult = await supabase.from("course")
          .select("course_id, course_code, course_title, description, min_credit_hours, max_credit_hours, active_status, department:department_id(department_name)")
          .eq("course_id", courseId).abortSignal(controller.signal).maybeSingle();
        if (controller.signal.aborted) return;
        if (courseResult.error) throw courseResult.error;
        if (!courseResult.data) {
          setResult({ loading: false, course: null });
          return;
        }

        const [rules, sections] = await Promise.all([
          supabase.from("courserule")
            .select("rule_id, rule_type, raw_text, catalog_year_id, catalogyear:catalog_year_id(catalog_label, start_year, end_year)")
            .eq("course_id", courseId).order("rule_type").order("rule_id").abortSignal(controller.signal),
          supabase.from("coursesection")
            .select("section_id, section_number, crn, instructor, campus, delivery_mode, section_status, capacity, enrolled, term:term_id(term_name, start_date), sectionmeeting(meeting_id, day_of_week, start_time, end_time, building, room, meeting_type)")
            .eq("course_id", courseId).order("term_id", { ascending: false })
            .order("section_number").abortSignal(controller.signal),
        ]);
        if (controller.signal.aborted) return;
        setResult({ loading: false, course: courseResult.data, rules, sections });
      } catch {
        if (!controller.signal.aborted) {
          setResult({ loading: false, error: "We couldn’t load this course. Please try again." });
        }
      }
    }
    load();
    return () => controller.abort();
  }, [courseId, attempt]);

  useEffect(() => {
    if (!result.loading) heading.current?.focus();
  }, [result.loading]);

  const { course, rules, sections } = result;

  return (
    <main className="catalog-page course-details-page" aria-busy={result.loading}>
      {location.state?.fromCourses ? (
        <button className="catalog-button course-back-link" type="button" onClick={() => navigate(-1)}><span aria-hidden="true">←</span> Back to courses</button>
      ) : <Link className="catalog-button course-back-link" to="/courses"><span aria-hidden="true">←</span> Back to courses</Link>}

      {result.loading ? (
        <section className="catalog-panel">
          <p className="catalog-notice" role="status">Loading course details…</p>
          <div className="catalog-result-placeholder" aria-hidden="true"><span /><span /></div>
        </section>
      ) : result.error ? (
        <section className="catalog-panel course-detail-error">
          <h1 ref={heading} tabIndex={-1}>Unable to load course</h1>
          <p className="catalog-notice catalog-warning" role="alert">{result.error}</p>
          <button className="catalog-button catalog-button-primary" type="button" onClick={() => setAttempt(attempt + 1)}>Try again</button>
        </section>
      ) : !course ? (
        <section className="catalog-panel course-detail-error">
          <h1 ref={heading} tabIndex={-1}>Course not found</h1>
          <p className="catalog-notice">This course is unavailable or you don’t have access to it.</p>
        </section>
      ) : (
        <>
          <header className="catalog-hero course-details-heading">
            <div className="course-heading-meta">
              <span className="course-heading-code">{course.course_code}</span>
              <span className={`course-status-badge${course.active_status ? "" : " catalog-inactive"}`}>
                {course.active_status ? "Active" : "Inactive"}
              </span>
            </div>
            <h1 ref={heading} tabIndex={-1}>{course.course_title}</h1>
            <dl className="course-facts course-summary-facts">
              <div><dt>Credits:</dt><dd>{course.min_credit_hours === course.max_credit_hours
                ? course.min_credit_hours : `${course.min_credit_hours}–${course.max_credit_hours}`}</dd></div>
              <div><dt>Department:</dt><dd>{course.department?.department_name || "Not listed"}</dd></div>
            </dl>
          </header>

          <div className="course-details-layout">
            <div className="course-details-main-column">
              <CourseDetailCard title="Description" className="course-description-card">
                <p className="course-description">{course.description || "No description available."}</p>
              </CourseDetailCard>

              <CourseDetailCard title="Course sections" className="course-sections-card">
                {sections.error ? <p className="catalog-notice catalog-warning" role="alert">Sections could not be loaded. Try reloading the course.</p>
                  : sections.data?.length ? (
                    <>
                      <p className="catalog-muted">All recorded terms are shown. Enrollment information may change.</p>
                      <div className="course-sections">
                        {sections.data.map((section) => <Section section={section} key={section.section_id} />)}
                      </div>
                    </>
                  ) : <p className="course-sections-empty">No sections are listed for this course.</p>}
              </CourseDetailCard>
            </div>
            <aside className="course-requirements-sidebar" aria-label="Course requirements">
              {rules.error ? (
                <CourseDetailCard title="Prerequisites and corequisites" className="course-requirements-card">
                  <p className="catalog-notice catalog-warning" role="alert">Requirements could not be loaded. Try reloading the course.</p>
                </CourseDetailCard>
              ) : <CourseRequirements key={course.course_id} rules={rules.data ?? []} />}
            </aside>
          </div>
          {(rules.error || sections.error) && (
            <button className="catalog-button" type="button" onClick={() => setAttempt(attempt + 1)}>Reload course</button>
          )}
        </>
      )}
    </main>
  );
}

export default CourseDetails;
