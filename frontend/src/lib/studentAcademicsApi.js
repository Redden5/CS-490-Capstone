import { buildPlanningRequest, PlanningError, request, validatePlanningReport } from './planningApi.js';
import { supabase } from './supabaseClient.js';

async function studentRequest(path, options) {
  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session?.access_token) {
    throw new PlanningError('Sign in again to access your saved coursework.', 401);
  }
  options.signal?.throwIfAborted();
  return request(`student/academics${path}`, { ...options, token: data.session.access_token });
}

export function loadAcademics(signal) {
  return studentRequest('', { signal });
}

export function saveAcademics(input, expectedRevision, signal) {
  const { program_key, catalog_year, completed_courses } = buildPlanningRequest(input);
  return studentRequest('', { method: 'PUT', signal, body: {
    program_key,
    catalog_year,
    expected_revision: expectedRevision,
    attempts: completed_courses.map((course, index) => {
      const row = input.rows[index];
      return { ...course, id: row.id, reported_credits: row.credits.trim() ? Number(row.credits) : null };
    }),
  } });
}

export async function evaluateAcademics(revision, catalogYear, signal) {
  const report = await studentRequest('/evaluate', { body: { revision }, signal });
  return validatePlanningReport(report, catalogYear);
}
