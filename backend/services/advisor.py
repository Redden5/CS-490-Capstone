"""Retrieve Marshall academic records and compose answers with source citations."""

from collections import defaultdict
import csv
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
COURSE_CODE = re.compile(r"\b([A-Z][A-Z&]{1,7})\s*[- ]?\s*(\d{3}[A-Z]{0,2})\b", re.I)
TERM = re.compile(r"\b(?:fall|spring|summer|winter)\s+\d{4}\b|\b20\d{4}\b", re.I)
TOPICS = {
    "prerequisites": re.compile(r"\b(?:pre[ -]?req\w*|co[ -]?req\w*|eligib\w*)\b"),
    "schedules": re.compile(r"\b(?:schedul\w*|planner|sections?|offered|offerings?|meet\w*|times?|availability|seats?|crns?|semester|term|conflicts?|together)\b"),
    "degree": re.compile(r"\b(?:degree|audit\w*|major|graduat\w*|program|electives?|core\s*(?:i{1,2}|[12])\b|requirements?)\b"),
    "courses": re.compile(r"\b(?:courses?|class(?:es)?|catalog|credits?|descriptions?)\b"),
}
STOP_WORDS = frozenset(
    "a an and are about be can could do does for how i is it list me my of on or please show "
    "tell that the to what when where which who will with would you course courses class classes "
    "catalog details detail information description credit credits prerequisite prerequisites "
    "semester term schedule scheduling planner sections offered meet time degree major audit "
    "requirements need take next marshall university".split()
)
SCOPE_REPLY = "I can help with Marshall course details, prerequisites, scheduling, and degree requirements. Try a course code such as CS 110."
SCHEDULE_NOTE = "These are sections from a saved schedule snapshot. Use Planner or the official schedule for current availability and conflicts."
AUDIT_NOTE = "For a coursework-specific result, enter your completed courses in Academic Info and run Degree Audit. The unofficial audit currently supports Computer Science B.S., catalog year 2026–2027."


@dataclass(frozen=True)
class Record:
    title: str
    text: str
    topic: str
    url: str | None
    codes: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    search_text: str = ""


def words(value):
    return set(re.findall(r"[a-z0-9]+", value.lower())) - STOP_WORDS


def clean(value):
    return " ".join(str(value or "").split())


def csv_rows(name):
    with (SCRIPTS / name).open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def dataset(name):
    return json.loads((SCRIPTS / "data" / name).read_text(encoding="utf-8"))


def official_url(value):
    url = urlsplit(value or "")
    return value if url.scheme == "https" and url.netloc in {"catalog.marshall.edu", "mubert.marshall.edu", "www.marshall.edu"} else None


def credit_range(row):
    low, high = (clean(row[key]).removesuffix(".0") for key in ("min_credit_hours", "max_credit_hours"))
    if low and high:
        return f"{low} credits" if low == high else f"{low}–{high} credits"
    return "Credits unavailable"


def catalog_records():
    attributes = defaultdict(set)
    for row in csv_rows("catalog_attributes.csv"):
        attributes[row["course_code"]].add(row["attribute_name"])
    for row in csv_rows("courses.csv"):
        code = row["course_code"]
        tags = ", ".join(sorted(attributes[code]))
        description = clean(row["description"])
        text = f"{credit_range(row)}."
        if description:
            text += f" {description}"
        if tags:
            text += f" Catalog attributes: {tags}."
        yield Record(f"{code} · {clean(row['course_title'])}", text, "courses", official_url(row["source_url"]), (code,))

    for course in dataset("prerequisites/parsed_all_v2.json")["courses"]:
        code = course["course_code"]
        rules = " ".join(f"{rule['rule_type'].replace('_', ' ').title()}: {clean(rule['raw_text'])}"
                         for rule in course["rules"] if rule.get("raw_text"))
        if rules:
            review = "" if course.get("complete_enrollment_conditions_machine_checkable") else " The parsed rules contain conditions that need review."
            yield Record(f"{code} · Enrollment requirements ({course['catalog_year']})",
                         rules + review, "prerequisites", official_url(course["source_url"]), (code,))


