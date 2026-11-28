// Presentation arithmetic only; all requirement decisions remain service-owned.
export function degreeProgress(record, audit) {
  const target = record.input.program?.required_credits;
  if (record.loading || record.error || !record.revision || !Number.isFinite(target) || target <= 0) return null;
  const evidence = audit?.distinct_course_credit_evidence;
  const credits = record.input.rows.length ? evidence?.known_credit_subtotal : 0;
  if (!Number.isFinite(credits) || credits < 0) return null;
  return { credits, target, filled: Math.min(credits, target), fraction: Math.min(credits / target, 1),
    unknownCount: evidence?.courses_without_known_credits?.length ?? 0 };
}

export function requirementsMet(audit) {
  const count = audit?.summary?.candidate_met_rule_occurrences;
  return Number.isInteger(count) && count >= 0 ? count : null;
}

export function semesterPreview(semesters, preferredTermId) {
  if (!semesters.length) return null;
  const index = Math.max(0, semesters.findIndex((plan) => preferredTermId != null && plan.term_id === preferredTermId));
  const plan = semesters[index];
  let minimum = 0, maximum = 0;
  const known = plan.courses.every((course) => {
    const low = course.min_credit_hours, high = course.max_credit_hours;
    if (!Number.isFinite(low) || !Number.isFinite(high) || low < 0 || high < low) return false;
    minimum += low; maximum += high; return true;
  });
  return { plan, index, minimum: known ? minimum : null, maximum: known ? maximum : null,
    label: plan.name || plan.plan_name || `Plan ${index + 1} · ${String(plan.id).slice(0, 8)}`,
    courses: plan.courses.slice(0, 3), additionalCount: Math.max(0, plan.courses.length - 3) };
}
