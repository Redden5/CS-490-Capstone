import { studentRequest } from './studentApi.js';

export function loadPlanner(signal) {
  return studentRequest('planner', { signal });
}

export function saveSemester(semester) {
  const { revision, ...record } = semester;
  return studentRequest('planner/semesters', {
    method: 'PUT', body: { ...record, expected_revision: revision ?? null },
  });
}

export function deleteSemester(id, revision) {
  return studentRequest(`planner/semesters/${id}`, {
    method: 'DELETE', body: { expected_revision: revision },
  });
}

export function saveGeneratorPreferences(termId, targetCredits) {
  return studentRequest('planner/preferences', {
    method: 'PUT', body: { term_id: termId ? Number(termId) : null, target_credits: Number(targetCredits) },
  });
}