def schedule_records():
    meetings = defaultdict(list)
    for row in csv_rows("section_meetings.csv"):
        if row["day_of_week"] and row["start_time"] and row["end_time"]:
            time = f"{row['start_time'][:5]}–{row['end_time'][:5]}"
            meetings[row["term_code"], row["crn"]].append(clean(f"{row['day_of_week']} {time} {row['location']}"))
    sections = defaultdict(list)
    urls = {}
    crns = defaultdict(list)
    for row in csv_rows("sections.csv"):
        term, code, crn = row["term_code"], row["course_code"], row["crn"]
        times = "; ".join(meetings[term, crn]) or "meeting time unavailable"
        delivery = row["delivery_mode"].replace("_", " ")
        sections[term, code].append(f"Section {row['section_number']} (CRN {crn}) · {delivery} · {times}")
        urls[term, code] = official_url(row["source_url"])
        crns[term, code].append(crn)
    for (term, code), rows in sections.items():
        label = "Fall 2026" if term == "202701" else f"Term {term}"
        yield Record(f"{code} · {label} sections", "\n".join(rows), "schedules",
                     urls[term, code], (code,), (term, label.lower()), " ".join(crns[term, code]))


def degree_records():
    resolved = dataset("degree_requirements/resolved/computer_science_bs_resolved.json")
    needs_review = {node["source"].get("rule_id") for node in resolved["nodes"] if node["status"] == "NEEDS_REVIEW"}
    for filename in sorted((SCRIPTS / "data/degree_requirements/parsed").glob("*.json")):
        data = dataset(f"degree_requirements/parsed/{filename.name}")
        program, year = data["program_title"], data["catalog_year"]
        url = official_url(data["source_url"])
        groups = defaultdict(list)
        for rule in data["requirements"]:
            group = " / ".join(rule["section_path"]) or "Degree requirements"
            name = clean(rule["name"])
            options = rule.get("options", [])
            codes = tuple(dict.fromkeys([*rule.get("course_codes", []), *(
                code for option in options for code in option.get("course_codes", [])
            )]))
            if rule.get("instruction"):
                name += f". {clean(rule['instruction'])}"
            if options:
                name += " " + "; ".join(clean(option.get("description") or option.get("code_expression")) for option in options)
            credits = rule.get("catalog_declared_credits")
            note = f" · {credits} catalog credits" if credits else ""
            if rule.get("review_flags") or (program == resolved["program_title"] and rule["rule_id"] in needs_review):
                note += " · Review the catalog for conditions"
            groups[group].append((name + note, codes))
        for group, rules in groups.items():
            for start in range(0, len(rules), 7):
                chunk = rules[start:start + 7]
                yield Record(f"{program} · {group} ({year})", "\n".join(row for row, _ in chunk),
                             "degree", url, tuple(dict.fromkeys(code for _, codes in chunk for code in codes)),
                             search_text=program)

    core = dataset("core_ii_courses.json")
    for group in core["categories"]:
        courses = group["courses"]
        for start in range(0, len(courses), 8):
            chunk = courses[start:start + 8]
            yield Record(f"Core II · {group['category']}", "\n".join(
                f"{row['course_code']} · {clean(row['course_title'])}" for row in chunk),
                "degree", official_url(core["source_url"]), tuple(row["course_code"] for row in chunk),
                search_text=f"Core II {group['attribute_name']}")


@lru_cache(maxsize=1)
def index():
    records = tuple(record for source in (catalog_records, schedule_records, degree_records) for record in source())
    codes = frozenset(code for record in records for code in record.codes)
    course_titles = {record.codes[0]: record.title for record in records if record.topic == "courses"}
    tokens = tuple(words(f"{record.title} {record.search_text}").union(
        *(words(course_titles.get(code, "")) for code in record.codes if record.topic == "schedules")
    ) for record in records)
    return records, codes, tokens


def select_topic(question, has_course):
    if TOPICS["prerequisites"].search(question):
        return "prerequisites"
    if TOPICS["degree"].search(question) and (
        not has_course or re.search(r"\b(?:degree|audit\w*|major|graduat\w*|program|core)\b", question)
    ):
        return "degree"
    if TOPICS["schedules"].search(question):
        return "schedules"
    if has_course and re.search(r"\brequirements?\b", question):
        return "prerequisites"
    if has_course or TOPICS["courses"].search(question):
        return "courses"
    return None


def reply(answer, matches=(), codes=(), action=None):
    citations = dict.fromkeys((row.title, row.url) for row in matches)
    return {"answer": answer, "sources": [{"title": title, "url": url} for title, url in citations],
            "course_codes": list(codes), "action": action}


def format_record(record, crn):
    if record.topic != "schedules":
        return f"{record.title}\n{record.text}"

    sections = record.text.splitlines()
    if crn:
        sections = [line for line in sections if f"(CRN {crn})" in line]
    excerpt = sections[:5]
    if len(sections) > len(excerpt):
        excerpt.append(f"{len(sections) - len(excerpt)} more sections in this snapshot.")
    return f"{record.title}\n" + "\n".join(excerpt)


