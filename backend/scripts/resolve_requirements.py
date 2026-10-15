"""Convert parsed Marshall University degree requirements into a reviewable draft.
This stage DOES NOT approve degree rules or write to Supabase.
Usage from the repository root (PowerShell):
    python -m backend.scripts.resolve_requirements --input backend/scripts/data/degree_requirements/parsed/computer_science_bs_parsed.json
Output goes to backend/scripts/data/degree_requirements/resolved/ by default.
Design:
  * The resolver is program-agnostic: it uses the structured ``kind`` and
    ``section_path`` fields, not names of particular programs or courses.
  * Straightforward required courses become structurally READY rules.
  * Credit requirements stay NEEDS_REVIEW until their eligibility and
    institutional policies have been verified.
  * Catalog-declared choice credit totals remain unconfirmed unless an operator
    explicitly reviews and confirms a source rule's credit target.
  * Confirmation preserves unrelated option/policy warnings and NEVER approves
    a degree template for seeding or graduation audits.
  * Compound choices are represented by child ALL nodes for each option.
  * Optional shared Core II matching recognizes safe, institution-wide labels
    from an unversioned official listing while retaining review flags.
  * Every output is blocked from automatic seeding at this stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ALLOWED_OPTION_KINDS = {"SINGLE_COURSE", "ALL_OF"}
# Shared Marshall-wide labels (NOT degree-specific rules). Matching is exact
# after case/whitespace normalization. Never infer from loose keywords.
CORE_II_LABELS = {
    "core ii humanities": ("Core II Humanities", False),
    "core ii social studies": ("Core II Social Sciences", False),
    "core ii social sciences": ("Core II Social Sciences", False),
    "core ii fine arts": ("Core II Fine Arts", False),
    "core ii mathematics": ("Core II Mathematics", False),
    "core ii communication studies": ("Core II Communication Studies", False),
    "core ii communications": ("Core II Communication Studies", False),
    "composition core ii": ("Composition Core II", False),
    "core ii composition": ("Composition Core II", False),
    "core ii natural sciences": ("Core II Natural Sciences", False),
    "sci w/lab core ii physical/natural science": ("Core II Natural Sciences", True),
}

# Exact, shared university-wide requirement labels. These are *candidate*
# mappings: the audit must still verify course memberships against Supabase
# for the student's selected catalog year. Do not infer attributes from loose
# text such as "science", "humanities", or "international" in a title.
# The boolean means a lab condition remains to be checked separately.
CATALOG_ATTRIBUTE_LABELS: dict[str, tuple[tuple[str, ...], bool]] = {
    "critical thinking course": (("Critical Thinking",), False),
    "critical thinking": (("Critical Thinking",), False),
    "core ii humanities": (("Core II Humanities",), False),
    "core ii social studies": (("Core II Social Sciences",), False),
    "core ii social sciences": (("Core II Social Sciences",), False),
    "core ii fine arts": (("Core II Fine Arts",), False),
    "core ii natural sciences": (("Core II Natural Sciences",), False),
    "sci w/lab core ii physical/natural science": (("Core II Natural Sciences",), True),
    "multicultural or international": (("Multicultural", "International"), False),
    "multicultural/international": (("Multicultural", "International"), False),
    "multicultural or international course": (("Multicultural", "International"), False),
}


def apply_catalog_attribute_candidates(rule_nodes: list[dict[str, Any]]) -> dict[str, int]:
    """Annotate exact-label candidates; never certify attribute eligibility.

    Source-proven catalog-year memberships will be checked by the read-only
    degree audit against courseattribute. A match here establishes only which
    *kind* of classification the requirement is asking for.
    """
    matched = lab_pending = 0
    for node in rule_nodes:
        if node["source_kind"] != "CREDIT_REQUIREMENT_DRAFT":
            continue
        candidate = CATALOG_ATTRIBUTE_LABELS.get(normalize_heading(node["name"]))
        if candidate is None:
            continue
        attributes, lab_required = candidate
        if not positive_number(node["minimum_credits"]):
            raise ResolutionError(f"{node['name']!r} lacks a positive credit minimum")

        node["attribute_names"] = list(attributes)
        node["metadata"]["attribute_resolution"] = {
            "scope": "catalog_year_scoped_supabase_lookup_required",
            "attribute_match_mode": "ANY",  # Multicultural OR International
            "catalog_year_applicability_verified": False,
            "additional_lab_eligibility_unverified": lab_required,
        }
        # Mapping identifies a category; it does not resolve the university's
        # credit, grade, lab, catalog-policy, or overlap requirements.
        flags = [f for f in node["review_flags"] if f not in {
            "credit_requirement_eligibility_unmapped",
            "eligibility_requires_explicit_resolution",
            "catalog_year_applicability_unverified",
        }]
        flags.append("catalog_year_course_eligibility_lookup_required")
        if lab_required:
            flags.append("lab_eligibility_unverified")
            lab_pending += 1
        node["review_flags"] = list(dict.fromkeys(flags))
        node["status"] = "NEEDS_REVIEW"
        matched += 1
    return {"matched": matched, "partial_lab": lab_pending}

def list_current_core_ii_attributes(listing: dict[str, Any]) -> set[str]:
    if not isinstance(listing, dict):
        raise ResolutionError("Core II source must be a JSON object")
    if listing.get("stage") != "unverified_current_core_ii_listing":
        raise ResolutionError("Expected the current, unverified Core II listing")
    if listing.get("catalog_year") is not None:
        raise ResolutionError("Unexpected catalog year on current Core II listing; review source handling")
    if not isinstance(listing.get("source_url"), str) or not listing["source_url"].startswith("https://"):
        raise ResolutionError("Core II listing must include a valid HTTPS source URL")
    categories = listing.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ResolutionError("Core II listing has no categories")
    names: set[str] = set()
    for category in categories:
        if not isinstance(category, dict):
            raise ResolutionError("Malformed Core II category")
        attribute = category.get("attribute_name")
        if not isinstance(attribute, str) or not attribute.strip():
            raise ResolutionError("Core II category missing attribute_name")
        if attribute in names:
            raise ResolutionError(f"Repeated Core II attribute: {attribute}")
        courses = category.get("courses")
        if not isinstance(courses, list) or not courses:
            raise ResolutionError(f"Core II attribute {attribute!r} has no courses")
        names.add(attribute)
    return names


def apply_current_core_ii_matches(
    rule_nodes: list[dict[str, Any]], listing: dict[str, Any]
) -> dict[str, int]:
    """Attach current-listing evidence, never degree-audit approval."""
    available = list_current_core_ii_attributes(listing)
    matched = partial = 0
    for node in rule_nodes:
        if node["source_kind"] != "CREDIT_REQUIREMENT_DRAFT":
            continue
        match = CORE_II_LABELS.get(normalize_heading(node["name"]))
        if not match:
            continue
        attribute_name, lab_condition = match
        if attribute_name not in available:
            raise ResolutionError(
                f"{node['name']!r} maps to {attribute_name!r}, absent from Core II listing"
            )
        if not positive_number(node["minimum_credits"]):
            raise ResolutionError(f"{node['name']!r} has no positive credit threshold")
        node["attribute_names"] = [attribute_name]
        node["metadata"]["attribute_resolution"] = {
            "scope": "current_official_course_listing_only",
            "attribute_match_mode": "ANY",
            "source_url": listing["source_url"],
            "observed_at_utc": listing.get("scraped_at_utc"),
            "catalog_year_applicability_verified": False,
            "additional_lab_eligibility_unverified": lab_condition,
        }
        flags = [flag for flag in node["review_flags"] if flag not in {
            "credit_requirement_eligibility_unmapped",
            "eligibility_requires_explicit_resolution",
        }]
        flags.append("catalog_year_applicability_unverified")
        if lab_condition:
            flags.append("lab_eligibility_unverified")
            partial += 1
        node["review_flags"] = list(dict.fromkeys(flags))
        node["status"] = "NEEDS_REVIEW"
        matched += 1
    return {"matched": matched, "category_only": matched - partial, "partial_lab": partial}


class ResolutionError(ValueError):
    """The input file is malformed or internally inconsistent."""


def positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def unique_nonblank_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip() not in result:
            result.append(value.strip())
    return result


def distinct_flags(*flag_lists: Any) -> list[str]:
    result: list[str] = []
    for flags in flag_lists:
        for flag in flags or []:
            if flag not in result:
                result.append(flag)
    return result


def normalize_heading(heading: str) -> str:
    return " ".join(heading.casefold().split())


def group_key(path: list[str]) -> str:
    # Stable across runs and safe when two distinct paths have the same label.
    payload = json.dumps(path, ensure_ascii=False, separators=(",", ":"))
    return "group:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_node(
    *,
    node_id: str,
    parent_node_id: str | None,
    name: str,
    source_kind: str,
    db_type: str | None,
    course_codes: list[str] | None = None,
    minimum_courses: int | None = None,
    minimum_credits: int | float | None = None,
    review_flags: list[str] | None = None,
    source: dict[str, Any] | None = None,
    section_path: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    flags = distinct_flags(review_flags)
    if source_kind == "SECTION_GROUP":
        status = "STRUCTURAL"
    elif db_type is None:
        status = "UNSUPPORTED"
    else:
        status = "NEEDS_REVIEW" if flags else "READY"
    return {
        "node_id": node_id,
        "parent_node_id": parent_node_id,
        "name": name,
        "source_kind": source_kind,
        "db_requirement_type_candidate": db_type,
        "minimum_courses": minimum_courses,
        "minimum_credits": minimum_credits,
        "course_codes": course_codes or [],
        "attribute_names": [],  # No guesswork based on similarly named categories.
        "status": status,
        "review_flags": flags,
        "section_path": section_path or [],
        "source": source or {},
        "metadata": metadata or {},
    }


def resolve(
    parsed: dict[str, Any],
    core_ii_listing: dict[str, Any] | None = None,
    confirmed_choice_credit_ids: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(parsed, dict) or not isinstance(parsed.get("requirements"), list):
        raise ResolutionError("Expected a JSON object with a 'requirements' array.")
    if not parsed.get("program_title") or not parsed.get("catalog_year"):
        raise ResolutionError("Missing 'program_title' or 'catalog_year'.")
    input_rules = parsed["requirements"]
    confirmed_ids = set(confirmed_choice_credit_ids or ())
    confirmed_choice_credit_targets = 0
    expected_count = parsed.get("summary", {}).get("interpreted_rules")
    if expected_count is not None and expected_count != len(input_rules):
        raise ResolutionError(
            f"Parser summary says {expected_count} rules but found {len(input_rules)}."
        )
    used_rule_ids: set[str] = set()
    groups: dict[tuple[str, ...], str] = {}
    nodes: list[dict[str, Any]] = []
    original_rule_nodes: list[dict[str, Any]] = []
    compound_option_count = 0
    for idx, rule in enumerate(input_rules, start=1):
        if not isinstance(rule, dict):
            raise ResolutionError(f"Requirement #{idx} is not a JSON object.")
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ResolutionError(f"Requirement #{idx} has no valid rule_id.")
        if rule_id in used_rule_ids:
            raise ResolutionError(f"Duplicate rule_id: {rule_id}")
        used_rule_ids.add(rule_id)
        kind = rule.get("kind")
        if rule_id in confirmed_ids and kind != "CHOOSE_N":
            raise ResolutionError(
                f"Cannot confirm choice credits for {rule_id}: "
                f"source kind is {kind!r}, not CHOOSE_N."
            )
        name = rule.get("name", "")
        if not isinstance(name, str) or not name.strip():
            raise ResolutionError(f"Rule {rule_id} lacks a name.")
        path = rule.get("section_path", [])
        if not isinstance(path, list) or any(not isinstance(x, str) for x in path):
            raise ResolutionError(f"Rule {rule_id} has an invalid section_path.")
        path = [x.strip() for x in path if x.strip()]
        # If an explicit choice group's final section label is the rule's own
        # name, don't create a redundant parent group with the same name.
        group_path = (
            path[:-1]
            if kind == "CHOOSE_N" and path
            and normalize_heading(path[-1]) == normalize_heading(name)
            else path
        )
        parent_id: str | None = None
        for depth in range(1, len(group_path) + 1):
            prefix = tuple(group_path[:depth])
            if prefix not in groups:
                new_id = group_key(list(prefix))
                groups[prefix] = new_id
                nodes.append(
                    make_node(
                        node_id=new_id,
                        parent_node_id=parent_id,
                        name=prefix[-1],
                        source_kind="SECTION_GROUP",
                        db_type="ALL",
                        section_path=list(prefix),
                    )
                )
            parent_id = groups[prefix]
        flags = distinct_flags(rule.get("review_flags"))
        course_codes: list[str] = []
        minimum_courses: int | None = None
        minimum_credits: int | float | None = None
        db_type: str | None = None
        options: list[dict[str, Any]] = []
        child_option_nodes: list[dict[str, Any]] = []
        listed_credits = rule.get("catalog_declared_credits")
        metadata: dict[str, Any] = {
            "catalog_declared_credits": listed_credits,
            "credit_slots": rule.get("credit_slots", []),
            "original_review_flags": rule.get("review_flags", []),
        }
        if kind == "REQUIRED_COURSE":
            db_type = "ALL"
            course_codes = unique_nonblank_strings(rule.get("course_codes", []))
            if not course_codes or len(course_codes) != 1:
                flags = distinct_flags(flags, ["required_course_must_reference_one_exact_course"])
            if rule.get("course_combination") != "SINGLE_COURSE":
                flags = distinct_flags(flags, ["unsupported_required_course_combination"])
        elif kind == "CREDIT_REQUIREMENT_DRAFT":
            db_type = "MIN_CREDITS"
            minimum_credits = rule.get("total_listed_credits")
            if not positive_number(minimum_credits):
                flags = distinct_flags(flags, ["invalid_minimum_credits"])
                minimum_credits = None
            # No attribute/course mapping is inferred merely from a label.
            flags = distinct_flags(flags, ["eligibility_requires_explicit_resolution"])
            metadata["referenced_course_codes"] = rule.get("referenced_course_codes", [])
            metadata["course_patterns"] = rule.get("course_patterns", [])
        elif kind == "CHOOSE_N":
            db_type = "CHOOSE_N"
            minimum_courses = rule.get("minimum_choices")
            if not isinstance(minimum_courses, int) or isinstance(minimum_courses, bool) or minimum_courses <= 0:
                flags = distinct_flags(flags, ["invalid_minimum_choices"])
                minimum_courses = None
            minimum_credits = rule.get("minimum_credits")
            if minimum_credits is not None and not positive_number(minimum_credits):
                flags = distinct_flags(flags, ["invalid_minimum_credits"])
                minimum_credits = None
            if rule_id in confirmed_ids:
                # Human confirmation applies ONLY to the group credit target.
                # Do not promote substitution/overlap/qualification review flags.
                if not positive_number(listed_credits):
                    raise ResolutionError(
                        f"Cannot confirm choice credits for {rule_id}: "
                        "catalog_declared_credits is missing or nonpositive."
                    )
                if not isinstance(rule.get("instruction"), str) or not rule["instruction"].strip():
                    raise ResolutionError(
                        f"Cannot confirm choice credits for {rule_id}: "
                        "selection instruction is missing."
                    )
                if minimum_credits is not None and minimum_credits != listed_credits:
                    raise ResolutionError(
                        f"Cannot confirm choice credits for {rule_id}: "
                        f"parsed minimum {minimum_credits} disagrees with "
                        f"catalog total {listed_credits}."
                    )
                if rule.get("minimum_credits") is not None and not positive_number(rule["minimum_credits"]):
                    raise ResolutionError(
                        f"Cannot confirm choice credits for {rule_id}: "
                        "parsed minimum is invalid."
                    )
                minimum_credits = listed_credits
                flags = [flag for flag in flags if flag != "catalog_credit_threshold_needs_confirmation"]
                metadata["credit_threshold_resolution"] = {
                    "basis": "explicit_confirmation_of_catalog_declared_group_credit_total",
                    "source_rule_id": rule_id,
                    "source_url": parsed.get("source_url"),
                    "catalog_declared_credits": listed_credits,
                    "confirmation_scope": "numeric_choice_group_credit_target_only",
                    "other_policy_and_allocation_rules_verified": False,
                }
                confirmed_choice_credit_targets += 1
            elif minimum_credits is None and positive_number(listed_credits):
                # The group-level catalog number is evidence, but isn't yet
                # an approved minimum-credit threshold for this parsed rule.
                flags = distinct_flags(flags, ["catalog_credit_threshold_needs_confirmation"])
            raw_options = rule.get("options")
            if not isinstance(raw_options, list) or not raw_options:
                flags = distinct_flags(flags, ["choice_options_missing"])
                raw_options = []
            option_kinds = {x.get("kind") for x in raw_options if isinstance(x, dict)}
            compound = "ALL_OF" in option_kinds
            for option_idx, option in enumerate(raw_options, start=1):
                if not isinstance(option, dict):
                    flags = distinct_flags(flags, ["malformed_choice_option"])
                    continue
                option_kind = option.get("kind")
                codes = unique_nonblank_strings(option.get("course_codes", []))
                option_flags = distinct_flags(option.get("review_flags"))
                if option_kind not in ALLOWED_OPTION_KINDS:
                    option_flags = distinct_flags(option_flags, ["unsupported_option_kind"])
                if (option_kind == "SINGLE_COURSE" and len(codes) != 1) or (
                    option_kind == "ALL_OF" and len(codes) < 2
                ):
                    option_flags = distinct_flags(option_flags, ["invalid_option_course_codes"])
                if option_flags:
                    flags = distinct_flags(flags, ["some_options_require_review"])
                options.append({
                    "kind": option_kind,
                    "course_codes": codes,
                    "code_expression": option.get("code_expression"),
                    "description": option.get("description"),
                    "review_flags": option_flags,
                    "source_row": option.get("source_row"),
                    "source_option": option,
                })
                if compound:
                    compound_option_count += 1
                    child_option_nodes.append(
                        make_node(
                            node_id=f"option:{rule_id}:{option_idx}",
                            parent_node_id=f"rule:{rule_id}",
                            name=option.get("code_expression") or " + ".join(codes),
                            source_kind="CHOICE_OPTION",
                            db_type="ALL" if option_kind in ALLOWED_OPTION_KINDS else None,
                            course_codes=codes,
                            review_flags=option_flags,
                            section_path=path,
                            source={"rule_id": rule_id, "source_row": option.get("source_row")},
                            metadata={"option_kind": option_kind, "source_option": option},
                        )
                    )
                elif option_kind == "SINGLE_COURSE":
                    course_codes.extend(codes)
            if compound:
                # Prevent accidentally treating a lecture and its lab as
                # separate electives in the parent requirement's course list.
                course_codes = []
                metadata["compound_option_groups"] = True
                # Distinct groups may share a course. Determining whether two
                # such groups can both count is a policy/allocator decision.
                occurrences = Counter(code for opt in options for code in opt["course_codes"])
                shared = {code: n for code, n in occurrences.items() if n > 1}
                metadata["shared_course_codes_across_options"] = shared
                if shared:
                    flags = distinct_flags(flags, ["shared_courses_across_options_need_allocation_rule"])
                flags = distinct_flags(flags, ["compound_options_require_group_evaluation"])
            else:
                if len(course_codes) != len(set(course_codes)):
                    flags = distinct_flags(flags, ["duplicate_course_options"])
                course_codes = list(dict.fromkeys(course_codes))
            if minimum_courses and minimum_courses > len(options):
                flags = distinct_flags(flags, ["minimum_choices_exceeds_options"])
            metadata["options"] = options
            metadata["instruction"] = rule.get("instruction")
        else:
            flags = distinct_flags(flags, ["unsupported_rule_kind"])
            db_type = None
        # A known reviewer flag never becomes silently approved. READY here
        # means a mechanically representable candidate, not policy approval.
        main_node = make_node(
            node_id=f"rule:{rule_id}",
            parent_node_id=parent_id,
            name=name,
            source_kind=str(kind),
            db_type=db_type,
            course_codes=course_codes,
            minimum_courses=minimum_courses,
            minimum_credits=minimum_credits,
            review_flags=flags,
            section_path=path,
            source={
                "rule_id": rule_id,
                "table_index": rule.get("table_index"),
                "source_rows": rule.get("source_rows", []),
                "source_row_ids": rule.get("source_row_ids", []),
            },
            metadata=metadata,
        )
        nodes.append(main_node)
        original_rule_nodes.append(main_node)
        nodes.extend(child_option_nodes)
    unknown_confirmations = confirmed_ids - used_rule_ids
    if unknown_confirmations:
        raise ResolutionError(
            "Cannot confirm credits for unknown rule ID(s): "
            + ", ".join(sorted(unknown_confirmations))
        )
    # Every foreign key in this draft is an internal stable node ID. The
    # eventual seeder will map node IDs to DB-assigned BIGINT primary keys.
    all_ids = {n["node_id"] for n in nodes}
    for node in nodes:
        if node["parent_node_id"] and node["parent_node_id"] not in all_ids:
            raise ResolutionError(f"Orphan parent: {node['node_id']}")
    mapping_counts = {"matched": 0, "category_only": 0, "partial_lab": 0}
    if core_ii_listing is not None:
        mapping_counts = apply_current_core_ii_matches(original_rule_nodes, core_ii_listing)

    # This supersedes unversioned Core II candidates when the same exact
    # university-wide requirement name has a catalog-scoped classification.
    catalog_mapping_counts = apply_catalog_attribute_candidates(original_rule_nodes)

    by_status = Counter(n["status"] for n in original_rule_nodes)
    review_queue = [
        {
            "rule_id": node["source"]["rule_id"],
            "name": node["name"],
            "status": node["status"],
            "review_flags": node["review_flags"],
            "node_id": node["node_id"],
        }
        for node in original_rule_nodes
        if node["status"] != "READY"
    ]
    return {
        "program_title": parsed["program_title"],
        "catalog_year": parsed["catalog_year"],
        "source_url": parsed.get("source_url"),
        "source_stage": parsed.get("stage"),
        "stage": "resolved_draft_not_approved_for_seeding",
        "approved_for_seeding": False,
        "summary": {
            "source_rules": len(original_rule_nodes),
            "ready_rules": by_status["READY"],
            "needs_review_rules": by_status["NEEDS_REVIEW"],
            "unsupported_rules": by_status["UNSUPPORTED"],
            "section_group_nodes": len(groups),
            "compound_option_nodes": compound_option_count,
            "all_nodes": len(nodes),
            "current_core_ii_mappings": mapping_counts,
            "catalog_attribute_candidate_mappings": catalog_mapping_counts,
            "confirmed_choice_credit_targets": confirmed_choice_credit_targets,
        },
        "nodes": nodes,
        "review_queue": review_queue,
        "source_program_review_flags": parsed.get("program_review_flags", []),
        "source_unlabeled_table_indices": parsed.get("unlabeled_table_indices", []),
        "warnings": [
            "No rule is approved for production degree auditing by this file.",
            "Attribute eligibility and historical catalog-year applicability require verification.",
            "Count credits only once toward overall degree totals, even if rules overlap.",
            "Lecture/lab choices require group-aware allocation and verified equivalents.",
        ],
    }


def default_output_path(input_path: Path) -> Path:
    file_stem = input_path.stem
    if file_stem.endswith("_parsed"):
        file_stem = file_stem[: -len("_parsed")]
    if input_path.parent.name == "parsed":
        directory = input_path.parent.parent / "resolved"
    else:
        directory = input_path.parent / "resolved"
    return directory / f"{file_stem}_resolved.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input parsed requirements JSON")
    parser.add_argument(
        "--core-ii-json", type=Path,
        help="Optional shared, current Core II listing for conservative category matching",
    )
    parser.add_argument(
        "--confirm-choice-credit-total", action="append", default=[], metavar="RULE_ID",
        help=("Explicitly confirm the catalog-declared credit total of one reviewed "
              "CHOOSE_N rule by its source rule_id; repeat for multiple rules."),
    )
    parser.add_argument("--output", type=Path, help="Output file (default: adjacent resolved/ directory)")
    args = parser.parse_args()
    try:
        with args.input.open("r", encoding="utf-8-sig") as handle:
            parsed = json.load(handle)
        core_ii_listing = None
        if args.core_ii_json:
            with args.core_ii_json.open("r", encoding="utf-8-sig") as handle:
                core_ii_listing = json.load(handle)
        confirmed = args.confirm_choice_credit_total
        if len(confirmed) != len(set(confirmed)):
            raise ResolutionError("Duplicate --confirm-choice-credit-total rule IDs")
        result = resolve(parsed, core_ii_listing, confirmed_choice_credit_ids=set(confirmed))
        output_path = args.output or default_output_path(args.input)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, ResolutionError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    stats = result["summary"]
    print(f"Program: {result['program_title']} ({result['catalog_year']})")
    print(f"Explicitly confirmed choice credit targets: {stats['confirmed_choice_credit_targets']}")
    print(f"Source rules: {stats['source_rules']}")
    print(
        f"Rule status: READY={stats['ready_rules']}, "
        f"NEEDS_REVIEW={stats['needs_review_rules']}, "
        f"UNSUPPORTED={stats['unsupported_rules']}"
    )
    print(
        f"Hierarchy: {stats['section_group_nodes']} groups, "
        f"{stats['compound_option_nodes']} compound option nodes"
    )
    candidate_stats = stats["catalog_attribute_candidate_mappings"]
    print(
        f"Catalog attribute candidate mappings: {candidate_stats['matched']} "
        f"({candidate_stats['partial_lab']} require lab checks)"
    )
    if args.core_ii_json:
        counts = stats["current_core_ii_mappings"]
        print(
            f"Current Core II mappings: {counts['matched']} "
            f"({counts['category_only']} category-only, {counts['partial_lab']} partial lab)"
        )
        print("Historical catalog-year applicability verified by resolver: 0")
    if result["review_queue"]:
        print("Review queue:")
        for item in result["review_queue"]:
            print(f"  - {item['rule_id']}: {item['name']} ({', '.join(item['review_flags'])})")
    print(f"Saved: {output_path}")
    print("NOT APPROVED FOR SEEDING. No database writes performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
