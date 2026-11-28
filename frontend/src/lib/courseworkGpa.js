const GRADE_POINTS = new Map([['A', 4], ['B', 3], ['C', 2], ['D', 1], ['F', 0]]);
const NON_GPA_GRADES = new Set(['P', 'S', 'CR', 'NC', 'W', 'I', 'AU', 'T', 'TR', 'IP']);

export function courseworkGpa(rows) {
  let qualityPoints = 0;
  let gradedCredits = 0;
  let includedAttempts = 0;
  let excludedGrades = 0;
  let missingEvidence = 0;
  const breakdown = [...GRADE_POINTS].map(([grade, points]) => ({
    grade, points, attempts: 0, credits: 0, qualityPoints: 0,
  }));

  for (const row of rows) {
    const grade = String(row.grade ?? '').trim().toUpperCase();
    if (NON_GPA_GRADES.has(grade)) {
      excludedGrades++;
      continue;
    }
    const credits = Number(row.credits);
    if (!GRADE_POINTS.has(grade) || !Number.isFinite(credits) || credits <= 0) {
      missingEvidence++;
      continue;
    }
    const points = GRADE_POINTS.get(grade) * credits;
    qualityPoints += points;
    gradedCredits += credits;
    includedAttempts++;
    const group = breakdown.find((item) => item.grade === grade);
    group.attempts++;
    group.credits += credits;
    group.qualityPoints += points;
  }

  return {
    gpa: gradedCredits > 0 ? qualityPoints / gradedCredits : null,
    qualityPoints, gradedCredits, includedAttempts, excludedGrades, missingEvidence,
    breakdown: breakdown.filter((group) => group.attempts > 0),
  };
}