@lru_cache(maxsize=1)
def related_courses():
    """Index prerequisite references without treating alternatives as eligibility."""
    reverse = defaultdict(list)
    for course in dataset("prerequisites/parsed_all_v2.json")["courses"]:
        for rule in course["rules"]:
            if rule["rule_type"] != "PREREQUISITE":
                continue
            references = {atom["course_code"] for clause in rule.get("dnf_clauses", [])
                          for atom in clause if atom.get("course_code")}
            for reference in references:
                reverse[reference].append(Record(
                    f"{course['course_code']} · After {reference} ({course['catalog_year']})",
                    f"References {reference} as a prerequisite. {clean(rule['raw_text'])}",
                    "prerequisites", official_url(course["source_url"]), (course["course_code"],),
                ))
    return reverse


def relationship_answer(question, codes, records):
    if re.search(r"\b(?:opens? up|unlocks?|after|leads? to|depend\w* on)\b", question):
        matches = [record for code in codes for record in related_courses().get(code, [])]
        intro = "These courses reference your selected course in their prerequisite rules. Additional prerequisites, alternative paths, grades, and concurrent conditions still apply."
    elif re.search(r"\b(?:alternatives?|same (?:elective|requirement)|other.*elective)\b", question):
        matches = [record for record in records if record.topic == "degree"
                   and set(codes).intersection(record.codes)]
        intro = "These degree requirement groups include your selected course. Check each selection instruction and catalog year."
    else:
        return None
    if not matches:
        return reply("No matching course relationships were found in the saved academic data.", codes=codes)
    matches = list(dict.fromkeys(matches))[:3]
    answer = intro + "\n\n" + "\n\n".join(format_record(record, None) for record in matches)
    return reply(answer, matches, tuple(dict.fromkeys(code for record in matches for code in record.codes))[:3])


