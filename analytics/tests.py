"""Stage 8 analytics, report, and search tests (FR-015, FR-016, FR-018).

Gate: dashboard and report values match totals counted independently from a
small fixture. The fixture (counted by hand below):

  Vacancy A (1 position)
    app1  shortlisted -> interview attended -> evaluation v1 amended to v2
          -> recommended -> selected -> appointed (Hired; vacancy Filled)
    app2  shortlisted -> interview canceled -> interview no-show
          -> not recommended -> final outcome Not Selected
    app3  not shortlisted (Not Selected)
  Vacancy B
    app4  submitted by the same person as app2

  submitted 4, unique people 3, ever shortlisted 2 (rate 50%),
  interviews scheduled excluding canceled 2, completed 1 (50%), canceled 1, no-show 1,
  committee reviews 2, recommended 1 (50%), current evaluations 1, hired 1.
"""

import io
from datetime import date, timedelta

from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts import roles
from accounts.test_stage3 import make_user
from audit.models import AuditLog
from committee import services as committee
from committee.models import AttendanceStatus, Interview
from committee.tests import CommitteeTestCase
from documents import services as documents
from recruitment import application_services as applications
from recruitment import decision_services as decisions
from recruitment.models import (ApplicationStatus, DecisionOutcome, DecisionType, Vacancy,
                                VacancyStatus)
from recruitment.test_applications import pdf

from . import metrics, services
from .models import AnalyticsReport, RecruitmentMetric

T, O, S = DecisionType, DecisionOutcome, ApplicationStatus


class AnalyticsTestCase(CommitteeTestCase):
    def setUp(self):
        super().setUp()
        Vacancy.objects.filter(pk=self.vacancy.pk).update(positions_available=1)
        self.vacancy.refresh_from_db()
        self.vacancy_b = self.published(position_title='Part-Time Instructor - English')
        self.app1 = self.application
        self.person2 = self.applicant_user('person2')
        self.person3 = self.applicant_user('person3')
        self.app2 = self.submit_for(self.person2, self.vacancy)
        self.app3 = self.submit_for(self.person3, self.vacancy)
        self.app4 = self.submit_for(self.person2, self.vacancy_b)
        for app in (self.app2, self.app3):
            applications.start_review(self.hr, app)
        self.build_history()

    def applicant_user(self, username):
        user = make_user(username, roles.APPLICANT, last_name=username.title())
        self.complete_profile(user)
        return user

    def submit_for(self, user, vacancy):
        application = applications.start_application(user, vacancy)[0]
        documents.upload(user, application, self.pds, pdf('pds.pdf'))
        applications.submit(user, application)
        application.refresh_from_db()
        return application

    def record(self, application, decision_type, outcome, **kwargs):
        actor = self.approver if decision_type in (T.FINAL_OUTCOME, T.APPOINTMENT) else self.hr
        decision = decisions.record_decision(actor, application, decision_type=decision_type,
                                             outcome=outcome, reason='Minutes', **kwargs)
        application.refresh_from_db()
        return decision

    def interview_for(self, application, attendance=None, cancel=False):
        start = timezone.now() + timedelta(days=2)
        interview = committee.schedule_interview(
            self.hr, application, panel=[self.chair], starts_at=start,
            ends_at=start + timedelta(hours=1), mode='in_person', location='Room 1')
        if cancel:
            committee.cancel_interview(self.hr, interview, reason='Typhoon')
        if attendance:
            past = timezone.now() - timedelta(hours=2)
            Interview.objects.filter(pk=interview.pk).update(starts_at=past,
                                                             ends_at=past + timedelta(hours=1))
            committee.record_attendance(self.hr, interview, attendance=attendance)
        application.refresh_from_db()
        return interview

    def build_history(self):
        for app in (self.app1, self.app2):
            decisions.release_decision(self.hr, self.record(app, T.SHORTLISTING, O.SHORTLISTED))
        self.record(self.app3, T.SHORTLISTING, O.NOT_SHORTLISTED)

        interview1 = self.interview_for(self.app1, attendance=AttendanceStatus.ATTENDED)
        v1 = committee.start_evaluation(self.psb1, self.app1, interview=interview1,
                                        findings='Clear demo')
        committee.submit_evaluation(self.psb1, v1)
        v2 = committee.amend_evaluation(self.psb1, v1, findings='Clear demo; strong Q&A',
                                        reason='Added Q&A notes')
        committee.submit_evaluation(self.psb1, v2)
        self.record(self.app1, T.COMMITTEE_RECOMMENDATION, O.RECOMMENDED,
                    committee=self.committee)
        self.record(self.app1, T.FINAL_OUTCOME, O.SELECTED)
        self.record(self.app1, T.APPOINTMENT, O.APPOINTED, appointment_reference='APPT-1',
                    appointment_at=timezone.now())

        self.interview_for(self.app2, cancel=True)
        self.interview_for(self.app2, attendance=AttendanceStatus.NO_SHOW)
        self.record(self.app2, T.COMMITTEE_RECOMMENDATION, O.NOT_RECOMMENDED,
                    committee=self.committee)
        self.record(self.app2, T.FINAL_OUTCOME, O.NOT_SELECTED)


