import test from 'node:test';
import assert from 'node:assert/strict';
import { degreeProgress, requirementsMet, semesterPreview } from '../src/lib/dashboardPresentation.js';
const record = (rows = [{ code: 'CS 110' }]) => ({ loading: false, error: null, revision: 'saved', input: { program: { required_credits: 120 }, rows } });
const audit = (credits, unknown = []) => ({ distinct_course_credit_evidence: { known_credit_subtotal: credits, courses_without_known_credits: unknown } });
test('credit progress uses audit credit evidence and the saved program target', () => {
  assert.deepEqual(degreeProgress(record(), audit(3)), { credits: 3, target: 120, filled: 3, fraction: .025, unknownCount: 0 });
  assert.equal(degreeProgress(record(), audit(130)).filled, 120);
  assert.equal(degreeProgress(record(), audit(130)).credits, 130);
  assert.equal(degreeProgress(record(), audit(3, ['CS 120'])).unknownCount, 1);
});
test('loading, failed, unsaved, malformed, and unavailable evidence never become misleading zeros', () => {
  for (const value of [{ ...record(), loading: true }, { ...record(), error: new Error('unavailable') }, { ...record(), revision: null }]) assert.equal(degreeProgress(value, audit(3)), null);
  for (const credits of [undefined, null, NaN, Infinity, -1]) assert.equal(degreeProgress(record(), audit(credits)), null);
  assert.equal(degreeProgress(record(), null), null);
  assert.equal(degreeProgress({ ...record(), input: { program: {}, rows: [] } }, audit(3)), null);
  assert.equal(degreeProgress(record([]), null).credits, 0);
});
test('requirements met comes only from a valid backend summary', () => {
  assert.equal(requirementsMet({ summary: { candidate_met_rule_occurrences: 2 } }), 2);
  assert.equal(requirementsMet({ summary: { candidate_met_rule_occurrences: 0 } }), 0);
  for (const value of [null, {}, { summary: {} }, { summary: { candidate_met_rule_occurrences: -1 } }]) assert.equal(requirementsMet(value), null);
});
const course = (id, low = 3, high = low) => ({ course_id: id, course_code: 'CS ' + id, min_credit_hours: low, max_credit_hours: high });
test('plan preview respects preferred term, preserves identity, and limits courses to three', () => {
  const plans = [{ id: 'abcdefgh-1', term_id: 1, courses: [course(110)] }, { id: 'ijklmnop-2', term_id: 2, courses: [110,120,210,215].map((id) => course(id)) }];
  const preview = semesterPreview(plans, 2);
  assert.equal(preview.plan.id, plans[1].id); assert.equal(preview.label, 'Plan 2 · ijklmnop');
  assert.equal(preview.minimum, 12); assert.equal(preview.maximum, 12); assert.equal(preview.courses.length, 3); assert.equal(preview.additionalCount, 1);
  assert.equal(semesterPreview(plans, null).plan.id, plans[0].id);
  assert.equal(semesterPreview([], 1), null);
});
test('two plans in one term remain distinct; variable and missing credits are not invented', () => {
  const plans = [{ id: 'one', term_id: 1, courses: [course(110, 1, 4), course(120)] }, { id: 'two', term_id: 1, courses: [course(210)] }];
  assert.equal(semesterPreview(plans, 1).plan.id, 'one');
  assert.equal(semesterPreview(plans, 1).minimum, 4); assert.equal(semesterPreview(plans, 1).maximum, 7);
  assert.equal(semesterPreview([{ id: 'bad', courses: [{ course_id: 1 }] }], null).minimum, null);
  assert.equal(semesterPreview([{ id: 'named', name: 'My draft', courses: [] }], null).label, 'My draft');
});
