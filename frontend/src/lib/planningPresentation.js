const labels = {
  CANDIDATE_COMPLETE: 'Provisionally satisfied', CANDIDATE_COURSEWORK_REMAINING: 'Coursework remaining',
  LAB_ALREADY_USED_IN_MAJOR_CHOICE: 'This lab already counts toward another science option in your major.',
  LISTED_CHOICE_COUNT_MET: 'You have already completed enough of the listed options.',
  PHYSICS_I_LECTURE_ALREADY_ENTERED: 'You have already reported a Physics I lecture for this requirement.',
  REVIEW_REQUIRED: 'Needs review', EVIDENCE_MISSING: 'Evidence missing', UNSUPPORTED: 'Needs review',
  MET: 'Met by reported evidence', UNMET: 'Condition not met', UNKNOWN: 'Uncertain',
  MISSING_REPORTED_GRADE: 'A required grade has not been reported.',
  REPEATED_COURSE_EVIDENCE_CONFLICT: 'Reported attempts contain conflicting grade evidence.',
  UNSUPPORTED_REPORTED_GRADE: 'This grade format needs review.',
  GRADE_BELOW_MINIMUM: 'The reported grade is below the required minimum.',
  EQUIVALENCY_UNVERIFIED: 'Course equivalency needs review.',
  CONCURRENT_ENROLLMENT_EVIDENCE_MISSING: 'Same-semester enrollment has not been evaluated.',
  RULE_DATA_COMPLETENESS_UNVERIFIED: 'Complete rule evidence could not be established.',
  NO_RULES_OR_INVALID_RULE_LIST: 'No usable rule evidence was supplied.',
  ALL_SUPPLIED_RULES_MET: 'Reported coursework meets the supplied rules.',
  REQUIRED_RULE_UNMET: 'At least one supplied rule is not met.',
  REQUIRED_RULE_UNKNOWN: 'The supplied evidence cannot resolve all requirements.',
};
export function explain(value) {
  if (value == null) return 'Not supplied';
  const code = typeof value === 'object' ? value.code : value;
  if (typeof code !== 'string') return 'Additional review needed';
  return labels[code] ?? code.replaceAll('_', ' ').toLowerCase();
}
export function choiceProgressText(option) {
  if (option.candidate_exclusion_reason) {
    return `Option excluded. ${explain(option.candidate_exclusion_reason)}`;
  }
  if (option.fully_entered) return 'All courses reported completed.';
  return `Still needed for this option: ${option.missing_codes_to_complete_option.join(', ') || 'Needs review'}`;
}
export function requirementGroups(requirements) {
  const groups = new Map();
  for (const item of requirements) {
    const name = item.section_path?.[0] || 'Other requirements';
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(item);
  }
  return [...groups.entries()];
}
export function safeSourceUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
