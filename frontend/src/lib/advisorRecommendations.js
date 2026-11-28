function courseCode(value) {
  return value.trim().toUpperCase().replace(/\s+/g, ' ');
}

export function advisorRecommendations(handoff, plannedCourses = []) {
  if (!handoff) return [];
  const excluded = new Set([
    ...(handoff.completed_course_codes_entered ?? []),
    ...plannedCourses.map((course) => course.course_code),
  ].map(courseCode));
  const eligibility = new Map((handoff.course_eligibility?.courses ?? [])
    .map((course) => [course.course_code, course]));
  const flexible = new Map((handoff.flexible_candidates?.requirements ?? [])
    .map((requirement) => [requirement.requirement_id, requirement]));
  const candidates = new Map();

  function add(code, requirement, priority, bundle = []) {
    const evidence = eligibility.get(code);
    if (excluded.has(courseCode(code)) || evidence?.status === 'UNMET') return;
    const reason = requirement.kind === 'REQUIRED_COURSE'
      ? `Remaining required course for ${requirement.name}.`
      : `An option for ${requirement.name}.`;
    const companions = bundle.filter((member) => member !== code);
    const bundleNote = companions.length ? ` Complete this option with ${companions.join(' + ')}.` : '';
    const rank = priority + (evidence?.status === 'MET' ? 0 : 1);
    const existing = candidates.get(code);
    if (!existing || rank < existing.rank) candidates.set(code, {
      code, reason: reason + bundleNote, status: evidence?.status ?? 'UNKNOWN', rank,
    });
  }

  for (const requirement of handoff.requirements ?? []) {
    if (requirement.candidate_progress_status !== 'CANDIDATE_NOT_MET' ||
        requirement.remaining_policy_review_flags?.length) continue;
    if (requirement.kind === 'REQUIRED_COURSE') {
      for (const code of requirement.remaining_required_course_codes ?? []) add(code, requirement, 0);
    }
    if (requirement.kind === 'CHOOSE_N' && requirement.choice_progress?.choices_remaining_candidate > 0) {
      for (const option of requirement.course_options ?? []) {
        if (option.fully_entered || option.candidate_exclusion_reason || option.review_flags?.length) continue;
        for (const code of option.missing_codes_to_complete_option ?? []) {
          add(code, requirement, 2, option.course_codes);
        }
      }
    }
    if (requirement.kind === 'CREDIT_REQUIREMENT_DRAFT') {
      for (const candidate of flexible.get(requirement.rule_id)?.candidates ?? []) {
        add(candidate.course_code, requirement, 4);
      }
    }
  }
  return [...candidates.values()].sort((left, right) =>
    left.rank - right.rank || left.code.localeCompare(right.code));
}

export function draftCredits(courses) {
  const low = courses.reduce((sum, course) => sum + Number(course.min_credit_hours), 0);
  const high = courses.reduce((sum, course) => sum + Number(course.max_credit_hours), 0);
  return low === high ? String(low) : `${low}–${high}`;
}
