import test from 'node:test';
import assert from 'node:assert/strict';
import { courseworkGpa } from '../src/lib/courseworkGpa.js';

test('GPA is weighted by credits and includes saved transfer coursework', () => {
  const result = courseworkGpa([
    { grade: 'A', credits: '3' },
    { grade: ' b ', credits: '4', unresolvedEquivalency: true },
    { grade: 'D', credits: '1' },
  ]);
  assert.equal(result.qualityPoints, 25);
  assert.equal(result.gradedCredits, 8);
  assert.equal(result.gpa, 3.125);
  assert.equal(result.includedAttempts, 3);
});

test('non-GPA grades and missing or unsupported evidence do not become zero grades', () => {
  const result = courseworkGpa([
    { grade: 'CR', credits: '3' }, { grade: 'P', credits: '2' }, { grade: 'IP', credits: '3' },
    { grade: 'A-', credits: '3' }, { grade: '', credits: '3' },
    { grade: 'B', credits: '' }, { grade: 'B', credits: '0' }, { grade: 'B', credits: 'Infinity' },
  ]);
  assert.equal(result.gpa, null);
  assert.equal(result.excludedGrades, 3);
  assert.equal(result.missingEvidence, 5);
});

test('each repeated attempt contributes independently and zero-point grades count in the denominator', () => {
  const result = courseworkGpa([
    { code: 'CS 110', grade: 'D', credits: '3' },
    { code: 'CS 110', grade: 'A', credits: '3' },
    { code: 'CS 120', grade: 'F', credits: '3' },
  ]);
  assert.equal(result.gpa, 15 / 9);
  assert.equal(result.gradedCredits, 9);
  assert.equal(result.includedAttempts, 3);
});

test('empty records and fractional credits are handled without premature rounding', () => {
  assert.equal(courseworkGpa([]).gpa, null);
  const result = courseworkGpa([{ grade: 'A', credits: '1.5' }, { grade: 'C', credits: '0.5' }]);
  assert.equal(result.gpa, 3.5);
});
