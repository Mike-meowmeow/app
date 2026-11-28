"""CSC model merit system rules (CSC MC No. 19, s. 2005; vault note "CSC Merit
System Review"):

1. Qualification standard presets: the seeded Instructor I position, and
   standards limited to board-exam courses that each vacancy must keep or
   remove before publishing.
2. Publication rules: at least 10 calendar days to apply (blocks publishing),
   6-month validity and 9-month fill limit (warnings).
3. The kind of appointment is required on appointment records.
"""

from datetime import datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from committee.tests import CommitteeTestCase

from . import decision_services as decisions
from . import merit, services
from . import position_services as catalog
from .forms import PositionStandardForm
from .models import (CriterionType, DecisionOutcome, DecisionType, Position, RecruitmentDecision,
                     Vacancy, VacancyStatus)
from .test_positions import CatalogTestCase
from .tests import VacancyTestCase

MANILA = ZoneInfo('Asia/Manila')
T = DecisionType
O = DecisionOutcome


class MonthTests(SimpleTestCase):
    def test_add_months_keeps_the_day_or_uses_the_last_day(self):
        start = datetime(2026, 8, 31, 9, 0, tzinfo=MANILA)
        self.assertEqual(merit.add_months(start, 6), datetime(2027, 2, 28, 9, 0, tzinfo=MANILA))
        self.assertEqual(merit.add_months(start, 9), datetime(2027, 5, 31, 9, 0, tzinfo=MANILA))
        self.assertEqual(merit.add_months(datetime(2026, 1, 15, tzinfo=MANILA), 12),
                         datetime(2027, 1, 15, tzinfo=MANILA))
        self.assertEqual(merit.add_months(datetime(2026, 3, 31, tzinfo=MANILA), -1),
                         datetime(2026, 2, 28, tzinfo=MANILA))


class PostingPeriodTests(VacancyTestCase):
    def test_publishing_needs_ten_calendar_days_to_apply(self):
        now = timezone.now()
        vacancy = self.complete(self.new_vacancy(opens_at=now - timedelta(hours=1),
                                                 deadline_at=now + timedelta(days=9)))
        problems = services.publication_problems(vacancy, vacancy.draft_requirements())
        self.assertTrue(any('at least 10 calendar days' in p for p in problems), problems)
        with self.assertRaises(ValidationError):
            services.publish(self.hr, vacancy)
        services.update_vacancy(self.hr, vacancy, deadline_at=now + timedelta(days=10, hours=1))
        services.publish(self.hr, vacancy)
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.status, VacancyStatus.PUBLISHED)

    def test_period_counts_from_a_later_opening_date(self):
        now = timezone.now()
        vacancy = self.complete(self.new_vacancy(opens_at=now + timedelta(days=5),
                                                 deadline_at=now + timedelta(days=12)))
        problems = services.publication_problems(vacancy, vacancy.draft_requirements())
        self.assertTrue(any('at least 10 calendar days' in p for p in problems), problems)

    def test_published_deadline_cannot_be_shortened_below_the_period(self):
        vacancy = self.published()
        with self.assertRaises(ValidationError):
            services.update_vacancy(self.hr, vacancy, reason='Shorter',
                                    deadline_at=vacancy.posted_at + timedelta(days=9))
        services.update_vacancy(self.hr, vacancy, reason='Longer',
                                deadline_at=vacancy.deadline_at + timedelta(days=3))

    def test_requirement_updates_are_not_blocked_late_in_the_period(self):
        vacancy = self.published()
        Vacancy.objects.filter(pk=vacancy.pk).update(
            deadline_at=timezone.now() + timedelta(days=2))
        vacancy.refresh_from_db()
        draft = services.start_requirements_update(self.hr, vacancy)
        self.assertEqual(services.publication_problems(vacancy, draft), [])


