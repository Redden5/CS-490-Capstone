const API_BASE = (import.meta.env?.VITE_PLANNING_API_URL ??
  (import.meta.env?.DEV ? 'http://127.0.0.1:8000' : '')).replace(/\/$/, '');

export function selectedSectionIds(sections) {
  return Object.fromEntries(Object.entries(sections).map(([code, section]) => [code, section.section_id]));
}

export async function scheduleRequest(action, body, signal) {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/v1/schedules/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), cache: 'no-store',
      signal: AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(180000)]),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error(error.name === 'TimeoutError'
      ? 'Scheduling took too long. Try again.'
      : 'The scheduling service could not be reached. Check that the backend is running.', { cause: error });
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(response.status === 422
      ? 'The term or planning data is no longer supported. Refresh the terms and re-evaluate your coursework.'
      : response.status === 503
        ? 'Scheduling needs backend Supabase configuration. Set SUPABASE_URL and SUPABASE_SECRET_KEY in the backend terminal, then restart it.'
      : response.status === 404
        ? 'Scheduling is unavailable. Restart the backend with the latest application code.'
        : 'Unable to load schedules. Check the backend Supabase configuration and try again.');
  }
  const valid = action === 'generate' ? Array.isArray(data?.options) && Array.isArray(data?.choice_groups)
    : action === 'sections' ? Array.isArray(data?.sections)
      : typeof data?.ok === 'boolean' && (data.ok ? data.sections && typeof data.sections === 'object' : Array.isArray(data.errors));
  if (!valid) throw new Error('The scheduling service returned an incomplete response.');
  return data;
}

const REASONS = {
  PREREQUISITES_UNKNOWN: 'Prerequisites need review',
  PREREQUISITES_UNMET: 'Prerequisites are not met',
  MEETING_TIMES_INCOMPLETE_OR_TBA: 'Meeting times are incomplete or TBA',
  UNVERIFIED_TIME_COMPATIBILITY: 'Time compatibility cannot be fully verified',
  SECTION_STATUS_UNKNOWN: 'Section status is unknown',
  SEAT_AVAILABILITY_UNKNOWN: 'Seat availability is unknown',
  NO_USABLE_SECTIONS_OR_LOCK_UNAVAILABLE: 'No available sections were found',
  COURSE_CREDITS_UNKNOWN_OR_VARIABLE: 'Credits are unknown or require a choice',
  COURSE_CREDITS_UNAVAILABLE: 'Course credits are unavailable',
  TIME_CONFLICT: 'The sections overlap in time',
  CHOICE_GROUP_LIMIT_EXCEEDED: 'This change exceeds the remaining choices for a requirement',
  MANDATORY_COURSE_CANNOT_BE_REPLACED: 'A required course must stay in this schedule',
  NOT_AN_APPROVED_ALTERNATIVE_IN_SAME_GROUP: 'Choose an alternative from the same requirement',
  REPLACEMENT_SECTION_OR_CREDITS_UNAVAILABLE: 'The replacement section or credits are unavailable',
  SECTION_NOT_AVAILABLE_FOR_COURSE_AND_TERM: 'This section is no longer available for the selected course and term',
  COURSE_NOT_IN_SCHEDULE_OR_ALREADY_SELECTED: 'The course is missing from the draft or the replacement is already selected',
  REPLACEMENT_IS_ALSO_MANDATORY_REQUIRES_DEGREE_REVIEW: 'The replacement is separately required and needs degree review',
  COURSE_NOT_IN_SCHEDULE: 'The course is no longer in this draft',
  CREDIT_LIMIT_EXCEEDED: 'This change would exceed the credit limit',
  NO_SEATS_REPORTED: 'No seats are reported available',
  SECTION_CLOSED: 'The section is closed',
  SECTION_FULL: 'The section is full',
  SECTION_CANCELLED: 'The section is cancelled',
  FLEXIBLE_CORE_II_OPTIONS_NOT_INCLUDED: 'Flexible Core II course options are not included in these schedules',
  FLEXIBLE_CREDIT_REQUIREMENT_NOT_SCHEDULED: 'A flexible credit requirement still needs manual planning',
  CHOICE_GROUP_REQUIRES_REVIEW: 'A course-choice requirement still needs review',
};

export function scheduleReason(value) {
  const parts = String(value).split(':');
  if (REASONS[parts[0]]) return REASONS[parts[0]];
  if (REASONS[parts[1]]) return `${parts[0]}: ${REASONS[parts[1]]}`;
  return String(value).replaceAll('_', ' ').toLowerCase();
}

export function meetingLabel(meeting) {
  const days = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  return `${days[meeting.day_of_week] || 'Day TBA'} ${meeting.start_time?.slice(0, 5) || 'TBA'}–${meeting.end_time?.slice(0, 5) || 'TBA'}`;
}
