import { useEffect, useRef, useState } from "react";
import { supabase } from "../lib/supabaseClient.js";
import DashboardCard from "../components/DashboardCard.jsx";
import "../styles/Planner.css";

const PAGE_SIZE = 25;

function creditLabel(min, max) {
  return min === max ? `${min}` : `${min}–${max}`;
}

function CoursePicker({ term, selected, onSelect, onClose }) {
  const dialog = useRef(null);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState({ loading: true });

  useEffect(() => {
    const element = dialog.current;
    element.showModal();
    return () => element.close();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setResult({ loading: true });
      try {
        // Query courses as the parent so multiple sections never duplicate a course.
        let query = supabase.from("course").select(
          "course_id, course_code, course_title, min_credit_hours, max_credit_hours, coursesection!inner(term_id)",
          { count: "exact" }
        ).eq("coursesection.term_id", term.term_id);
        const safeSearch = search.replace(/[^a-zA-Z0-9\s-]/g, "").trim();
        if (safeSearch) {
          query = query.or(`course_code.ilike.%${safeSearch}%,course_title.ilike.%${safeSearch}%`);
        }
        const { data, error, count } = await query.order("course_code").order("course_id")
          .range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1).abortSignal(controller.signal);
        if (error) throw error;
        if (!controller.signal.aborted) setResult({ loading: false, courses: data ?? [], total: count ?? 0 });
      } catch {
        if (!controller.signal.aborted) setResult({ loading: false, error: true });
      }
    }
    load();
    return () => controller.abort();
  }, [term.term_id, search, page, attempt]);

  return (
    <dialog className="planner-course-dialog" ref={dialog} aria-labelledby="course-picker-title" onCancel={onClose}>
      <div className="planner-dialog-heading">
        <h2 id="course-picker-title">Add course · {term.term_name}</h2>
        <button type="button" className="planner-secondary-button" onClick={onClose}>Close</button>
      </div>
      <p>Search courses with recorded offerings in this semester. Adding a course does not enroll you.</p>
      <form className="planner-search" onSubmit={(event) => {
        event.preventDefault();
        setSearch(input.trim());
        setPage(0);
      }}>
        <label htmlFor="planner-course-search">Course code or title</label>
        <div>
          <input id="planner-course-search" value={input} onChange={(event) => setInput(event.target.value)} />
          <button className="planner-primary-button" type="submit">Search</button>
        </div>
      </form>
      <div aria-busy={result.loading}>
        {result.loading ? <p role="status">Loading courses…</p> : result.error ? (
          <div role="alert">
            <p>Unable to load course offerings. Please try again.</p>
            <button type="button" onClick={() => setAttempt(attempt + 1)}>Try again</button>
          </div>
        ) : (
          <>
            <p role="status">{result.total} courses found</p>
            {!result.courses.length && <p>{search ? "No courses match your search in this semester." : "No course offerings are listed for this semester."}</p>}
            <ul className="planner-picker-results">
              {result.courses.map((course) => {
                const added = selected.some((item) => item.course_id === course.course_id);
                return (
                  <li key={course.course_id}>
                    <div><strong>{course.course_code}</strong> — {course.course_title}
                      <p>{creditLabel(course.min_credit_hours, course.max_credit_hours)} credits</p>
                    </div>
                    <button type="button" className="planner-secondary-button" disabled={added}
                      aria-label={`${added ? "Added" : "Add"} ${course.course_code}`}
                      onClick={() => onSelect(course)}>{added ? "Added" : "Add"}</button>
                  </li>
                );
              })}
            </ul>
            {result.total > PAGE_SIZE && (
              <div className="planner-pagination">
                <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
                <span>Page {page + 1} of {Math.ceil(result.total / PAGE_SIZE)}</span>
                <button type="button" disabled={(page + 1) * PAGE_SIZE >= result.total} onClick={() => setPage(page + 1)}>Next</button>
              </div>
            )}
          </>
        )}
      </div>
    </dialog>
  );
}