class MetricTests(AnalyticsTestCase):
    def test_metrics_match_independent_fixture_totals(self):
        m = metrics.compute(services.scope_vacancies(self.hr))
        expected = {
            'submitted_applications': 4, 'unique_applicants': 3, 'shortlisted_count': 2,
            'scheduled_interviews': 2, 'completed_interviews': 1, 'canceled_interviews': 1,
            'no_show_interviews': 1, 'reviewed_applications': 2, 'recommended_count': 1,
            'current_evaluations': 1, 'hired_count': 1, 'filled_vacancies': 1,
        }
        self.assertEqual({k: m[k] for k in expected}, expected)
        self.assertEqual(m['shortlisting_rate'], 0.5)
        self.assertEqual(m['interview_completion_rate'], 0.5)
        self.assertEqual(m['recommendation_rate'], 0.5)
        distribution = dict(m['status_distribution'])
        self.assertEqual((distribution['Hired'], distribution['Not Selected'],
                          distribution['Submitted']), (1, 2, 1))
        self.assertIsNotNone(m['time_to_fill_seconds'])
        self.vacancy.refresh_from_db()
        self.assertEqual(self.vacancy.status, VacancyStatus.FILLED)
        stages = {s['to']: s['applications'] for s in m['stage_durations']}
        self.assertEqual(stages[S.SHORTLISTED], 2)
        self.assertEqual(stages[S.HIRED], 1)

    def test_scope_follows_permissions_and_filters(self):
        vacancy_a_only = metrics.compute(services.scope_vacancies(self.hr, vacancy=self.vacancy))
        self.assertEqual((vacancy_a_only['submitted_applications'],
                          vacancy_a_only['unique_applicants']), (3, 3))
        nothing = metrics.compute(services.scope_vacancies(self.hr2))  # no assignments
        self.assertEqual(nothing['submitted_applications'], 0)
        self.assertIsNone(nothing['shortlisting_rate'])
        self.assertEqual(metrics.format_rate(nothing['shortlisting_rate']), 'Not applicable')
        self.assertEqual(metrics.format_duration(None), 'Not applicable')
        self.assertEqual(metrics.compute(services.scope_vacancies(self.approver))
                         ['submitted_applications'], 4)
        start, end = services.period_bounds(date(2000, 1, 1), date(2000, 12, 31))
        old = metrics.compute(services.scope_vacancies(self.hr), period_start=start,
                              period_end=end)
        self.assertEqual(old['submitted_applications'], 0)

    def test_snapshot_keeps_values_and_scope(self):
        vacancies = services.scope_vacancies(self.hr, vacancy=self.vacancy)
        scope = services.describe_scope(self.hr, vacancies, vacancy=self.vacancy)
        snapshot = services.save_snapshot(self.hr, vacancies, scope, vacancy=self.vacancy)
        self.assertEqual((snapshot.submitted_applications, snapshot.shortlisted_count,
                          snapshot.completed_interviews, snapshot.recommended_count),
                         (3, 2, 1, 1))
        self.assertEqual(snapshot.definition_version, metrics.DEFINITION_VERSION)
        self.assertIn(snapshot, services.visible_snapshots(self.hr))
        self.assertNotIn(snapshot, services.visible_snapshots(self.hr2))
        with self.assertRaises(PermissionError):
            snapshot.delete()
        self.assertEqual(RecruitmentMetric.objects.count(), 1)

    def test_dashboard_renders_for_permitted_roles(self):
        self.client.force_login(self.hr)
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['values']['submitted_applications'], 4)
        self.assertContains(response, '50.0%')
        response = self.client.get(reverse('analytics:dashboard'),
                                   {'vacancy': str(self.vacancy_b.pk)})
        self.assertEqual(response.context['values']['submitted_applications'], 1)
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(reverse('analytics:dashboard')).status_code, 403)
        self.client.force_login(self.psb1)
        self.assertEqual(self.client.get(reverse('analytics:dashboard')).status_code, 403)