class PublicationAgeTests(VacancyTestCase):
    def aged(self, months, days=0):
        vacancy = self.published()
        posted = merit.add_months(timezone.localtime(), -months) - timedelta(days=days)
        Vacancy.objects.filter(pk=vacancy.pk).update(posted_at=posted)
        vacancy.refresh_from_db()
        return vacancy

    def test_warnings_after_six_and_nine_months(self):
        self.assertIsNone(merit.publication_warning(self.aged(5, days=20)))
        level, message = merit.publication_warning(self.aged(6, days=1))
        self.assertEqual(level, 'validity')
        self.assertIn('screening started within 6 months', message)
        level, message = merit.publication_warning(self.aged(9, days=1))
        self.assertEqual(level, 'fill_limit')
        self.assertIn('publishing the vacancy again', message)

    def test_no_warning_for_drafts_or_filled_vacancies(self):
        vacancy = self.aged(10)
        Vacancy.objects.filter(pk=vacancy.pk).update(status=VacancyStatus.FILLED)
        vacancy.refresh_from_db()
        self.assertIsNone(merit.publication_warning(vacancy))
        self.assertIsNone(merit.publication_warning(self.new_vacancy()))

    def test_vacancy_pages_show_the_warning(self):
        vacancy = self.aged(9, days=1)
        self.client.force_login(self.hr)
        detail = self.client.get(reverse('recruitment:vacancy_detail', args=[vacancy.pk]))
        self.assertContains(detail, 'publishing the vacancy again')
        listing = self.client.get(reverse('recruitment:vacancy_list'))
        self.assertContains(listing, 'Past the fill limit')


