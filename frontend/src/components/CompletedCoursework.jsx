import { useEffect, useMemo, useState } from 'react';
import { supabase } from '../lib/supabaseClient.js';

export default function CompletedCoursework({ rows }) {
  const recent = useMemo(() => rows.slice(-4).reverse(), [rows]);
  const [titles, setTitles] = useState({});
  useEffect(() => {
    if (!recent.length) return;
    const controller = new AbortController();
    async function readTitles() {
      try {
        const { data, error } = await supabase.from('course').select('course_code, course_title')
          .in('course_code', [...new Set(recent.map((row) => row.code))]).abortSignal(controller.signal);
        if (!error && !controller.signal.aborted) setTitles(Object.fromEntries((data ?? []).map((course) => [course.course_code, course.course_title])));
      } catch { /* Catalog labels are optional; saved coursework remains visible. */ }
    }
    readTitles();
    return () => controller.abort();
  }, [recent]);
  if (!rows.length) return <div className="student-empty"><p>No completed courses added yet. Add your coursework to start tracking degree progress.</p></div>;
  return <>
    <ul className="student-coursework" aria-label="Recent saved course entries">{recent.map((row) => {
      const title = row.course_title || titles[row.code];
      return <li key={row.id}><div><strong>{row.code}</strong>{title && <span className="student-course-title">{title}</span>}</div>
        <p>{row.credits ? `${row.credits} ${Number(row.credits) === 1 ? 'credit' : 'credits'}` : 'Credits not reported'}{row.grade && ` · Grade ${row.grade}`}{row.unresolvedEquivalency && ' · Equivalency needs review'}</p>
      </li>;
    })}</ul>
    {rows.length > recent.length && <p className="student-support">Showing {recent.length} of {rows.length} saved course entries.</p>}
  </>;
}
