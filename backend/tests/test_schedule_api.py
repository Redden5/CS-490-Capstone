"""Exercise the registered HTTP routes and real scheduler using SELECT-only tables."""
from contextlib import ExitStack
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from backend.main import app
from backend.routes.planning import get_client_factory
from backend.services import schedule_service as scheduler
from backend.services.degree_planning import run_degree_plan


class Query:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.filters, self.orders = [], []
        self.start, self.end = 0, 1000

    def select(self, columns):
        self.columns = columns.split(',')
        return self

    def eq(self, key, value):
        self.filters.append(lambda row: row[key] == value)
        return self

    def in_(self, key, values):
        self.filters.append(lambda row: row[key] in values)
        return self

    def order(self, key):
        self.orders.append(key)
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        self.client.calls.append(self.table)
        rows = [row for row in self.client.tables[self.table] if all(f(row) for f in self.filters)]
        rows.sort(key=lambda row: tuple(row[k] for k in self.orders))
        return SimpleNamespace(data=[{k: row[k] for k in self.columns} for row in rows[self.start:self.end+1]])


class Database:
    def __init__(self):
        self.calls = []
        self.tables = {'term': [{'term_id': 1, 'active_status': True}, {'term_id': 2, 'active_status': False}],
                       'course': [], 'coursesection': [], 'sectionmeeting': []}
        for i, code in enumerate(['CS 120', 'CS 205', 'CS 310', 'CS 320'], 1):
            self.tables['course'].append({'course_id': i, 'course_code': code, 'min_credit_hours': 3,
                                         'max_credit_hours': 3, 'active_status': True})
            self.add_section(i * 10, i, i, '09:00:00', '10:00:00')
        # A nonconflicting alternative and a known collision for CS 120.
        self.add_section(11, 1, 5, '11:00:00', '12:00:00')
        self.add_section(12, 1, 2, '09:30:00', '10:30:00')

    def add_section(self, sid, cid, day, start, end, *, term=1):
        self.tables['coursesection'].append({'section_id': sid, 'course_id': cid, 'term_id': term,
            'crn': str(sid), 'section_number': str(sid), 'section_status': 'OPEN',
            'capacity': 20, 'enrolled': 1, 'delivery_mode': 'IN_PERSON'})
        self.tables['sectionmeeting'].append({'meeting_id': sid, 'section_id': sid,
            'day_of_week': day, 'start_time': start, 'end_time': end, 'meeting_type': 'LEC'})

    def table(self, name):
        if name not in self.tables:
            raise AssertionError('Unexpected table')
        return Query(self, name)


def handoff():
    draft = Path(__file__).resolve().parents[1] / 'scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json'
    def unavailable():
        raise ValueError('No live database permitted')
    result = run_degree_plan(json.loads(draft.read_text()), [], core_ii_candidates=True,
                             client_factory=unavailable).handoff
    result['requirements'] = [
        {'rule_id': 'required', 'kind': 'REQUIRED_COURSE', 'planning_disposition': 'CANDIDATE_COURSEWORK_REMAINING',
         'remaining_required_course_codes': ['CS 120', 'CS 205']},
        {'rule_id': 'choice', 'kind': 'CHOOSE_N', 'planning_disposition': 'CANDIDATE_COURSEWORK_REMAINING',
         'choice_progress': {'choices_remaining_candidate': 1}, 'remaining_policy_review_flags': [],
         'course_options': [{'kind': 'SINGLE_COURSE', 'course_codes': [code], 'missing_codes_to_complete_option': [code]} for code in ['CS 310', 'CS 320']]},
    ]
    result['course_eligibility'] = {'evaluation_status': 'COMPLETED', 'courses': [
        {'course_code': code, 'status': 'MET'} for code in ['CS 120', 'CS 205', 'CS 310', 'CS 320']]}
    return result


class ScheduleApiTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.db = Database()
        self.factory = Mock(return_value=self.db)
        stack.enter_context(patch.dict(app.dependency_overrides, {get_client_factory: lambda: self.factory}))
        self.http = stack.enter_context(TestClient(app, raise_server_exceptions=False))
        self.handoff = handoff()

    def post(self, action, **fields):
        return self.http.post('/api/v1/schedules/' + action, json=fields)

    def test_generate_uses_real_readers_and_shared_client(self):
        response = self.post('generate', handoff=self.handoff, term_id=1, target_credits=9)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.factory.assert_called_once_with()
        data = response.json()
        self.assertEqual(data['options'][0]['total_credits'], 9)
        self.assertEqual(set(data['options'][0]['sections']) & {'CS 120', 'CS 205'}, {'CS 120', 'CS 205'})
        self.assertIn('sectionmeeting', self.db.calls)
        self.assertEqual(self.db.calls.count('course'), 1)
        for option in data['options']:
            sections = list(option['sections'].values())
            for i, first in enumerate(sections):
                for second in sections[i+1:]:
                    self.assertFalse(scheduler.sections_conflict(first, second))

    def test_readers_page_through_all_sections_and_meetings(self):
        with patch.object(scheduler, 'PAGE_SIZE', 1):
            response = self.post('sections', course_code='CS 120', term_id=1)
        self.assertEqual(response.status_code, 200, response.text)
        sections = response.json()['sections']
        self.assertEqual({s['section_id'] for s in sections}, {10, 11, 12})
        self.assertTrue(all(len(s['meetings']) == 1 for s in sections))
        self.assertGreater(self.db.calls.count('coursesection'), 1)

    def test_sections_are_filtered_to_course_and_term(self):
        self.db.add_section(99, 1, 1, '09:00:00', '10:00:00', term=2)
        response = self.post('sections', course_code='CS 120', term_id=1)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({s['section_id'] for s in response.json()['sections']}, {10, 11, 12})

    def test_section_swap_preserves_other_courses_and_rejects_conflict(self):
        fields = dict(handoff=self.handoff, term_id=1, current_sections={'CS 120': 10, 'CS 205': 20}, course_code='CS 120')
        good = self.post('swap-section', **fields, new_section_id=11)
        self.assertEqual(good.status_code, 200, good.text)
        self.assertTrue(good.json()['ok'])
        self.assertEqual(good.json()['sections']['CS 205']['section_id'], 20)
        bad = self.post('swap-section', **fields, new_section_id=12)
        self.assertEqual(bad.status_code, 200, bad.text)
        self.assertFalse(bad.json()['ok'])
        self.assertTrue(any(e.startswith('TIME_CONFLICT') for e in bad.json()['errors']))

    def test_course_swap_and_mandatory_protection(self):
        fields = dict(handoff=self.handoff, term_id=1, current_sections={'CS 120': 10, 'CS 310': 30},
                      new_course_code='CS 320', new_section_id=40)
        good = self.post('swap-course', **fields, old_course_code='CS 310')
        self.assertEqual(good.status_code, 200, good.text)
        self.assertTrue(good.json()['ok'])
        self.assertEqual(set(good.json()['sections']), {'CS 120', 'CS 320'})
        bad = self.post('swap-course', **fields, old_course_code='CS 120')
        self.assertFalse(bad.json()['ok'])

    def test_unmet_full_and_unknown_evidence(self):
        self.handoff['course_eligibility']['courses'][0]['status'] = 'UNMET'
        self.handoff['course_eligibility']['courses'][1]['status'] = 'UNKNOWN'
        self.db.tables['coursesection'][2]['enrolled'] = 20
        response = self.post('generate', handoff=self.handoff, term_id=1)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['excluded_courses']['CS 120'], 'PREREQUISITES_UNMET')
        self.assertIn('CS 310', data['excluded_courses'])
        self.assertTrue(any('CS 205:PREREQUISITES_UNKNOWN' in option['warnings'] for option in data['options']))

    def test_invalid_terms_versions_and_credit_limits(self):
        for term in [2, 99]:
            self.assertEqual(self.post('generate', handoff=self.handoff, term_id=term).status_code, 422)
        for credits in [0, 22]:
            self.assertEqual(self.post('generate', handoff=self.handoff, term_id=1, target_credits=credits).status_code, 422)
        self.handoff['schema_version'] = '2.2.0'
        self.assertEqual(self.post('generate', handoff=self.handoff, term_id=1).status_code, 422)

    def test_stale_or_injected_current_sections_rejected(self):
        fields = dict(handoff=self.handoff, term_id=1, course_code='CS 120', new_section_id=11)
        for selected in [{'CS 120': 999}, {'CS 120': 10, 'ENG 101': 20}]:
            self.assertEqual(self.post('swap-section', **fields, current_sections=selected).status_code, 422)

    def test_unavailable_database_is_sanitized(self):
        self.factory.side_effect = RuntimeError('private credentials')
        response = self.post('generate', handoff=self.handoff, term_id=1)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private credentials', response.text)

    def test_configuration_and_unexpected_errors_keep_cors_headers(self):
        payload = {'handoff': self.handoff, 'term_id': 1}
        headers = {'Origin': 'http://localhost:5173'}
        self.factory.side_effect = ValueError('secret configuration')
        response = self.http.post('/api/v1/schedules/generate', json=payload, headers=headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers.get('access-control-allow-origin'), headers['Origin'])
        self.factory.side_effect = None
        with patch.object(Database, 'table', side_effect=Exception('private database error')):
            response = self.http.post('/api/v1/schedules/generate', json=payload, headers=headers)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers.get('access-control-allow-origin'), headers['Origin'])
        self.assertNotIn('private database error', response.text)

    def test_full_degree_audit_can_be_consumed(self):
        draft = Path(__file__).resolve().parents[1] / 'scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json'
        result = run_degree_plan(json.loads(draft.read_text()), [], core_ii_candidates=True,
            client_factory=Mock(side_effect=ValueError('offline'))).handoff
        response = self.post('generate', handoff=result, term_id=1)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['options'])
