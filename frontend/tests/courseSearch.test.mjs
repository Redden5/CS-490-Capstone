import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeCourseSearch, courseCodeSearch, matchesCourseSearch, searchCourses } from '../src/lib/courseSearch.js';

const rows = [
  { course_id: 1, course_code: 'CS 120', course_title: 'Computer Science II', active_status: true, terms: [1] },
  { course_id: 2, course_code: 'CS 120L', course_title: 'Programming Lab', active_status: true, terms: [2] },
  { course_id: 3, course_code: 'CS 215', course_title: 'Data Structures', active_status: false, terms: [1] },
  { course_id: 4, course_code: 'MTH 100', course_title: 'Mathematics', active_status: true, terms: [1] },
  { course_id: 5, course_code: 'PHY 101', course_title: 'Physics', active_status: true, terms: [1] },
];
const like = (value, pattern) => new RegExp('^' + pattern.split('%').map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$', 'i').test(value);
function client() {
  const calls = [];
  return { calls, from(table) {
    assert.equal(table, 'course');
    const call = { filters: [], first: 0, last: 24 };
    const q = {
      select(columns, options) { call.columns = columns; assert.equal(options.count, 'exact'); return q; },
      eq(key, value) { call.filters.push((r) => key === 'coursesection.term_id' ? r.terms.includes(value) : r[key] === value); return q; },
      ilike(key, pattern) { call.pattern = pattern; call.filters.push((r) => like(r[key], pattern)); return q; },
      or(filter) { call.or = filter; call.filters.push((r) => filter.split(',').some((part) => { const [key, , pattern] = part.split('.'); return like(r[key], pattern); })); return q; },
      order() { return q; },
      range(first, last) { call.first = first; call.last = last; return q; },
      abortSignal(signal) { signal.throwIfAborted(); return q; },
      then(resolve) { calls.push(call); const found = rows.filter((r) => call.filters.every((f) => f(r))); return Promise.resolve({ data: found.slice(call.first, call.last + 1), count: found.length, error: null }).then(resolve); },
    }; return q;
  } };
}

test('normalizes whitespace and fullwidth input without allowing filter syntax', () => {
  assert.equal(normalizeCourseSearch('  ＣＳ１２０  '), 'CS120');
  assert.equal(normalizeCourseSearch(' Data   Structures '), 'Data Structures');
  assert.equal(normalizeCourseSearch('cs%,()'), 'cs');
  assert.deepEqual(courseCodeSearch(' cS - 120l '), { subject: 'CS', number: '120L' });
});

test('subject-only search finds CS rather than incidental mathematics and physics titles', async () => {
  for (const term of ['cs', 'CS', ' Cs ']) {
    const db = client(); const result = await searchCourses(db, { term });
    assert.deepEqual(result.data.map((r) => r.course_code), ['CS 120', 'CS 120L', 'CS 215']);
    assert.equal(db.calls.length, 1);
  }
});

test('compact, spaced, lowercase, and hyphenated codes return the same courses', async () => {
  for (const term of ['cs120', 'CS120', 'CS 120', 'cs-120', 'CS   120']) {
    const result = await searchCourses(client(), { term });
    assert.deepEqual(result.data.map((r) => r.course_id), [1, 2]);
  }
});

test('title search handles phrases and short words that are not subjects', async () => {
  for (const term of ['Data Structures', 'data', 'DATA   structures']) {
    const result = await searchCourses(client(), { term });
    assert.deepEqual(result.data.map((r) => r.course_id), [3]);
  }
});

test('pagination retains filtered counts and never falls back just because a page is empty', async () => {
  const db = client();
  const result = await searchCourses(db, { term: 'CS', page: 1, pageSize: 1 });
  assert.equal(result.count, 3); assert.equal(result.data[0].course_id, 2);
  const beyond = await searchCourses(db, { term: 'CS', page: 9, pageSize: 1 });
  assert.deepEqual(beyond.data, []); assert.equal(beyond.count, 3); assert.equal(db.calls.length, 2);
});

test('preserves active-only and academic-term filters', async () => {
  const db = client();
  const result = await searchCourses(db, { term: 'cs', activeOnly: true, termId: 1 });
  assert.deepEqual(result.data.map((r) => r.course_id), [1]);
  assert.match(db.calls[0].columns, /coursesection!inner/);
});

test('clearing browses the catalog; punctuation-only input never exposes all results', async () => {
  const db = client();
  assert.equal((await searchCourses(db, { term: '' })).count, 5);
  assert.deepEqual(await searchCourses(db, { term: ',()%_' }), { data: [], count: 0 });
  assert.equal(db.calls.length, 1);
});

test('audit course-code filtering accepts the same forms', () => {
  for (const term of ['cs', 'CS', 'CS120', 'cs 120', 'CS-120', '  cs   120  ']) {
    assert.equal(matchesCourseSearch(rows[0], term), true);
    assert.equal(matchesCourseSearch(rows[3], term), false);
  }
  assert.equal(matchesCourseSearch(rows[0], ''), true);
  assert.equal(matchesCourseSearch(rows[0], ',()%'), false);
});

test('cancellation is not converted into an empty successful result', async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(searchCourses(client(), { term: 'CS', signal: controller.signal }), { name: 'AbortError' });
});
