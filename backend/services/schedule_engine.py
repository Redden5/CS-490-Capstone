"""Read-only course schedule planning for a target university term.

Planning output is NEVER registration clearance or a verified degree audit.
Works with the supplied v1/v2 handoff's explicit requirements. v2.1 Core II
candidate expansion requires a separate, approved integration.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal, InvalidOperation
from typing import Any

# The Supabase client is supplied by the FastAPI dependency; no import-time
# connection or environment lookup takes place here.

PAGE_SIZE = 1000
HARD_CREDIT_LIMIT = Decimal("21")


def _read_all(table: str, columns: str, *, client: Any,
              filters: list[tuple[str, str, Any]] | None = None,
              order_by: str) -> list[dict]:
    """Read every paginated matching row (don't silently truncate at 1000)."""
    rows: list[dict] = []
    while True:
        query = client.table(table).select(columns)
        for operation, field, value in (filters or []):
            if operation == "eq":
                query = query.eq(field, value)
            elif operation == "in":
                query = query.in_(field, value)
            else:
                raise ValueError(f"Unsupported query operation: {operation}")
        query = query.order(order_by).range(len(rows), len(rows) + PAGE_SIZE - 1)
        page = query.execute().data
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
    return rows


def get_missing_required_course(handoff: dict) -> set[str]:
    """Codes still required by explicit REQUIRED_COURSE rules, not all choices."""
    missing: set[str] = set()
    for requirement in handoff["requirements"]:
        if (requirement.get("kind") == "REQUIRED_COURSE"
                and requirement.get("planning_disposition") == "CANDIDATE_COURSEWORK_REMAINING"):
            missing.update(requirement.get("remaining_required_course_codes") or [])
    return missing - set(handoff.get("completed_course_codes_entered") or [])


def get_course_ids(course_codes: set[str], *, client: Any) -> list[dict]:
    if not course_codes:
        return []
    return _read_all("course", "course_id,course_code,min_credit_hours,max_credit_hours,active_status",
                     client=client, filters=[("in", "course_code", sorted(course_codes))], order_by="course_id")


def get_available_sections(course_codes: set[str], term_id: int, *, client: Any) -> list[dict]:
    """Get all offered sections. Availability is assessed separately."""
    courses = get_course_ids(course_codes, client=client)
    return _sections_for_ids([c["course_id"] for c in courses], term_id, client=client)


def _sections_for_ids(course_ids: list[int], term_id: int, *, client: Any) -> list[dict]:
    if not course_ids:
        return []
    return _read_all("coursesection",
                     "section_id,course_id,term_id,crn,section_number,section_status,capacity,enrolled,delivery_mode",
                     client=client, filters=[("in", "course_id", course_ids), ("eq", "term_id", term_id)],
                     order_by="section_id")


def get_section_meetings(section_ids: list[int], *, client: Any) -> list[dict]:
    if not section_ids:
        return []
    return _read_all("sectionmeeting",
                     "meeting_id,section_id,day_of_week,start_time,end_time,meeting_type",
                     client=client, filters=[("in", "section_id", section_ids)], order_by="meeting_id")


def group_sections_by_course(course_codes: set[str], term_id: int,
                             *, client: Any, catalog_courses: list[dict] | None = None) -> dict[str, list[dict]]:
    """Retrieve sections and attach each section's meetings without duplicate course reads."""
    grouped: dict[str, list[dict]] = {code: [] for code in sorted(course_codes)}
    courses = catalog_courses if catalog_courses is not None else get_course_ids(course_codes, client=client)
    id_to_code = {course["course_id"]: course["course_code"] for course in courses}
    sections = _sections_for_ids(list(id_to_code), term_id, client=client)
    meetings_by_id = {section["section_id"]: [] for section in sections}
    for meeting in get_section_meetings(list(meetings_by_id), client=client):
        meetings_by_id[meeting["section_id"]].append(meeting)
    for section in sections:
        section["meetings"] = meetings_by_id[section["section_id"]]
        grouped[id_to_code[section["course_id"]]].append(section)
    return grouped


def _as_time(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def _valid_meeting(meeting: dict) -> bool:
    start, end = _as_time(meeting.get("start_time")), _as_time(meeting.get("end_time"))
    return (isinstance(meeting.get("day_of_week"), int)
            and start is not None and end is not None and start < end)


def meeting_conflict(meeting_a: dict, meeting_b: dict) -> bool:
    """Known overlap only. Invalid/unknown meetings are handled as tentative elsewhere."""
    if not _valid_meeting(meeting_a) or not _valid_meeting(meeting_b):
        return False
    if meeting_a["day_of_week"] != meeting_b["day_of_week"]:
        return False
    return (_as_time(meeting_a["start_time"]) < _as_time(meeting_b["end_time"])
            and _as_time(meeting_b["start_time"]) < _as_time(meeting_a["end_time"]))


def sections_conflict(section_a: dict, section_b: dict) -> bool:
    """True if at least one KNOWN meeting pair overlaps."""
    return any(meeting_conflict(a, b) for a in section_a.get("meetings", [])
               for b in section_b.get("meetings", []))


def _meeting_uncertain(section: dict) -> bool:
    meetings = section.get("meetings")
    return not isinstance(meetings, list) or not meetings or any(not _valid_meeting(m) for m in meetings)


def _section_issues(section: dict) -> tuple[list[str], list[str]]:
    """Return (hard blocks, tentativeness reasons) for section evidence."""
    blocked: list[str] = []
    tentative: list[str] = []
    status = (section.get("section_status") or "UNKNOWN").upper()
    if status in {"CLOSED", "CANCELLED", "CANCELED", "FULL"}:
        blocked.append(f"SECTION_{status}")
    elif status not in {"OPEN", "AVAILABLE"}:
        tentative.append("SECTION_STATUS_UNKNOWN")
    cap, enrolled = section.get("capacity"), section.get("enrolled")
    if cap is not None and enrolled is not None:
        if enrolled >= cap:
            blocked.append("NO_SEATS_REPORTED")
    else:
        tentative.append("SEAT_AVAILABILITY_UNKNOWN")
    if _meeting_uncertain(section):
        tentative.append("MEETING_TIMES_INCOMPLETE_OR_TBA")
    return blocked, tentative


def _credits(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result > 0 and result.is_finite() else None


def course_credit_values(courses: list[dict], overrides: dict[str, Any] | None = None) -> tuple[dict[str, Decimal], dict[str, str]]:
    """For variable-credit courses, require an explicit credit choice within the catalog range."""
    overrides = overrides or {}
    credits: dict[str, Decimal] = {}
    problems: dict[str, str] = {}
    for course in courses:
        code = course["course_code"]
        low, high = _credits(course.get("min_credit_hours")), _credits(course.get("max_credit_hours"))
        if course.get("active_status") is False:
            problems[code] = "COURSE_INACTIVE"
        elif low is None or high is None or low > high:
            problems[code] = "COURSE_CREDITS_UNKNOWN"
        elif code in overrides:
            chosen = _credits(overrides[code])
            if chosen is None or not low <= chosen <= high:
                problems[code] = "INVALID_CREDIT_OVERRIDE"
            else:
                credits[code] = chosen
        elif low == high:
            credits[code] = low
        else:
            problems[code] = "VARIABLE_CREDITS_CHOICE_REQUIRED"
    return credits, problems


def _normalized_credit_map(values: dict[str, Any]) -> dict[str, Decimal]:
    """Accept credit maps from Pydantic, JSON responses, or Decimal-based callers."""
    return {code: parsed for code, raw in values.items()
            if (parsed := _credits(raw)) is not None}


def read_explicit_plan(handoff: dict) -> dict:
    """Read v1/v2 explicit requirements conservatively. No automatic Core II expansion."""
    version = str(handoff.get("schema_version", ""))
    if version not in {"1.0.0", "2.0.0", "2.1.0", "2.2.0"}:
        raise ValueError(f"Unsupported degree handoff schema_version: {version}")
    required = get_missing_required_course(handoff)
    completed = set(handoff.get("completed_course_codes_entered") or [])
    warnings: list[str] = []
    groups: list[dict] = []

    for requirement in handoff["requirements"]:
        kind = requirement.get("kind")
        if kind == "CHOOSE_N" and requirement.get("planning_disposition") != "CANDIDATE_COMPLETE":
            remaining = (requirement.get("choice_progress") or {}).get("choices_remaining_candidate")
            flags = requirement.get("remaining_policy_review_flags") or []
            options = requirement.get("course_options") or []
            # Only safe-to-enumerate single-course options are eligible for automatic choice.
            valid_codes = sorted({option["course_codes"][0]
                                  for option in options
                                  if option.get("kind") == "SINGLE_COURSE"
                                  and len(option.get("course_codes") or []) == 1
                                  and not option.get("fully_entered")
                                  and not option.get("review_flags")
                                  and option["course_codes"][0] not in completed})
            if (isinstance(remaining, int) and remaining > 0 and not flags
                    and options and all(o.get("kind") == "SINGLE_COURSE" for o in options)
                    and valid_codes):
                groups.append({"requirement_id": requirement["rule_id"], "course_codes": valid_codes,
                               "max_courses_this_term": remaining})
            else:
                warnings.append(f"CHOICE_GROUP_REQUIRES_REVIEW:{requirement.get('rule_id')}")
        elif kind == "CREDIT_REQUIREMENT_DRAFT" and requirement.get("planning_disposition") != "CANDIDATE_COMPLETE":
            warnings.append(f"FLEXIBLE_CREDIT_REQUIREMENT_NOT_SCHEDULED:{requirement.get('rule_id')}")

    # A separate teammate agreement is required before using v2.1 Core II memberships.
    if version in {"2.1.0", "2.2.0"} and handoff.get("flexible_candidates"):
        warnings.append("V2_1_CORE_II_CANDIDATES_AWAIT_SCHEDULER_INTEGRATION_APPROVAL")

    eligibility = handoff.get("course_eligibility") or {}
    evaluated = eligibility.get("evaluation_status") in {"COMPLETED", "PARTIAL"}
    statuses = {c["course_code"]: c["status"] for c in eligibility.get("courses", [])}
    if not evaluated:
        statuses = {}  # NEVER trust old/partial data if batch wasn't evaluated.

    return {"required_courses": sorted(required), "choice_groups": groups,
            "eligibility_statuses": statuses, "warnings": warnings}


def _eligibility_issues(code: str, statuses: dict[str, str]) -> tuple[list[str], list[str]]:
    status = statuses.get(code, "UNKNOWN")
    if status == "UNMET":
        return ["PREREQUISITES_UNMET"], []
    if status == "MET":
        return [], []  # Reported prerequisite evidence only, never registration permission.
    return [], ["PREREQUISITES_UNKNOWN"]


def _inspect_selection(selected: dict[str, dict], credits: dict[str, Decimal],
                       statuses: dict[str, str], max_credits: Decimal) -> dict:
    total = sum((credits[code] for code in selected), Decimal(0))
    blocked: list[str] = []
    tentative: list[str] = []
    if total > max_credits:
        blocked.append("CREDIT_LIMIT_EXCEEDED")
    for code, section in selected.items():
        hard, uncertain = _section_issues(section)
        e_hard, e_uncertain = _eligibility_issues(code, statuses)
        blocked += [f"{code}:{reason}" for reason in hard + e_hard]
        tentative += [f"{code}:{reason}" for reason in uncertain + e_uncertain]
    chosen = list(selected.values())
    for i, first in enumerate(chosen):
        for second in chosen[i + 1:]:
            if sections_conflict(first, second):
                blocked.append(f"TIME_CONFLICT:{first['section_id']}:{second['section_id']}")
            elif _meeting_uncertain(first) or _meeting_uncertain(second):
                tentative.append(f"UNVERIFIED_TIME_COMPATIBILITY:{first['section_id']}:{second['section_id']}")
    return {"blocked": sorted(set(blocked)), "warnings": sorted(set(tentative)),
            "total_credits": float(total),
            "status": "TENTATIVE" if tentative else "CHECKS_PASSED_NOT_REGISTRATION_CLEARANCE"}


def generate_schedule_options(grouped_sections: dict[str, list[dict]],
                              credits: dict[str, Decimal], required_courses: set[str],
                              *, choice_groups: list[dict] | None = None,
                              eligibility_statuses: dict[str, str] | None = None,
                              target_credits: float = 15, max_credits: float = 21,
                              administrator_approved: bool = False,
                              max_results: int = 5, max_search_nodes: int = 100000,
                              locked_sections: dict[str, int] | None = None) -> dict:
    """Bounded backtracking: prioritizes required courses; may defer those that don't fit.

    Locked sections are pinned during generation (useful for user 'nit-picking').
    Only explicit safe SINGLE_COURSE choice groups are used automatically.
    """
    cap, target = _credits(max_credits), _credits(target_credits)
    if cap is None or target is None:
        raise ValueError("Credit limits and target must be positive")
    if cap > HARD_CREDIT_LIMIT and not administrator_approved:
        raise ValueError("More than 21 credits requires administrator approval")
    if max_results < 1 or max_search_nodes < 1:
        raise ValueError("max_results and max_search_nodes must be positive")
    statuses = eligibility_statuses or {}
    credits = _normalized_credit_map(credits)
    locked_sections = locked_sections or {}
    choice_groups = choice_groups or []
    choices_by_code: dict[str, list[str]] = {}
    groups_limits: dict[str, int] = {}
    for group in choice_groups:
        gid = group["requirement_id"]
        groups_limits[gid] = group["max_courses_this_term"]
        for code in group["course_codes"]:
            # A required course remains required regardless of choice-group membership;
            # do not silently count it toward an additional choice requirement.
            if code not in required_courses:
                choices_by_code.setdefault(code, []).append(gid)
    codes = sorted(set(required_courses) | set(choices_by_code),
                   key=lambda code: (code not in required_courses, code))
    # Reject requested locks if not among the candidates (don't silently drop them).
    if set(locked_sections) - set(codes):
        raise ValueError("Locked section contains a course outside this candidate pool")
    selectable: dict[str, list[dict]] = {}
    exclusions: dict[str, str] = {}
    for code in codes:
        if code not in credits:
            exclusions[code] = "COURSE_CREDITS_UNKNOWN_OR_VARIABLE"
            selectable[code] = []
            continue
        if statuses.get(code) == "UNMET":
            exclusions[code] = "PREREQUISITES_UNMET"
            selectable[code] = []
            continue
        sections = []
        for section in grouped_sections.get(code, []):
            block, _ = _section_issues(section)
            if not block and (code not in locked_sections
                              or section["section_id"] == locked_sections[code]):
                sections.append(section)
        selectable[code] = sorted(sections, key=lambda s: s["section_id"])
        if not sections:
            exclusions[code] = "NO_USABLE_SECTIONS_OR_LOCK_UNAVAILABLE"
    if any(not selectable[code] for code in locked_sections):
        return {"options": [], "excluded_courses": exclusions,
                "search_truncated": False, "reason": "LOCKED_SECTION_UNAVAILABLE"}

    solutions: list[dict] = []
    nodes = 0
    truncated = False
    selected: dict[str, dict] = {}
    group_counts = {gid: 0 for gid in groups_limits}

    def visit(position: int, current_credits: Decimal) -> None:
        nonlocal nodes, truncated
        if nodes >= max_search_nodes:
            truncated = True
            return
        nodes += 1
        if position == len(codes):
            if not selected:
                return
            inspection = _inspect_selection(selected, credits, statuses, cap)
            if inspection["blocked"]:
                return
            mandatory_count = len(set(selected) & required_courses)
            mandatory_credits = sum((credits[c] for c in selected if c in required_courses), Decimal(0))
            solutions.append({"sections": dict(selected), "total_credits": inspection["total_credits"],
                              "status": inspection["status"], "warnings": inspection["warnings"],
                              "unscheduled_required": sorted(required_courses - set(selected)),
                              "mandatory_courses_scheduled": mandatory_count,
                              "mandatory_credits_scheduled": float(mandatory_credits)})
            return
        code = codes[position]
        gids = choices_by_code.get(code, [])
        group_ok = all(group_counts[gid] < groups_limits[gid] for gid in gids)
        # Never add a duplicate course, never exceed the cap, and reuse known conflict checks.
        if group_ok and current_credits + credits.get(code, Decimal("9999")) <= cap:
            for section in selectable[code]:
                if any(sections_conflict(section, current) for current in selected.values()):
                    continue
                selected[code] = section
                for gid in gids:
                    group_counts[gid] += 1
                visit(position + 1, current_credits + credits[code])
                for gid in gids:
                    group_counts[gid] -= 1
                del selected[code]
                if truncated:
                    return
        # Mandatory means 'required for degree', not 'must all fit this term'.
        if code not in locked_sections:
            visit(position + 1, current_credits)

    visit(0, Decimal(0))
    # Deduplicate equivalent selections, order by target closeness, required count,
    # uncertainty, and deterministic CRN/ID tie-breaking.
    unique: dict[tuple, dict] = {}
    for solution in solutions:
        identity = tuple(sorted((code, s["section_id"]) for code, s in solution["sections"].items()))
        unique[identity] = solution
    ranked = sorted(unique.values(), key=lambda item: (
        -min(Decimal(str(item["mandatory_credits_scheduled"])), target),
        abs(Decimal(str(item["total_credits"])) - target),
        -item["mandatory_courses_scheduled"],
        item["status"] == "TENTATIVE",
        len(item["warnings"]),
        tuple(sorted((code, s["section_id"]) for code, s in item["sections"].items()))))
    return {"options": ranked[:max_results], "excluded_courses": exclusions,
            "search_truncated": truncated,
            "reason": None if ranked else "NO_COMPATIBLE_SCHEDULE_FOUND"}


def replace_section(schedule: dict[str, dict], course_code: str, new_section_id: int,
                    grouped_sections: dict[str, list[dict]], credits: dict[str, Decimal],
                    *, eligibility_statuses: dict[str, str] | None = None,
                    max_credits: float = 21, administrator_approved: bool = False) -> dict:
    """Swap just one section of the same course; preserve every other section."""
    if course_code not in schedule:
        return {"ok": False, "errors": ["COURSE_NOT_IN_SCHEDULE"]}
    replacement = next((s for s in grouped_sections.get(course_code, [])
                        if s["section_id"] == new_section_id), None)
    if replacement is None:
        return {"ok": False, "errors": ["SECTION_NOT_AVAILABLE_FOR_COURSE_AND_TERM"]}
    credits = _normalized_credit_map(credits)
    proposed = dict(schedule)
    proposed[course_code] = replacement
    if set(proposed) - set(credits):
        return {"ok": False, "errors": ["COURSE_CREDITS_UNAVAILABLE"]}
    cap = _credits(max_credits)
    if cap is None or (cap > HARD_CREDIT_LIMIT and not administrator_approved):
        return {"ok": False, "errors": ["INVALID_CREDIT_LIMIT_OR_ADMIN_APPROVAL_REQUIRED"]}
    result = _inspect_selection(proposed, credits, eligibility_statuses or {}, cap)
    if result["blocked"]:
        return {"ok": False, "errors": result["blocked"]}
    return {"ok": True, "sections": proposed, **result}


def replace_flexible_course(schedule: dict[str, dict], old_code: str, new_code: str,
                            new_section_id: int, grouped_sections: dict[str, list[dict]],
                            credits: dict[str, Decimal], required_courses: set[str],
                            choice_groups: list[dict], *,
                            eligibility_statuses: dict[str, str] | None = None,
                            max_credits: float = 21, administrator_approved: bool = False) -> dict:
    """Swap two alternatives belonging to the same reviewed-safe SINGLE_COURSE group."""
    if old_code in required_courses:
        return {"ok": False, "errors": ["MANDATORY_COURSE_CANNOT_BE_REPLACED"]}
    if old_code not in schedule or (new_code in schedule and new_code != old_code):
        return {"ok": False, "errors": ["COURSE_NOT_IN_SCHEDULE_OR_ALREADY_SELECTED"]}
    if not any(old_code in g["course_codes"] and new_code in g["course_codes"]
               for g in choice_groups):
        return {"ok": False, "errors": ["NOT_AN_APPROVED_ALTERNATIVE_IN_SAME_GROUP"]}
    if new_code in required_courses:
        return {"ok": False, "errors": ["REPLACEMENT_IS_ALSO_MANDATORY_REQUIRES_DEGREE_REVIEW"]}
    credits = _normalized_credit_map(credits)
    replacement = next((s for s in grouped_sections.get(new_code, [])
                        if s["section_id"] == new_section_id), None)
    if replacement is None or new_code not in credits:
        return {"ok": False, "errors": ["REPLACEMENT_SECTION_OR_CREDITS_UNAVAILABLE"]}
    proposed = dict(schedule)
    del proposed[old_code]
    proposed[new_code] = replacement
    if set(proposed) - set(credits):
        return {"ok": False, "errors": ["COURSE_CREDITS_UNAVAILABLE"]}
    cap = _credits(max_credits)
    if cap is None or (cap > HARD_CREDIT_LIMIT and not administrator_approved):
        return {"ok": False, "errors": ["INVALID_CREDIT_LIMIT_OR_ADMIN_APPROVAL_REQUIRED"]}
    result = _inspect_selection(proposed, credits, eligibility_statuses or {}, cap)
    if result["blocked"]:
        return {"ok": False, "errors": result["blocked"]}
    return {"ok": True, "sections": proposed, **result}


def generate_from_handoff(handoff: dict, term_id: int, *, client: Any,
                          target_credits: float = 15,
                          max_credits: float = 21, administrator_approved: bool = False,
                          credit_overrides: dict[str, Any] | None = None,
                          max_results: int = 5,
                          max_search_nodes: int = 15000) -> dict:
    """High-level read-only entry point for the FastAPI route to call later."""
    plan = read_explicit_plan(handoff)
    course_codes = set(plan["required_courses"])
    for group in plan["choice_groups"]:
        course_codes.update(group["course_codes"])
    if len(course_codes) > 200:
        raise ValueError("Too many explicit candidate courses for one scheduling request")
    catalog = get_course_ids(course_codes, client=client)
    credits, credit_problems = course_credit_values(catalog, credit_overrides)
    grouped = group_sections_by_course(course_codes, term_id, client=client, catalog_courses=catalog)
    result = generate_schedule_options(
        grouped, credits, set(plan["required_courses"]),
        choice_groups=plan["choice_groups"],
        eligibility_statuses=plan["eligibility_statuses"],
        target_credits=target_credits, max_credits=max_credits,
        administrator_approved=administrator_approved, max_results=max_results,
        max_search_nodes=max_search_nodes)
    result.update({"term_id": term_id, "required_courses": plan["required_courses"],
                   "choice_groups": plan["choice_groups"], "course_credit_hours": {k: float(v) for k, v in credits.items()},
                   "credit_problems": credit_problems,
                   "handoff_warnings": plan["warnings"],
                   "planning_disclaimer": "Tentative course planning; not degree verification or registration clearance."})
    return result
