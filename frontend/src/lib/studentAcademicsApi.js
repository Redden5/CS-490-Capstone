import { buildPlanningRequest, validatePlanningReport } from './planningApi.js';
import { studentRequest } from './studentApi.js';

export function loadAcademics(signal) {
  return studentRequest('academics', { signal });
}

export function saveAcademics(input, expectedRevision, signal) {
  const { program_key, catalog_year, completed_courses } = buildPlanningRequest(input);
  return studentRequest('academics', { method: 'PUT', signal, body: {
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
  const report = await studentRequest('academics/evaluate', { body: { revision }, signal });
  return validatePlanningReport(report, catalogYear);
}