def answer_question(question, previous_codes=()):
    records, known_codes, indexed_words = index()
    question_lower = question.lower()
    if re.search(r"\b(?:what (?:courses|classes) do i still need|(?:run|see|view|check) my (?:degree )?audit)\b", question_lower):
        return reply(AUDIT_NOTE, action="audit")
    explicit = tuple(dict.fromkeys(f"{subject.upper()} {number.upper()}" for subject, number in COURSE_CODE.findall(question)))
    codes = tuple(code for code in explicit if code in known_codes)[:3]
    relationship = relationship_answer(question_lower, codes, records) if codes else None
    if relationship:
        return relationship
    topic = select_topic(question_lower, bool(codes))
    similar_query = bool(codes and re.search(r"\b(?:similar|related|like)\b", question_lower))
    if similar_query:
        from backend.services.advisor_ollama import semantic_matches
        originals = [record for record in records if record.topic == "courses" and set(codes).intersection(record.codes)]
        related = semantic_matches(" ".join(record.title + " " + record.text for record in originals), records)
        matches = [record for record in related if not set(codes).intersection(record.codes)][:3]
        if not matches:
            return reply("No semantic course matches are available yet. Try a course topic or check the local embedding index.", codes=codes)
        return reply("These courses cover related topics. Degree credit and equivalency require separate review.\n\n"
                     + "\n\n".join(format_record(record, None) for record in matches), matches,
                     tuple(record.codes[0] for record in matches))
    query = words(question)
    follow_up = re.search(r"\b(?:it|that class|that course|those courses|them)\b", question_lower)
    # Use prior course identities for short follow-ups that do not name another subject.
    if not explicit and (follow_up or (topic in {"courses", "prerequisites", "schedules"} and not query - {"its", "any", "many"})):
        codes = tuple(code for code in previous_codes if code in known_codes)[:3]
        if codes and topic is None:
            topic = "courses"
    if topic is None:
        known_title = len(query) >= 2 and any(
            record.topic == "courses" and query <= vocabulary for record, vocabulary in zip(records, indexed_words)
        )
        if not known_title:
            return reply(SCOPE_REPLY)
        topic = "courses"

    if topic == "degree" and re.search(r"\b(?:my|i)\b.*\b(?:audit|coursework|graduate|degree|requirements|graduation)\b", question_lower):
        return reply(AUDIT_NOTE, action="audit")

    terms = {clean(term).lower() for term in TERM.findall(question)}
    if topic == "schedules" and (
        terms - {"202701", "fall 2026"}
        or re.search(r"\b(?:(?:next|this|upcoming)\s+(?:term|semester|fall|spring|summer|winter)|(?:fall|spring|summer|winter)(?!\s+\d{4}))\b", question_lower)
    ):
        return reply("The local schedule snapshot covers Fall 2026 (term 202701). Use Planner to check the term you want.", action="planner")
    if topic == "schedules" and re.search(r"\b(?:conflicts?|together)\b", question_lower):
        return reply("In Planner, choose a term and generate schedules from your course selections. Planner checks conflicts using section meeting days and times.", codes=codes, action="planner")

    # An explicit unknown code should never match a different course via keywords.
    if not codes and any(code.split()[0] not in {"IN", "FA", "FOR", "TERM"} for code in explicit):
        return reply("I could not find that course code in the local Marshall catalog. Check the code and try again.")

    core_query = topic == "degree" and re.search(r"\bcore\s*(?:ii|2)\b", question_lower)
    if core_query:
        query -= {"core", "ii", "2"}
    elif topic == "degree":
        query -= {"core", "of", "bs", "ba", "s", "b", "a"}
    ranked = []
    crn_match = re.search(r"\bcrn\s*#?\s*(\d{4,5})\b", question_lower) if topic == "schedules" else None
    crn = crn_match[1] if crn_match else None
    for record, vocabulary in zip(records, indexed_words):
        if record.topic != topic:
            continue
        if core_query and not record.title.startswith("Core II · "):
            continue
        code_hits = len(vocabulary & query)
        if codes and not set(codes).intersection(record.codes):
            continue
        if terms and topic == "schedules" and not terms.issubset(record.terms):
            continue
        if crn and crn not in record.search_text.split():
            continue
        if codes or code_hits or (core_query and not query):
            ranked.append((code_hits, record))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if ranked and not codes:
        ranked = [item for item in ranked if item[0] == ranked[0][0]]
    matches = [record for _, record in ranked[:3]]
    if topic == "courses" and not codes:
        from backend.services.advisor_ollama import semantic_matches
        semantic = semantic_matches(question, records)
        credit_match = re.search(r"\b(\d+(?:\.\d+)?)\s*[- ]?credits?\b", question_lower)
        if credit_match:
            desired = float(credit_match[1])
            def matches_credits(record):
                credit_text = re.match(r"(\d+(?:\.\d+)?)(?:–(\d+(?:\.\d+)?))? credits", record.text)
                return bool(credit_text and float(credit_text[1]) <= desired <= float(credit_text[2] or credit_text[1]))
            matches = [record for record in matches if matches_credits(record)]
            semantic = [record for record in semantic if matches_credits(record)]
        # Semantic results complement exact title matches and keyword retrieval.
        exact_titles = [record for record in matches if query and query <= words(record.title)]
        matches = list(dict.fromkeys([*exact_titles, *semantic, *matches]))[:3]
    if not matches:
        if topic == "schedules":
            return reply("I could not find matching sections in the saved snapshot. Use Planner to check term offerings and assemble a schedule.", codes=codes, action="planner")
        if topic == "degree":
            return reply("Ask about Computer Science B.S., Music Industry B.A., or a specific Core II category. " + AUDIT_NOTE, action="audit")
        if topic == "prerequisites" and codes:
            return reply("I found no prerequisite or corequisite record for that course in the local snapshot. Check its official catalog listing before registering.",
                         [record for record in records if record.topic == "courses" and set(codes).intersection(record.codes)][:3], codes)
        return reply("I could not find matching information. Try a Marshall course code or the course's catalog title.")

    result = "\n\n".join(format_record(row, crn) for row in matches)
    if len(ranked) > len(matches):
        result += "\n\nShowing the first three matching records."
    matched_codes = tuple(dict.fromkeys(code for row in matches for code in row.codes))
    if codes:
        missing = [code for code in codes if code not in matched_codes]
        if missing:
            result += f"\n\nNo matching {topic} record in the snapshot for {', '.join(missing)}."
    action = None
    if topic == "schedules":
        result += "\n\n" + SCHEDULE_NOTE
        action = "planner"
    if topic == "degree":
        result += "\n\n" + AUDIT_NOTE
        action = "audit"
    if topic == "prerequisites" and re.search(r"\b(?:eligib\w*|can i|may i)\b", question_lower):
        result += "\n\nCompare these requirements with your completed coursework in Academic Info."
        action = "audit"
    return reply(result, matches, (codes or matched_codes)[:3] if topic != "degree" else (), action)
