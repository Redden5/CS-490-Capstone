const API_BASE = (import.meta.env?.VITE_PLANNING_API_URL ??
  (import.meta.env?.DEV ? 'http://127.0.0.1:8000' : '')).replace(/\/$/, '');

export class PlanningError extends Error {
  constructor(message, status = 0, fields = []) {
    super(message);
    this.name = 'PlanningError';
    this.status = status;
    this.fields = fields;
  }
}

async function request(path, { signal, body } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/v1/planning/${path}`, {
      method: body ? 'POST' : 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
      signal: AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(180000)]),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new PlanningError(error.name === 'TimeoutError'
      ? 'The planning service took too long. Please try again.'
      : 'The planning service could not be reached. Check that it is running and try again.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const fields = Array.isArray(data?.fields)
      ? data.fields.filter((field) => ['program_key', 'catalog_year', 'completed_courses', 'reported_credits'].includes(field)) : [];
    const messages = {
      404: 'This program and catalog year are no longer supported. Refresh the available programs.',
      422: 'Check the course codes, grades, and reported credits. Credits must match the catalog range for each completed course.',
    };
    throw new PlanningError(messages[response.status] ?? 'The planning service could not complete the request. Please try again.', response.status, fields);
  }
  return data;
}

export async function fetchPrograms(signal) {
  const data = await request('programs', { signal });
  if (!Array.isArray(data) || !data.every((p) =>
    typeof p.program_key === 'string' && typeof p.catalog_year === 'string' && typeof p.program_title === 'string')) {
    throw new PlanningError('The planning service returned an unexpected program list.');
  }
  return data;
}

export async function evaluatePlanning(body, signal) {
  const data = await request('evaluate', { body, signal });
  if (data?.schema_version !== '2.1.0' || !Array.isArray(data.requirements) ||
    !Array.isArray(data.course_eligibility?.courses) || !Array.isArray(data.flexible_candidates?.requirements) ||
    !data.summary || !data.degree || data.degree.catalog_year !== body.catalog_year) {
    throw new PlanningError('The planning service returned an unsupported or incomplete report.');
  }
  return data;
}

export function buildPlanningRequest(input) {
  if (!input.program) throw new PlanningError('Choose a supported program and catalog year.');
  const reported_credits = {};
  const completed_courses = input.rows.map((row, index) => {
    const course_code = row.code.trim().toUpperCase().replace(/\s+/g, ' ');
    if (!course_code) throw new PlanningError(`Enter a course code for coursework row ${index + 1}.`);
    const grade = row.grade.trim().toUpperCase();
    if (grade === 'F') throw new PlanningError(`Remove ${course_code}: enter successfully completed courses only.`);
    if (row.credits.trim()) {
      const amount = Number(row.credits);
      if (!Number.isFinite(amount) || amount <= 0) throw new PlanningError(`Enter a positive credit amount for ${course_code}, or leave it blank.`);
      if (reported_credits[course_code] != null && reported_credits[course_code] !== amount) {
        throw new PlanningError(`Use the same reported credit amount for each attempt of ${course_code}.`);
      }
      reported_credits[course_code] = amount;
    }
    return { course_code, grade: grade || null, equivalency_verified: !row.unresolvedEquivalency };
  });
  return { program_key: input.program.program_key, catalog_year: input.program.catalog_year, completed_courses, reported_credits };
}