function SemesterColumn({ term }) {
  const [courses, setCourses] = useState([]);
  const [picking, setPicking] = useState(false);
  const min = courses.reduce((sum, course) => sum + Number(course.min_credit_hours), 0);
  const max = courses.reduce((sum, course) => sum + Number(course.max_credit_hours), 0);

  return (
    <section className="planner-semester-card" aria-label={term.term_name}>
      <h2>{term.term_name}</h2>
      <p>{creditLabel(min, max)} planned credits</p>
      {!courses.length && <p className="planner-empty">No courses added yet.</p>}
      <ul className="planner-course-list">
        {courses.map((course) => (
          <li className="planner-course-slot planner-course-slot-filled" key={course.course_id}>
            <div className="planner-course-title"><strong>{course.course_code}</strong><span>{course.course_title}</span></div>
            <p>{creditLabel(course.min_credit_hours, course.max_credit_hours)} credits</p>
            <button type="button" className="planner-remove-button" aria-label={`Remove ${course.course_code} from ${term.term_name}`}
              onClick={() => setCourses(courses.filter((item) => item.course_id !== course.course_id))}>Remove</button>
          </li>
        ))}
      </ul>
      <button className="planner-secondary-button" type="button" onClick={() => setPicking(true)}>Add Course</button>
      {picking && <CoursePicker term={term} selected={courses} onClose={() => setPicking(false)}
        onSelect={(course) => setCourses((current) => current.some((item) => item.course_id === course.course_id) ? current : [...current, course])} />}
    </section>
  );
}

function Planner() {
  const [terms, setTerms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [termId, setTermId] = useState("");
  const [semesters, setSemesters] = useState([]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError(false);
      try {
        const allTerms = [];
        // Read every page rather than silently relying on the API's row limit.
        for (let start = 0; ; start += 100) {
          const { data, error } = await supabase.from("term")
            .select("term_id, term_name, start_date").eq("active_status", true)
            .order("start_date", { nullsFirst: false }).order("term_id")
            .range(start, start + 99).abortSignal(controller.signal);
          if (error) throw error;
          if (controller.signal.aborted) return;
          allTerms.push(...(data ?? []));
          if (!data || data.length < 100) break;
        }
        setTerms(allTerms);
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    load();
    return () => controller.abort();
  }, [attempt]);

  const availableTerms = terms.filter((term) => !semesters.some((semester) => semester.term_id === term.term_id));

  return (
    <main className="planner-page">
      <div className="planner-heading">
        <h1>Planner</h1>
        <p>Organize future semesters using recorded course offerings.</p>
      </div>
      <DashboardCard title="Semester Plan">
        <p>Your plan is temporary. Refreshing or leaving this page clears your selections.</p>
        {loading ? <p role="status">Loading semesters…</p> : error ? (
          <div role="alert"><p>Unable to load semesters.</p><button type="button" onClick={() => setAttempt(attempt + 1)}>Try again</button></div>
        ) : !terms.length ? <p>No active semesters are listed.</p> : !availableTerms.length ? <p>All available semesters have been added.</p> : (
          <form className="planner-plan-actions" onSubmit={(event) => {
            event.preventDefault();
            const term = availableTerms.find((item) => String(item.term_id) === termId);
            if (term) { setSemesters([...semesters, term]); setTermId(""); }
          }}>
            <label htmlFor="planner-term">Semester</label>
            <select id="planner-term" value={termId} onChange={(event) => setTermId(event.target.value)} required>
              <option value="">Choose a semester</option>
              {availableTerms.map((term) => <option key={term.term_id} value={term.term_id}>{term.term_name}</option>)}
            </select>
            <button className="planner-primary-button" type="submit" disabled={!termId}>Add Semester</button>
          </form>
        )}
      </DashboardCard>
      {!semesters.length && <p>Add a semester to begin planning.</p>}
      <div className="planner-semester-grid">
        {semesters.map((term) => <SemesterColumn term={term} key={term.term_id} />)}
      </div>
    </main>
  );
}

export default Planner;
