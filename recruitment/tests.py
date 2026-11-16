"""Stage 4 vacancy tests: FR-005 lifecycle and versioning, FR-026 publication
checks, FR-002 assignment-based access, and FR-015 restricted search."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.test_stage3 import make_user, seed
from audit.models import AuditLog
from core.models import Department, RecruitmentType
from documents.models import DocumentType

from . import selectors, services
from .models import (AssignmentRole, CriterionType, Vacancy, VacancyDocumentRequirement,
                     VacancyStatus)


class VacancyTestCase(TestCase):
    def setUp(self):
        seed()
        self.dept = Department.objects.create(name='College of Education', code='COED')
        self.pti = RecruitmentType.objects.get(code='PTI')
        self.pds = DocumentType.objects.get(code='PDS')
        self.tor = DocumentType.objects.get(code='TOR')
        self.hr = make_user('hr1', roles.HR)
        self.hr2 = make_user('hr2', roles.HR)
        self.approver = make_user('head', roles.APPROVER)
        self.dept_head = make_user('dhead', roles.DEPARTMENT_HEAD)
        self.applicant = make_user('appl', roles.APPLICANT)

    def new_vacancy(self, actor=None, **overrides):
        now = timezone.now()
        data = dict(department=self.dept, recruitment_type=self.pti,
                    position_title='Part-Time Instructor - Mathematics',
                    description='Teach undergraduate mathematics.', positions_available=2,
                    opens_at=now - timedelta(hours=1), deadline_at=now + timedelta(days=7))
        data.update(overrides)
        return services.create_vacancy(actor or self.hr, **data)

    def complete(self, vacancy, actor=None):
        actor = actor or self.hr
        services.add_document_requirement(actor, vacancy, document_type=self.pds,
                                          is_required=True, max_files=1, instructions='')
        services.add_qualification_standard(
            actor, vacancy, criterion_type=CriterionType.EDUCATION, degree_level='master',
            field='Mathematics', description="Master's degree in Mathematics")
        services.add_competency(actor, vacancy, name='Calculus')
        return vacancy

    def published(self, **overrides):
        vacancy = self.complete(self.new_vacancy(**overrides))
        services.publish(self.hr, vacancy)
        vacancy.refresh_from_db()
        return vacancy


class LifecycleTests(VacancyTestCase):
    def test_create_makes_draft_with_first_draft_version_and_assigns_creator(self):
        vacancy = self.new_vacancy()
        self.assertEqual(vacancy.status, VacancyStatus.DRAFT)
        self.assertEqual(vacancy.draft_requirements().version, 1)
        self.assertTrue(selectors.can_manage(self.hr, vacancy))
        self.assertTrue(AuditLog.objects.filter(action='vacancy.created',
                                                entity_id=str(vacancy.pk)).exists())

    def test_only_hr_creates_vacancies(self):
        with self.assertRaises(PermissionDenied):
            self.new_vacancy(actor=self.approver)

    def test_publication_reports_missing_configuration(self):
        vacancy = self.new_vacancy(deadline_at=None)
        with self.assertRaises(ValidationError) as ctx:
            services.publish(self.hr, vacancy)
        text = ' '.join(ctx.exception.messages)
        self.assertIn('deadline', text)
        self.assertIn('required document', text)
        self.assertIn('qualification standard', text)
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.status, VacancyStatus.DRAFT)

    def test_publish_freezes_version_with_manifests(self):
        vacancy = self.published()
        self.assertEqual(vacancy.status, VacancyStatus.PUBLISHED)
        self.assertIsNotNone(vacancy.posted_at)
        version = vacancy.current_requirements()
        self.assertEqual(version.version, 1)
        self.assertEqual(version.published_by, self.hr)
        self.assertEqual(version.document_checklist['schema_version'], 1)
        self.assertEqual(version.document_checklist['items'][0]['document_type'], 'PDS')
        self.assertEqual(version.qualification_rules['items'][0]['required_value'],
                         {'degree_level': 'master', 'field': 'Mathematics'})
        self.assertIsNone(vacancy.draft_requirements())

    def test_published_versions_and_items_are_immutable(self):
        vacancy = self.published()
        version = vacancy.current_requirements()
        version.qualification_rules = {}
        with self.assertRaises(PermissionError):
            version.save()
        with self.assertRaises(PermissionError):
            version.delete()
        with self.assertRaises(PermissionError):
            version.document_requirements.first().delete()
        with self.assertRaises(PermissionError):
            VacancyDocumentRequirement.objects.create(requirement_version=version,
                                                      document_type=self.tor)
        with self.assertRaises(ValidationError):  # no draft to change
            services.add_competency(self.hr, vacancy, name='Statistics')

    def test_requirement_change_creates_new_version_and_keeps_old(self):
        vacancy = self.published()
        v1 = vacancy.current_requirements()
        v1_manifest = v1.document_checklist
        draft = services.start_requirements_update(self.hr, vacancy)
        self.assertEqual(draft.version, 2)
        self.assertEqual(draft.document_requirements.count(), 1)  # copied
        services.add_document_requirement(self.hr, vacancy, document_type=self.tor,
                                          is_required=True, max_files=1, instructions='')
        with self.assertRaises(ValidationError):
            services.publish(self.hr, vacancy)  # reason required
        services.publish(self.hr, vacancy, reason='TOR now required')
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.current_requirements().version, 2)
        v1.refresh_from_db()
        self.assertEqual(v1.document_checklist, v1_manifest)
        self.assertEqual(v1.document_requirements.count(), 1)
        event = AuditLog.objects.get(action='vacancy.requirements_published')
        self.assertEqual(event.reason, 'TOR now required')
        response = self.client.get(reverse('recruitment:public_detail', args=[vacancy.pk]))
        self.assertContains(response, 'requirements were updated')
        self.assertContains(response, 'Transcript of Records')

    def test_discarding_a_draft_keeps_published_version(self):
        vacancy = self.published()
        services.start_requirements_update(self.hr, vacancy)
        services.discard_requirements_draft(self.hr, vacancy)
        self.assertIsNone(vacancy.draft_requirements())
        self.assertEqual(vacancy.current_requirements().version, 1)

    def test_editing_published_vacancy_needs_reason_and_keeps_locked_fields(self):
        vacancy = self.published()
        with self.assertRaises(ValidationError):
            services.update_vacancy(self.hr, vacancy, positions_available=3)
        new_deadline = vacancy.deadline_at + timedelta(days=3)
        services.update_vacancy(self.hr, vacancy, reason='Extended', positions_available=3,
                                deadline_at=new_deadline, position_title='Changed')
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.positions_available, 3)
        self.assertEqual(vacancy.deadline_at, new_deadline)
        self.assertEqual(vacancy.position_title, 'Part-Time Instructor - Mathematics')
        event = AuditLog.objects.get(action='vacancy.updated')
        self.assertEqual(event.changes['positions_available'], {'old': 2, 'new': 3})
        with self.assertRaises(ValidationError):
            services.update_vacancy(self.hr, vacancy, reason='x',
                                    deadline_at=timezone.now() - timedelta(days=1))

    def test_close_cancel_and_delete_rules(self):
        draft = self.new_vacancy()
        services.delete_draft_vacancy(self.hr, draft)
        self.assertFalse(Vacancy.objects.filter(pk=draft.pk).exists())

        vacancy = self.published()
        with self.assertRaises(ValidationError):
            services.delete_draft_vacancy(self.hr, vacancy)
        services.close_vacancy(self.hr, vacancy, reason='Enough applicants')
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.status, VacancyStatus.CLOSED)
        with self.assertRaises(ValidationError):
            services.close_vacancy(self.hr, vacancy, reason='again')
        services.cancel_vacancy(self.hr, vacancy, reason='Budget withdrawn')
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.status, VacancyStatus.CANCELED)
        self.assertIsNotNone(vacancy.canceled_at)
        with self.assertRaises(ValidationError):
            services.update_vacancy(self.hr, vacancy, reason='x', description='y')

    def test_database_rejects_invalid_values(self):
        now = timezone.now()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vacancy.objects.create(department=self.dept, recruitment_type=self.pti,
                                   position_title='X', description='Y',
                                   opens_at=now, deadline_at=now - timedelta(days=1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vacancy.objects.create(department=self.dept, recruitment_type=self.pti,
                                   position_title='X', description='Y', positions_available=0)


class DeadlineAndPublicViewTests(VacancyTestCase):
    def test_application_window(self):
        open_now = self.published()
        self.assertTrue(open_now.is_accepting_applications())
        upcoming = self.published(opens_at=timezone.now() + timedelta(days=1))
        self.assertFalse(upcoming.is_accepting_applications())
        self.assertEqual(upcoming.application_window, 'upcoming')
        draft = self.new_vacancy()
        self.assertFalse(draft.is_accepting_applications())

    def test_cannot_publish_with_past_deadline(self):
        vacancy = self.complete(self.new_vacancy(
            opens_at=timezone.now() - timedelta(days=5),
            deadline_at=timezone.now() - timedelta(days=1)))
        with self.assertRaises(ValidationError):
            services.publish(self.hr, vacancy)

    def test_public_list_shows_only_published_and_not_ended(self):
        shown = self.published(position_title='Shown vacancy')
        ended = self.published(position_title='Ended vacancy')
        Vacancy.objects.filter(pk=ended.pk).update(
            opens_at=timezone.now() - timedelta(days=9),
            deadline_at=timezone.now() - timedelta(days=1))
        self.new_vacancy(position_title='Draft vacancy')
        response = self.client.get(reverse('recruitment:public_list'))
        self.assertContains(response, 'Shown vacancy')
        self.assertNotContains(response, 'Ended vacancy')
        self.assertNotContains(response, 'Draft vacancy')
        self.assertContains(self.client.get(reverse('recruitment:public_detail',
                                                    args=[shown.pk])), 'Asia/Manila')

    def test_drafts_are_not_publicly_viewable(self):
        draft = self.new_vacancy()
        response = self.client.get(reverse('recruitment:public_detail', args=[draft.pk]))
        self.assertEqual(response.status_code, 404)


class AccessTests(VacancyTestCase):
    def test_unassigned_hr_cannot_see_or_manage(self):
        vacancy = self.published()
        self.client.force_login(self.hr2)
        self.assertNotContains(self.client.get(reverse('recruitment:vacancy_list')),
                               vacancy.position_title)
        self.assertEqual(self.client.get(reverse('recruitment:vacancy_detail',
                                                 args=[vacancy.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('recruitment:vacancy_close',
                                                  args=[vacancy.pk]),
                                          {'reason': 'x'}).status_code, 404)
        with self.assertRaises(PermissionDenied):
            services.close_vacancy(self.hr2, vacancy, reason='x')

    def test_assignment_grants_access_and_is_audited(self):
        vacancy = self.published()
        services.assign_staff(self.hr, vacancy, self.hr2, AssignmentRole.HR, reason='Backup')
        services.assign_staff(self.hr, vacancy, self.dept_head, AssignmentRole.DEPARTMENT_HEAD)
        self.assertTrue(selectors.can_manage(self.hr2, vacancy))
        self.assertTrue(selectors.can_view(self.dept_head, vacancy))
        self.assertFalse(selectors.can_manage(self.dept_head, vacancy))
        self.assertEqual(AuditLog.objects.filter(action='vacancy.assignment_added').count(), 2)

        assignment = vacancy.assignments.get(user=self.hr2)
        services.end_assignment(self.hr, assignment, reason='Reassigned')
        self.assertFalse(selectors.can_view(self.hr2, vacancy))
        services.assign_staff(self.hr, vacancy, self.hr2, AssignmentRole.HR)
        self.assertTrue(AuditLog.objects.filter(action='vacancy.assignment_reactivated').exists())

    def test_assignment_requires_matching_role_and_keeps_one_hr(self):
        vacancy = self.new_vacancy()
        with self.assertRaises(ValidationError):
            services.assign_staff(self.hr, vacancy, self.applicant, AssignmentRole.HR)
        with self.assertRaises(ValidationError):
            services.assign_staff(self.hr, vacancy, self.hr2, AssignmentRole.DEPARTMENT_HEAD)
        with self.assertRaises(ValidationError):
            services.end_assignment(self.hr, vacancy.assignments.get(user=self.hr))
        with self.assertRaises(PermissionDenied):
            services.assign_staff(self.hr2, vacancy, self.hr2, AssignmentRole.HR)

    def test_department_membership_alone_grants_nothing(self):
        vacancy = self.published()
        self.dept_head.department = self.dept
        self.dept_head.save()
        self.assertFalse(selectors.can_view(self.dept_head, vacancy))

    def test_approver_sees_all_but_cannot_edit(self):
        vacancy = self.published()
        self.assertTrue(selectors.can_view(self.approver, vacancy))
        self.assertFalse(selectors.can_manage(self.approver, vacancy))
        self.assertTrue(selectors.can_assign_staff(self.approver, vacancy))

    def test_applicants_and_admins_cannot_open_staff_screens(self):
        admin = make_user('sysadm', roles.SYSTEM_ADMIN)
        for user in (self.applicant, admin):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('recruitment:vacancy_list')).status_code,
                             403)

    def test_search_filters_and_counts_respect_access(self):
        math = self.published(position_title='Mathematics instructor')
        self.published(position_title='English instructor')
        services.assign_staff(self.hr, math, self.hr2, AssignmentRole.HR)
        self.client.force_login(self.hr2)
        url = reverse('recruitment:vacancy_list')
        self.assertContains(self.client.get(url), '1 result')
        response = self.client.get(url, {'q': 'English'})
        self.assertContains(response, '0 results')
        response = self.client.get(url, {'q': math.reference, 'status': 'published'})
        self.assertContains(response, 'Mathematics instructor')
        self.client.force_login(self.hr)
        self.assertContains(self.client.get(url, {'q': 'instructor'}), '2 results')
        self.assertContains(self.client.get(url, {'status': 'draft'}), '0 results')


class HttpFlowTests(VacancyTestCase):
    def test_hr_creates_configures_and_publishes_through_screens(self):
        self.client.force_login(self.hr)
        now = timezone.localtime()
        response = self.client.post(reverse('recruitment:vacancy_create'), {
            'position_title': 'Part-Time Instructor - Physics', 'department': self.dept.pk,
            'recruitment_type': self.pti.pk, 'description': 'Teach physics.',
            'positions_available': 1,
            'opens_at': (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'deadline_at': (now + timedelta(days=5)).strftime('%Y-%m-%dT%H:%M')})
        vacancy = Vacancy.objects.get(position_title='Part-Time Instructor - Physics')
        self.assertRedirects(response, reverse('recruitment:vacancy_detail', args=[vacancy.pk]))
        detail = self.client.get(response.url)
        self.assertContains(detail, 'Complete these before publishing')

        add = lambda kind, data: self.client.post(  # noqa: E731
            reverse('recruitment:requirement_add', args=[vacancy.pk, kind]), data)
        add('document', {'document_type': self.pds.pk, 'is_required': 'on', 'max_files': 1})
        add('standard', {'criterion_type': 'education', 'degree_level': '',
                         'description': 'Degree'})  # missing degree level -> rejected
        self.assertEqual(vacancy.draft_requirements().qualification_standards.count(), 0)
        add('standard', {'criterion_type': 'experience', 'amount': '2',
                         'description': 'At least 2 years of teaching'})
        self.client.post(reverse('recruitment:vacancy_publish', args=[vacancy.pk]))
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.status, VacancyStatus.PUBLISHED)
        standard = vacancy.current_requirements().qualification_standards.get()
        self.assertEqual((standard.operator, standard.unit), ('minimum', 'years'))
