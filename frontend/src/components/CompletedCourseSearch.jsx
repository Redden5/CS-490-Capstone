import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabaseClient.js';

export default function CompletedCourseSearch({ onAdd }) {
  const [input, setInput] = useState('');
  const [search, setSearch] = useState({ term: '', attempt: 0 });
  const [result, setResult] = useState({ data: [], loading: false, error: null });
  const [announcement, setAnnouncement] = useState('');

  useEffect(() => {
    if (!search.term) return;
    const controller = new AbortController();
    async function load() {
      setResult({ data: [], loading: true, error: null });
      try {
        const { data, error } = await supabase.from('course')
          .select('course_id, course_code, course_title')
          .or(`course_code.ilike.%${search.term}%,course_title.ilike.%${search.term}%`)
          .order('course_code').order('course_id').limit(12).abortSignal(controller.signal);
        if (error) throw error;
        if (!controller.signal.aborted) setResult({ data: data ?? [], loading: false, error: null });
      } catch {
        if (!controller.signal.aborted) setResult({ data: [], loading: false, error: 'Course search is unavailable. Try again or enter a course manually.' });
      }
    }
    load();
    return () => controller.abort();
  }, [search]);

  function submit(event) {
    event.preventDefault();
    const term = input.replace(/[^a-zA-Z0-9\s-]/g, '').trim();
    if (term.length < 2) {
      setResult({ data: [], loading: false, error: 'Enter at least two letters or numbers to search.' });
      return;
    }
    setSearch((current) => ({ term, attempt: current.attempt + 1 }));
  }

  return <section className="planning-card" aria-labelledby="course-search-title">
    <h2 id="course-search-title">Find completed courses</h2>
    <form className="planning-search" onSubmit={submit}>
      <label htmlFor="completed-course-search">Course code or title</label>
      <div><input id="completed-course-search" value={input} onChange={(e) => setInput(e.target.value)}
        placeholder="For example, CS 110" minLength={2} required />
        <button className="planning-button" type="submit">Search</button></div>
    </form>
    <p className="planning-muted">Search includes inactive catalog courses. If a course is missing, enter its code manually below.</p>
    {result.loading && <p role="status">Searching courses…</p>}
    {result.error && <p role="alert">{result.error}</p>}
    {search.term && !result.loading && !result.error && <p>{result.data.length
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