class ReportTests(AnalyticsTestCase):
    def setUp(self):
        super().setUp()
        import shutil
        import tempfile
        from django.test import override_settings
        self.report_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.report_dir, ignore_errors=True)
        override = override_settings(REPORT_STORAGE_ROOT=self.report_dir)
        override.enable()
        self.addCleanup(override.disable)
        self.vacancies = services.scope_vacancies(self.hr)
        self.scope = services.describe_scope(self.hr, self.vacancies)

    def generate(self, report_type, fmt='xlsx'):
        return services.generate_report(self.hr, report_type, fmt, self.vacancies, self.scope)

    def workbook(self, report):
        with open(services.report_path(report), 'rb') as handle:
            return load_workbook(io.BytesIO(handle.read())).active

    def test_statistics_report_matches_fixture(self):
        ws = self.workbook(self.generate('statistics'))
        values = {row[0]: row[1] for row in ws.iter_rows(values_only=True) if row[0]}
        self.assertEqual(values['Submitted applications'], 4)
        self.assertEqual(values['Unique applicants'], 3)
        self.assertEqual(values['Applications ever shortlisted'], 2)
        self.assertEqual(values['Shortlisting rate'], '50.0%')
        self.assertEqual(values['Interviews completed (applicant attended)'], 1)
        self.assertEqual(values['Recommendation rate'], '50.0%')
        self.assertEqual(values['Hired'], 1)
        self.assertTrue(any(str(v).startswith('Scope:') for v in values))

    def test_applicant_and_decision_reports(self):
        ws = self.workbook(self.generate('applicants'))
        refs = [row[0] for row in ws.iter_rows(values_only=True)
                if row[0] and str(row[0]).startswith('APP-')]
        self.assertEqual(sorted(refs), sorted(a.reference for a in
                                              (self.app1, self.app2, self.app3, self.app4)))
        ws = self.workbook(self.generate('decisions'))
        rows = [row for row in ws.iter_rows(values_only=True)
                if row[0] and str(row[0]).startswith('APP-')]
        self.assertEqual(len(rows), 8)  # 3 shortlisting, 2 recommendations, 2 final, 1 appt
        pdf_report = self.generate('vacancies', 'pdf')
        with open(services.report_path(pdf_report), 'rb') as handle:
            self.assertTrue(handle.read().startswith(b'%PDF'))

    def test_report_text_is_not_a_formula(self):
        user = self.app3.applicant.user
        user.last_name = '=HYPERLINK("http://example.com")'
        user.save()
        ws = self.workbook(self.generate('applicants'))
        cell = next(c for row in ws.iter_rows() for c in row
                    if c.value == '=HYPERLINK("http://example.com")')
        self.assertEqual(cell.data_type, 's')

    def test_download_permissions_and_audit(self):
        report = self.generate('statistics')
        self.client.force_login(self.hr)
        response = self.client.get(reverse('analytics:report_download', args=[report.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        b''.join(response.streaming_content)
        self.assertTrue(AuditLog.objects.filter(action='report.downloaded',
                                                entity_id=str(report.pk)).exists())
        self.client.force_login(self.hr2)  # cannot see these vacancies
        self.assertEqual(self.client.get(
            reverse('analytics:report_download', args=[report.pk])).status_code, 404)
        self.assertNotIn(report, services.visible_reports(self.hr2))
        with self.assertRaises(PermissionError):
            report.delete()

    def test_generation_through_the_dashboard(self):
        self.client.force_login(self.hr)
        response = self.client.post(reverse('analytics:dashboard') + f'?vacancy={self.vacancy.pk}',
                                    {'action': 'report', 'report_type': 'vacancies',
                                     'fmt': 'xlsx'})
        self.assertRedirects(response, reverse('analytics:reports'))
        report = AnalyticsReport.objects.get()
        self.assertEqual(report.scope['vacancy_ids'], [str(self.vacancy.pk)])
        self.assertEqual(self.client.get(reverse('analytics:reports')).status_code, 200)

    def test_failed_generation_leaves_no_file_or_record(self):
        from unittest import mock
        from pathlib import Path
        with mock.patch('analytics.services.AnalyticsReport.objects.create',
                        side_effect=RuntimeError('database down')):
            with self.assertRaises(services.ReportFailed):
                self.generate('statistics')
        self.assertFalse([p for p in Path(self.report_dir).rglob('*') if p.is_file()])
        self.assertFalse(AnalyticsReport.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action='report.generation_failed').exists())


class SearchTests(AnalyticsTestCase):
    def search(self, user, **params):
        self.client.force_login(user)
        response = self.client.get(reverse('recruitment:application_search'), params)
        self.assertEqual(response.status_code, 200)
        return {a.pk for a in response.context['page']}

    def test_combined_filters_and_permissions(self):
        everyone = {self.app1.pk, self.app2.pk, self.app3.pk, self.app4.pk}
        self.assertEqual(self.search(self.hr), everyone)
        self.assertEqual(self.search(self.hr, vacancy=self.vacancy_b.pk), {self.app4.pk})
        self.assertEqual(self.search(self.hr, status='not_selected'),
                         {self.app2.pk, self.app3.pk})
        self.assertEqual(self.search(self.hr, q='Person2'), {self.app2.pk, self.app4.pk})
        self.assertEqual(self.search(self.hr, q='Person2', vacancy=self.vacancy.pk),
                         {self.app2.pk})
        self.assertEqual(self.search(self.hr, q=self.app3.reference), {self.app3.pk})
        self.assertEqual(self.search(self.hr, degree_level='master'), everyone)
        self.assertEqual(self.search(self.hr, degree_level='doctorate'), set())
        self.assertEqual(self.search(self.hr, outcome='not_assessed'), everyone)
        standard = applications.requirements_for(self.app1).qualification_standards.first()
        self.open_review(self.app1)  # outcomes wait for the qualification review gate
        applications.record_qualification_outcome(self.hr, self.app1, standard,
                                                  outcome='needs_review', reason='Check TOR')
        self.assertEqual(self.search(self.hr, outcome='needs_review'), {self.app1.pk})
        self.assertEqual(self.search(self.hr, outcome='not_assessed'), everyone - {self.app1.pk})
        self.assertEqual(self.search(self.hr2), set())  # unassigned HR sees nothing
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(reverse('recruitment:application_search')).status_code,
                         403)
