import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { buildPlanningRequest, fetchPrograms, evaluatePlanning } from '../src/lib/planningApi.js';
import { choiceProgressText, requirementGroups, safeSourceUrl } from '../src/lib/planningPresentation.js';
const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const program = { program_key: 'computer-science-bs', program_title: 'Computer Science, B.S.', catalog_year: '2026-2027' };
const row = (extra = {}) => ({ id: 'local-only', code: ' CS 110 ', grade: '', credits: '', unresolvedEquivalency: false, ...extra });
const body = { program_key: program.program_key, catalog_year: program.catalog_year, completed_courses: [], reported_credits: {} };
const handoff = () => ({ schema_version: '2.1.0', degree: { catalog_year: program.catalog_year }, requirements: [], summary: {},
  course_eligibility: { evaluation_status: 'UNAVAILABLE', courses: [{ course_code: 'CS 210', status: 'UNKNOWN' }] },
  flexible_candidates: { generation_status: 'UNAVAILABLE', requirements: [] } });

test('empty coursework is valid and request excludes frontend-only state', () => {
  assert.deepEqual(buildPlanningRequest({ program, rows: [] }), body);
  assert.throws(() => buildPlanningRequest({ program: null, rows: [] }), /Choose a supported/);
});
test('preserves separate attempts, unknown grades and unresolved equivalency', () => {
  const result = buildPlanningRequest({ program, rows: [row({ grade: ' b ', credits: '3' }), row({ unresolvedEquivalency: true }), row({ code: 'BSC 120LH', grade: 'P' })] });
  assert.deepEqual(result.completed_courses, [
    { course_code: 'CS 110', grade: 'B', equivalency_verified: true },
    { course_code: 'CS 110', grade: null, equivalency_verified: false },
    { course_code: 'BSC 120LH', grade: 'P', equivalency_verified: true },
  ]);
  assert.deepEqual(result.reported_credits, { 'CS 110': 3 });
});
test('rejects blank codes, failures and invalid or conflicting credits', () => {
  for (const fields of [{ code: ' ' }, { grade: 'F' }, { credits: '0' }, { credits: '-1' }, { credits: 'Infinity' }, { credits: 'not-a-number' }]) {
    assert.throws(() => buildPlanningRequest({ program, rows: [row(fields)] }));
  }
  assert.throws(() => buildPlanningRequest({ program, rows: [row({ credits: '3' }), row({ credits: '4' })] }), /same reported credit/);
});
test('loads only supported program records', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/v1/planning/programs');
    assert.equal(options.method, 'GET');
    return Response.json([program]);
  };
  assert.deepEqual(await fetchPrograms(), [program]);
  globalThis.fetch = async () => Response.json([{ program_key: 'missing-title-and-year' }]);
  await assert.rejects(fetchPrograms(), /unexpected program list/);
});
test('sends request JSON without caching and preserves a degraded 200 response', async () => {
  const expected = handoff();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/v1/planning/evaluate');
    assert.equal(options.method, 'POST');
    assert.equal(options.cache, 'no-store');
    assert.deepEqual(JSON.parse(options.body), body);
    assert.deepEqual(options.headers, { 'Content-Type': 'application/json' });
    return Response.json(expected);
  };
  assert.deepEqual(await evaluatePlanning(body), expected);
});
test('rejects unsupported or mismatched reports instead of showing success', async () => {
  for (const data of [{}, { ...handoff(), schema_version: '1.0.0' }, { ...handoff(), degree: { catalog_year: '2025-2026' } }]) {
    globalThis.fetch = async () => Response.json(data);
    await assert.rejects(evaluatePlanning(body), /unsupported or incomplete/);
  }
});
test('maps errors without exposing arbitrary server details', async () => {
  for (const status of [404, 422, 500]) {
    globalThis.fetch = async () => Response.json({ detail: 'private server detail', fields: ['completed_courses', 'private_input'] }, { status });
    await assert.rejects(evaluatePlanning(body), (error) => {
      assert.equal(error.status, status);
      assert.ok(!error.message.includes('private'));
      assert.deepEqual(error.fields, ['completed_courses']);
      return true;
    });
  }
});
test('network and non-JSON responses produce actionable errors', async () => {
  globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(fetchPrograms(), /could not be reached/);
  globalThis.fetch = async () => new Response('<html>not the API</html>');
  await assert.rejects(fetchPrograms(), /unexpected program list/);
});
test('caller cancellation remains a cancellation', async () => {
  const controller = new AbortController();
  controller.abort();
  globalThis.fetch = async () => { throw controller.signal.reason; };
  await assert.rejects(evaluatePlanning(body, controller.signal), (error) => error.name === 'AbortError');
});
test('groups requirements without flattening choice bundles or recalculating progress', () => {
  const requirement = { rule_id: 'lab', section_path: ['Major', 'Science'], course_options: [{ kind: 'ALL_OF', course_codes: ['PHY 201', 'PHY 202'] }], planning_disposition: 'REVIEW_REQUIRED' };
  const groups = requirementGroups([requirement]);
  assert.equal(groups[0][0], 'Major');
  assert.equal(groups[0][1][0], requirement);
  assert.equal(requirement.planning_disposition, 'REVIEW_REQUIRED');
});
test('explains why PHY 201 cannot reuse the PHY 202 lab from the completed PHY 211 option', () => {
  const completed = { kind: 'ALL_OF', course_codes: ['PHY 211', 'PHY 202'], fully_entered: true, missing_codes_to_complete_option: [] };
  const alternative = { kind: 'ALL_OF', course_codes: ['PHY 201', 'PHY 202'], fully_entered: false, missing_codes_to_complete_option: ['PHY 201'] };
  assert.equal(choiceProgressText(completed), 'All courses reported completed.');
  assert.equal(choiceProgressText(alternative), 'Still needed for this option: PHY 201');
  assert.equal(
    choiceProgressText({ ...alternative, candidate_exclusion_reason: 'LAB_ALREADY_USED_IN_MAJOR_CHOICE' }),
    'Option excluded. This lab already counts toward another science option in your major.',
  );
});
test('only allows ordinary web source links', () => {
  assert.equal(safeSourceUrl('https://catalog.marshall.edu/undergraduate/'), 'https://catalog.marshall.edu/undergraduate/');
  for (const value of ['javascript:alert(1)', 'data:text/html,test', 'https://user:secret@example.com', '/relative', undefined]) assert.equal(safeSourceUrl(value), null);
});
