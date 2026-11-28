import { useEffect, useState } from 'react';
import { searchCourses, normalizeCourseSearch } from '../lib/courseSearch.js';
import { useCourseSearch } from '../lib/useCourseSearch.js';
import { supabase } from '../lib/supabaseClient.js';

export default function CompletedCourseSearch({ onAdd }) {
  const { input, setInput, search, submit } = useCourseSearch();
  const [result, setResult] = useState({ data: [], loading: false, error: null });
  const [announcement, setAnnouncement] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setResult({ data: [], loading: true, error: null });
      try {
        if (normalizeCourseSearch(search.term).length < 2) {
          setResult({ data: [], loading: false, error: null });
          return;
        }
        const { data } = await searchCourses(supabase, {
          term: search.term, pageSize: 12, columns: 'course_id, course_code, course_title', signal: controller.signal,
        });
        if (!controller.signal.aborted) setResult({ data: data ?? [], loading: false, error: null });
      } catch {
        if (!controller.signal.aborted) setResult({ data: [], loading: false, error: 'Course search is unavailable. Try again or enter a course manually.' });
      }
    }
    load();
    return () => controller.abort();
  }, [search]);

  return <section className="planning-card" aria-labelledby="course-search-title">
    <h2 id="course-search-title">Find completed courses</h2>
    <form className="planning-search" onSubmit={submit}>
      <label htmlFor="completed-course-search">Course code or title</label>
      <div><input id="completed-course-search" value={input} onChange={(e) => setInput(e.target.value)}
        placeholder="CS, CS120, or a course title" minLength={2} required />
        <button className="planning-button" type="submit">Search</button></div>
    </form>
    {input.trim().length === 1 && <p className="planning-muted">Type at least two letters or numbers to search.</p>}
    <p className="planning-muted">Search includes inactive catalog courses. If a course is missing, enter its code manually below.</p>
    {result.loading && <p role="status">Searching courses…</p>}
    {result.error && <p role="alert">{result.error}</p>}
    {normalizeCourseSearch(search.term).length >= 2 && !result.loading && !result.error && <p>{result.data.length
      ? 'Showing up to 12 matches. Refine your search to find more.' : 'No matching courses found.'}</p>}
    <ul className="planning-search-results">
      {result.data.map((course) => <li key={course.course_id}>
        <span><strong>{course.course_code}</strong> — {course.course_title}</span>
        <button className="planning-button secondary" type="button" aria-label={`Add ${course.course_code}`}
          onClick={() => { onAdd(course.course_code); setAnnouncement(`${course.course_code} added to completed coursework.`); }}>Add</button>
      </li>)}
    </ul>
    <p className="planning-muted" role="status">{announcement}</p>
  </section>;
}