class BoardCourseStandardTests(CatalogTestCase):
    def setUp(self):
        super().setUp()
        self.ra1080 = catalog.add_standard_default(
            self.hr, self.math, criterion_type=CriterionType.LICENSE,
            name='Professional Teacher', description='LET passer, for board courses',
            board_courses_only=True)

    def test_only_eligibility_or_license_can_be_limited(self):
        with self.assertRaises(ValidationError):
            catalog.add_standard_default(self.hr, self.math,
                                         criterion_type=CriterionType.EDUCATION,
                                         degree_level='bachelor', description='Any degree',
                                         board_courses_only=True)

    def test_form_ignores_the_flag_for_other_criteria(self):
        form = PositionStandardForm({'criterion_type': CriterionType.EDUCATION,
                                     'degree_level': 'bachelor', 'board_courses_only': 'on',
                                     'description': "Bachelor's degree"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data['board_courses_only'])
        form = PositionStandardForm({'criterion_type': CriterionType.LICENSE,
                                     'name': 'Professional Teacher', 'board_courses_only': 'on',
                                     'description': 'LET passer'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data['board_courses_only'])

    def test_copied_standard_blocks_publishing_until_kept(self):
        vacancy = self.from_catalog()
        draft = vacancy.draft_requirements()
        flagged = draft.qualification_standards.get(board_courses_only=True)
        problems = services.publication_problems(vacancy, draft)
        self.assertTrue(any('LET passer, for board courses' in p for p in problems), problems)
        with self.assertRaises(ValidationError):
            services.publish(self.hr, vacancy)
        services.keep_board_standard(self.hr, vacancy, flagged.pk)
        self.assertEqual(services.publication_problems(vacancy, draft), [])
        self.assertTrue(AuditLog.objects.filter(action='vacancy.board_standard_kept').exists())
        services.publish(self.hr, vacancy)
        published = vacancy.current_requirements()
        self.assertFalse(published.qualification_standards.filter(
            board_courses_only=True).exists())
        draft = services.start_requirements_update(self.hr, vacancy)
        with self.assertRaises(ValidationError):  # nothing left to confirm
            services.keep_board_standard(self.hr, vacancy,
                                         draft.qualification_standards.first().pk)

    def test_removing_the_standard_also_clears_the_problem(self):
        vacancy = self.from_catalog()
        draft = vacancy.draft_requirements()
        flagged = draft.qualification_standards.get(board_courses_only=True)
        services.remove_requirement_item(self.hr, vacancy, 'standard', flagged.pk)
        self.assertEqual(services.publication_problems(vacancy, draft), [])

    def test_keep_button_on_the_vacancy_page(self):
        vacancy = self.from_catalog()
        flagged = vacancy.draft_requirements().qualification_standards.get(
            board_courses_only=True)
        self.client.force_login(self.hr)
        url = reverse('recruitment:requirement_keep', args=[vacancy.pk, flagged.pk])
        page = self.client.get(reverse('recruitment:vacancy_detail', args=[vacancy.pk]))
        self.assertContains(page, url)
        self.client.post(url)
        flagged.refresh_from_db()
        self.assertFalse(flagged.board_courses_only)

    def test_manifest_records_the_flag(self):
        manifest = catalog.defaults_manifest(self.math)
        flags = {s['description']: s['board_courses_only'] for s in manifest['standards']}
        self.assertTrue(flags['LET passer, for board courses'])
        self.assertFalse(flags['LET eligibility'])


class SeededPositionTests(VacancyTestCase):
    def test_seed_adds_the_csc_instructor_position_once(self):
        position = Position.objects.get(title='Instructor I (Part-Time)')  # seeded in setUp
        standards = {s.criterion_type: s for s in position.qualification_standards.all()}
        self.assertEqual(standards[CriterionType.EDUCATION].required_value,
                         {'degree_level': 'bachelor'})
        ra1080 = standards[CriterionType.ELIGIBILITY]
        self.assertEqual(ra1080.required_value, {'name': 'RA 1080 (Board/Bar)'})
        self.assertTrue(ra1080.board_courses_only)
        self.assertFalse(standards[CriterionType.EDUCATION].board_courses_only)
        call_command('seed_reference_data', stdout=StringIO())
        self.assertEqual(Position.objects.filter(title='Instructor I (Part-Time)').count(), 1)

    def test_seed_leaves_an_edited_position_alone(self):
        position = Position.objects.get(title='Instructor I (Part-Time)')
        position.qualification_standards.all().delete()
        call_command('seed_reference_data', stdout=StringIO())
        self.assertFalse(position.qualification_standards.exists())


class AppointmentKindTests(CommitteeTestCase):
    def selected(self):
        self.shortlist()
        self.schedule()
        self.decide(T.COMMITTEE_RECOMMENDATION, O.RECOMMENDED, committee=self.committee)
        self.decide(T.FINAL_OUTCOME, O.SELECTED)

    def test_kind_is_required_validated_and_correctable(self):
        self.selected()
        for kind in ('', 'volunteer'):
            with self.assertRaises(ValidationError):
                self.decide(T.APPOINTMENT, O.APPOINTED, appointment_reference='A-1',
                            appointment_at=timezone.now(), appointment_kind=kind)
        appointment = self.decide(T.APPOINTMENT, O.APPOINTED, appointment_reference='A-1',
                                  appointment_at=timezone.now(), appointment_kind='job_order')
        self.assertEqual(appointment.appointment_kind, 'job_order')
        corrected = decisions.correct_decision(
            self.approver, appointment, correction_reason='Wrong kind', reason='Minutes',
            appointment_reference='A-1', appointment_at=appointment.appointment_at,
            appointment_kind='part_time')
        self.assertEqual(corrected.get_appointment_kind_display(),
                         'Part-time (plantilla position)')
        log = AuditLog.objects.filter(action='decision.corrected').latest('created_at')
        self.assertIn('appointment_kind', log.changes)

    def test_other_decisions_never_store_a_kind(self):
        decision = self.decide(T.SHORTLISTING, O.SHORTLISTED, appointment_kind='job_order')
        self.assertEqual(decision.appointment_kind, '')

    def test_applicant_sees_the_kind_after_release(self):
        self.selected()
        appointment = self.decide(T.APPOINTMENT, O.APPOINTED, appointment_reference='A-7',
                                  appointment_at=timezone.now(),
                                  appointment_kind='contract_of_service')
        decisions.release_decision(self.approver, appointment, note='Welcome.')
        self.client.force_login(self.application.applicant.user)
        page = self.client.get(reverse('recruitment:my_application', args=[self.application.pk]))
        self.assertContains(page, 'Contract of service')

    def test_appointment_past_the_fill_limit_keeps_the_warning(self):
        Vacancy.objects.filter(pk=self.vacancy.pk).update(
            posted_at=merit.add_months(timezone.localtime(), -10))
        self.selected()
        self.client.force_login(self.approver)
        page = self.client.get(reverse('recruitment:decision_new',
                                       args=[self.application.pk, T.APPOINTMENT]))
        self.assertContains(page, 'publishing the vacancy again')
        appointment = self.decide(T.APPOINTMENT, O.APPOINTED, appointment_reference='A-9',
                                  appointment_at=timezone.now(), appointment_kind='part_time')
        stored = RecruitmentDecision.objects.get(pk=appointment.pk).supporting_references
        self.assertIn('publishing the vacancy again', stored['publication_warning'])
