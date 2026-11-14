import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { generateSchedules, fetchSectionOptions, swapScheduleSection, swapScheduleCourse } from '../src/lib/scheduleApi.js';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('generate sends a v2.2 degree planning handoff to the schedule endpoint', async () => {
  const handoff = { schema_version: '2.2.0', requirements: [] };
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/v1/schedules/generate');
    assert.equal(options.method, 'POST');
    assert.equal(options.cache, 'no-store');
    assert.deepEqual(JSON.parse(options.body), { handoff, term_id: 1, target_credits: 15, max_results: 5 });
    return Response.json({ options: [], required_courses: [] });
  };
  assert.deepEqual(await generateSchedules(handoff, 1, 15), { options: [], required_courses: [] });
});

test('section and course swap preserve current selection IDs and use dedicated endpoints', async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return Response.json({ ok: true, sections: {} });
  };
  await fetchSectionOptions('CS 120', 1);
  await swapScheduleSection({}, 1, { 'CS 120': 868 }, 'CS 120', 869);
  await swapScheduleCourse({}, 1, { 'ENG 101': 100 }, 'ENG 101', 'ENG 102', 101);
  assert.deepEqual(requests.map((r) => r.url), [
    '/api/v1/schedules/sections', '/api/v1/schedules/swap-section', '/api/v1/schedules/swap-course',
  ]);
  assert.equal(requests[1].body.current_sections['CS 120'], 868);
  assert.equal(requests[2].body.new_course_code, 'ENG 102');
});

test('network errors hide raw backend information', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'private server path' }, { status: 500 });
  await assert.rejects(generateSchedules({}, 1, 15), (error) => {
    assert.match(error.message, /could not complete/);
    assert.doesNotMatch(error.message, /private/);
    return true;
  });
});
