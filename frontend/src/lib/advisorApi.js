const API_BASE = (import.meta.env?.VITE_PLANNING_API_URL ??
  (import.meta.env?.DEV ? 'http://127.0.0.1:8000' : '')).replace(/\/$/, '');

export async function askAdvisor(question, courseCodes, signal) {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/v1/advisor/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, course_codes: courseCodes }),
      cache: 'no-store',
      signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new Error(error.name === 'TimeoutError'
      ? 'The Advisor took too long to respond. Try again.'
      : 'The Advisor service could not be reached. Check that the backend is running and try again.', { cause: error });
  }
  if (response.status === 429) {
    const seconds = Number(response.headers.get('Retry-After'));
    throw new Error(`You’ve reached the Advisor’s question limit. Try again in ${seconds > 0 ? seconds : 60} seconds.`);
  }
  if (response.status === 503) throw new Error('The Advisor’s course information is temporarily unavailable. Please try again later.');
  if (!response.ok) throw new Error('The Advisor could not answer that question. Try again.');
  const data = await response.json().catch(() => null);
  if (typeof data?.answer !== 'string' || !Array.isArray(data.sources) || !Array.isArray(data.course_codes)) {
    throw new Error('The Advisor returned an unexpected response. Try again.');
  }
  return data;
}
