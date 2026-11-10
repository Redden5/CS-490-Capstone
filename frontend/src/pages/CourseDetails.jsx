import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { supabase } from "../lib/supabaseClient.js";
import DashboardCard from "../components/DashboardCard.jsx";
import CourseRequirements from "../components/CourseRequirements.jsx";
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
    <main className="course-details-page" aria-busy={result.loading}>
      {location.state?.fromCourses ? (
        <button className="course-back-link" type="button" onClick={() => navigate(-1)}>← Back to courses</button>
      ) : <Link className="course-back-link" to="/courses">← Back to courses</Link>}

      {result.loading ? <p role="status">Loading course details…</p> : result.error ? (
        <>
          <h1 ref={heading} tabIndex={-1}>Unable to load course</h1>
          <p role="alert">{result.error}</p>
          <button type="button" onClick={() => setAttempt(attempt + 1)}>Try again</button>
        </>
      ) : !course ? (
        <>
          <h1 ref={heading} tabIndex={-1}>Course not found</h1>
          <p>This course is unavailable or you don’t have access to it.</p>
        </>
      ) : (
        <>
          <header className="course-details-heading">
            <p>{course.course_code}</p>
            <h1 ref={heading} tabIndex={-1}>{course.course_title}</h1>
            <dl className="course-facts">
              <div><dt>Credits</dt><dd>{course.min_credit_hours === course.max_credit_hours
                ? course.min_credit_hours : `${course.min_credit_hours}–${course.max_credit_hours}`}</dd></div>
              <div><dt>Catalog status</dt><dd>{course.active_status ? "Active" : "Inactive"}</dd></div>
              <div><dt>Department</dt><dd>{course.department?.department_name || "Not listed"}</dd></div>
            </dl>
          </header>

          <DashboardCard title="Description">
            <p className="course-description">{course.description || "No description available."}</p>
          </DashboardCard>

          <DashboardCard title="Prerequisites and corequisites">
            {rules.error ? <p role="alert">Requirements could not be loaded. Try reloading the course.</p>
              : <CourseRequirements key={course.course_id} rules={rules.data ?? []} />}
          </DashboardCard>

          <DashboardCard title="Course sections">
            {sections.error ? <p role="alert">Sections could not be loaded. Try reloading the course.</p>
              : sections.data?.length ? (
                <>
                  <p>All recorded terms are shown. Enrollment information may change.</p>
                  <div className="course-sections">
                    {sections.data.map((section) => <Section section={section} key={section.section_id} />)}
                  </div>
                </>
              ) : <p>No sections are listed for this course.</p>}
          </DashboardCard>
          {(rules.error || sections.error) && (
            <button type="button" onClick={() => setAttempt(attempt + 1)}>Reload course</button>
          )}
        </>
      )}
    </main>
  );
}

export default CourseDetails;
