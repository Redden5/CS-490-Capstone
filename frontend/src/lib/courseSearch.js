// One identity rule for catalog, completed-course, planner, and audit searches.
export function normalizeCourseSearch(value) {
  return String(value ?? '').normalize('NFKC').replace(/[^a-zA-Z0-9\s-]/g, ' ').trim().replace(/\s+/g, ' ');
}

export function courseCodeSearch(value) {
  const compact = normalizeCourseSearch(value).replace(/[\s-]/g, '');
  const code = /^([a-z]{1,8})([0-9]+[a-z0-9]*)$/i.exec(compact);
  if (code) return { subject: code[1].toUpperCase(), number: code[2].toUpperCase() };
  // Prefer a subject over incidental title matches (e.g. CS in mathematics).
  if (/^[a-z]{1,4}$/i.test(compact)) return { subject: compact.toUpperCase(), number: '' };
  return null;
}

export function matchesCourseSearch(course, value) {
  const term = normalizeCourseSearch(value);
  if (!term) return !String(value ?? '').trim();
  const compact = (value) => String(value ?? '').replace(/[\s-]/g, '').toUpperCase();
  if (courseCodeSearch(term)) return compact(course.course_code).startsWith(compact(term));
  if (compact(course.course_code).includes(compact(term))) return true;
  const title = String(course.course_title ?? '').toLowerCase();
  return term.toLowerCase().split(' ').every((word) => title.includes(word));
}

export async function searchCourses(client, {
  term = '', page = 0, pageSize = 25, activeOnly = false, termId = null,
  columns = 'course_id, course_code, course_title, description, min_credit_hours, max_credit_hours, active_status',
  signal,
} = {}) {
  const clean = normalizeCourseSearch(term);
  if (String(term).trim() && !clean) return { data: [], count: 0 };
  const code = courseCodeSearch(clean);
  async function read(preferCode) {
    signal?.throwIfAborted();
    let query = client.from('course').select(
      termId == null ? columns : `${columns}, coursesection!inner(term_id)`, { count: 'exact' },
    );
    if (activeOnly) query = query.eq('active_status', true);
    if (termId != null) query = query.eq('coursesection.term_id', termId);
    if (clean) {
      if (preferCode) {
        query = query.ilike('course_code', code.number ? `${code.subject}%${code.number}%` : `${code.subject}%`);
      } else {
        const pattern = clean.replace(/[\s-]+/g, '%');
        query = query.or(`course_code.ilike.%${pattern}%,course_title.ilike.%${pattern}%`);
      }
    }
    query = query.order('course_code').order('course_id').range(page * pageSize, (page + 1) * pageSize - 1);
    if (signal) query = query.abortSignal(signal);
    const result = await query;
    if (result.error) throw result.error;
    return { data: result.data ?? [], count: result.count ?? 0 };
  }
  const result = await read(Boolean(code));
  // Short words such as "data" still search titles when no subject exists.
  return code && result.count === 0 ? read(false) : result;
}
