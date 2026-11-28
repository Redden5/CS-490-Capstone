import test from 'node:test';
import assert from 'node:assert/strict';
import { advisorRecommendations, draftCredits } from '../src/lib/advisorRecommendations.js';

const required = (code) => ({
  name: code, kind: 'REQUIRED_COURSE', candidate_progress_status: 'CANDIDATE_NOT_MET',
  remaining_required_course_codes: [code],
});

test('suggestions exclude completed, planned, and unmet courses and prioritize remaining requirements', () => {
  const handoff = {
    completed_course_codes_entered: ['CS 110'],
    requirements: ['CS 110', 'CS 120', 'CS 210', 'CS 215', 'CS 490'].map(required),
    course_eligibility: { courses: [
      { course_code: 'CS 210', status: 'UNMET' },
      { course_code: 'CS 215', status: 'UNKNOWN' },
      { course_code: 'CS 490', status: 'MET' },
    ] },
  };
  const suggestions = advisorRecommendations(handoff, [{ course_code: 'CS 120' }]);
  assert.deepEqual(suggestions.map((course) => course.code), ['CS 490', 'CS 215']);
  assert.equal(suggestions[1].status, 'UNKNOWN');
});

test('elective suggestions preserve bundles and exclude completed or withheld choices', () => {
  const suggestions = advisorRecommendations({
    requirements: [{
      name: 'Science with lab', kind: 'CHOOSE_N', candidate_progress_status: 'CANDIDATE_NOT_MET',
      choice_progress: { choices_remaining_candidate: 1 },
      course_options: [
        { course_codes: ['BSC 120', 'BSC 120L'], missing_codes_to_complete_option: ['BSC 120', 'BSC 120L'] },
        { fully_entered: true, missing_codes_to_complete_option: ['CHM 211'] },
        { candidate_exclusion_reason: 'SHARED_LAB', missing_codes_to_complete_option: ['PHY 201'] },
        { review_flags: ['UNRESOLVED'], missing_codes_to_complete_option: ['PHY 211'] },
      ],
    }],
  });
  assert.deepEqual(suggestions.map((course) => course.code), ['BSC 120', 'BSC 120L']);
  assert.match(suggestions[0].reason, /Complete this option with BSC 120L/);
});

test('flexible options belong to unfinished reviewed requirements and duplicate courses appear once', () => {
  const handoff = {
    requirements: [
      required('ART 112'),
      { rule_id: 'fine-arts', name: 'Fine Arts', kind: 'CREDIT_REQUIREMENT_DRAFT', candidate_progress_status: 'CANDIDATE_NOT_MET' },
      { ...required('CS 300'), remaining_policy_review_flags: ['UNRESOLVED'] },
      { ...required('CS 410'), candidate_progress_status: 'CANDIDATE_MET' },
    ],
    flexible_candidates: { requirements: [
      { requirement_id: 'fine-arts', candidates: [{ course_code: 'ART 112' }, { course_code: 'MUS 142' }] },
    ] },
  };
  assert.deepEqual(advisorRecommendations(handoff).map((course) => course.code), ['ART 112', 'MUS 142']);
  assert.deepEqual(advisorRecommendations(null), []);
});

test('draft totals preserve variable credit ranges and empty drafts', () => {
  assert.equal(draftCredits([]), '0');
  assert.equal(draftCredits([
    { min_credit_hours: 3, max_credit_hours: 3 },
    { min_credit_hours: 1, max_credit_hours: 4 },
  ]), '4–7');
});
