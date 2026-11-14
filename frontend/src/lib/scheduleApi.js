const API_BASE = (import.meta.env?.VITE_PLANNING_API_URL ??
  (import.meta.env?.DEV ? 'http://127.0.0.1:8000' : '')).replace(/\/$/, '');

async function post(path, payload, signal) {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/v1/schedules/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      cache: 'no-store',
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error('The schedule service could not be reached. Check that FastAPI is running.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(response.status === 422
      ? 'The schedule request is invalid or an offering changed. Generate the schedule again.'
      : 'The schedule service could not complete the request. Please try again.');
  }
  return data;
}

export function generateSchedules(handoff, term_id, target_credits, signal) {
  return post('generate', { handoff, term_id, target_credits, max_results: 5 }, signal);
}

export function fetchSectionOptions(course_code, term_id, signal) {
  return post('sections', { course_code, term_id }, signal);
}

export function swapScheduleSection(handoff, term_id, current_sections, course_code, new_section_id, signal) {
  return post('swap-section', { handoff, term_id, current_sections, course_code, new_section_id }, signal);
}

export function swapScheduleCourse(handoff, term_id, current_sections, old_course_code, new_course_code, new_section_id, signal) {
  return post('swap-course', { handoff, term_id, current_sections, old_course_code, new_course_code, new_section_id }, signal);
}
