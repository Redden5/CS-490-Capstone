import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { meetingLabel, scheduleReason, scheduleRequest, selectedSectionIds } from '../src/lib/scheduleApi.js';
const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('generation posts the audit, disables caching, and preserves tentative evidence', async () => {
  const body = { handoff: { schema_version: '2.1.0' }, term_id: 1, target_credits: 15 };
  const data = { options: [{ status: 'TENTATIVE', warnings: ['CS 120:PREREQUISITES_UNKNOWN'] }], choice_groups: [] };
  globalThis.fetch = async (url, init) => {
    assert.equal(url, '/api/v1/schedules/generate');
    assert.equal(init.method, 'POST');
    assert.equal(init.cache, 'no-store');
    assert.deepEqual(JSON.parse(init.body), body);
    return Response.json(data);
  };
  assert.deepEqual(await scheduleRequest('generate', body), data);
});

test('swap payload uses section identities and returns server rejection unchanged', async () => {
  assert.deepEqual(selectedSectionIds({ 'CS 120': { section_id: 10, crn: '1234' } }), { 'CS 120': 10 });
  const rejected = { ok: false, errors: ['TIME_CONFLICT:10:20'] };
  globalThis.fetch = async () => Response.json(rejected);
  assert.deepEqual(await scheduleRequest('swap-section', {}), rejected);
});

test('unsupported routes, invalid data, and backend failures have safe messages', async () => {
  for (const [status, message] of [[404, /Restart the backend/], [422, /re-evaluate/], [500, /Supabase configuration/], [503, /SUPABASE_SECRET_KEY/]]) {
    globalThis.fetch = async () => Response.json({ detail: 'private details' }, { status });
    await assert.rejects(scheduleRequest('generate', {}), message);
  }
  globalThis.fetch = async () => Response.json({ options: [] });
  await assert.rejects(scheduleRequest('generate', {}), /incomplete response/);
  globalThis.fetch = async () => new Response('not JSON');
  await assert.rejects(scheduleRequest('sections', {}), /incomplete response/);
});

test('cancelled schedule requests stay cancelled and network failures are actionable', async () => {
  const controller = new AbortController();
  controller.abort();
  globalThis.fetch = async () => { throw new DOMException('Aborted', 'AbortError'); };
  await assert.rejects(scheduleRequest('generate', {}, controller.signal), { name: 'AbortError' });
  globalThis.fetch = async () => { throw new TypeError('Network'); };
  await assert.rejects(scheduleRequest('generate', {}), /backend is running/);
});

test('unknown meetings and prerequisite warnings remain visible to students', () => {
  assert.equal(meetingLabel({ day_of_week: 1, start_time: '09:00:00', end_time: '10:00:00' }), 'Mon 09:00–10:00');
  assert.equal(meetingLabel({}), 'Day TBA TBA–TBA');
  assert.match(scheduleReason('CS 120:PREREQUISITES_UNKNOWN'), /CS 120: Prerequisites need review/);
  assert.match(scheduleReason('TIME_CONFLICT:10:20'), /overlap/);
});
