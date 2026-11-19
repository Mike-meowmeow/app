"""Position catalog: HR-managed reusable vacancy defaults.

Covers catalog management and audit, copying defaults into a vacancy and its
draft requirement version, independence of vacancies from later catalog edits
(and vice versa), inactive positions, the explicit reload-defaults action,
permissions, and compatibility with vacancies created before the catalog.
"""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import ProtectedError
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.test_stage3 import make_user
from audit.models import AuditLog

from . import position_services as catalog
from . import services
from .forms import VacancyForm
from .models import CriterionType, Position, Vacancy, VacancyRequirementVersion, VacancyStatus
from .tests import VacancyTestCase


class CatalogTestCase(VacancyTestCase):
    def setUp(self):
        super().setUp()
        self.math = catalog.create_position(
            self.hr, title='Part-Time Instructor - Mathematics',
            description='Teach undergraduate mathematics courses.')
        catalog.add_document_default(self.hr, self.math, document_type=self.pds,
                                     is_required=True, max_files=1, instructions='Signed')
        catalog.add_document_default(self.hr, self.math, document_type=self.tor,
                                     is_required=False, max_files=2)
        catalog.add_standard_default(self.hr, self.math, criterion_type=CriterionType.EDUCATION,
                                     degree_level='master', field='Mathematics',
                                     description="Master's degree in Mathematics")
        catalog.add_standard_default(self.hr, self.math, criterion_type=CriterionType.EXPERIENCE,
                                     amount=2, description='At least 2 years of teaching')
        catalog.add_standard_default(self.hr, self.math, criterion_type=CriterionType.ELIGIBILITY,
                                     name='RA 1080 (Teacher)', description='LET eligibility')
        catalog.add_competency_default(self.hr, self.math, name='Calculus',
                                       description='Differential and integral calculus')

    def from_catalog(self, position=None, **overrides):
        now = timezone.now()
        data = dict(position=position or self.math, department=self.dept,
                    recruitment_type=self.pti, positions_available=1,
                    opens_at=now - timedelta(hours=1), deadline_at=now + timedelta(days=7))
        data.update(overrides)
        return services.create_vacancy(self.hr, **data)

    @staticmethod
    def contents(version):
        return {
            'documents': sorted((d.document_type.code, d.is_required, d.max_files, d.instructions)
                                for d in version.document_requirements.all()),
            'standards': sorted((s.criterion_type, s.operator, s.unit,
                                 tuple(sorted(s.required_value.items())), s.description)
                                for s in version.qualification_standards.all()),
            'competencies': sorted((c.name, c.description) for c in version.competencies.all()),
        }

    @staticmethod
    def catalog_contents(position):
        return {
            'documents': sorted((d.document_type.code, d.is_required, d.max_files, d.instructions)
                                for d in position.document_requirements.all()),
            'standards': sorted((s.criterion_type, s.operator, s.unit,
                                 tuple(sorted(s.required_value.items())), s.description)
                                for s in position.qualification_standards.all()),
            'competencies': sorted((c.name, c.description) for c in position.competencies.all()),
        }


class CatalogManagementTests(CatalogTestCase):
    def test_structured_defaults_use_the_vacancy_standard_rules(self):
        by_type = {s.criterion_type: s for s in self.math.qualification_standards.all()}
        education = by_type[CriterionType.EDUCATION]
        self.assertEqual((education.operator, education.required_value),
                         ('minimum', {'degree_level': 'master', 'field': 'Mathematics'}))
        experience = by_type[CriterionType.EXPERIENCE]
        self.assertEqual((experience.operator, experience.unit, experience.required_value),
                         ('minimum', 'years', {'amount': '2'}))
        self.assertEqual(by_type[CriterionType.ELIGIBILITY].required_value,
                         {'name': 'RA 1080 (Teacher)'})
        with self.assertRaises(ValidationError):
            catalog.add_standard_default(self.hr, self.math, criterion_type=CriterionType.LICENSE,
                                         description='License')  # license name missing

    def test_titles_are_unique_ignoring_case_and_spacing(self):
        with self.assertRaises(ValidationError):
            catalog.create_position(self.hr, title='  part-time instructor -  MATHEMATICS ',
                                    description='Duplicate')
        other = catalog.create_position(self.hr, title='Part-Time Instructor - Physics',
                                        description='Teach physics.')
        with self.assertRaises(ValidationError):
            catalog.update_position(self.hr, other, title='PART-TIME INSTRUCTOR - MATHEMATICS')

    def test_duplicate_or_inactive_document_defaults_are_rejected(self):
        with self.assertRaises(ValidationError):
            catalog.add_document_default(self.hr, self.math, document_type=self.pds)
        self.tor.is_active = False
        self.tor.save()
        physics = catalog.create_position(self.hr, title='Physics', description='Physics.')
        with self.assertRaises(ValidationError):
            catalog.add_document_default(self.hr, physics, document_type=self.tor)

    def test_catalog_changes_are_audited(self):
        catalog.update_position(self.hr, self.math, description='Updated text.',
                                reason='Curriculum change')
        catalog.set_position_active(self.hr, self.math, False, reason='Not hiring')
        catalog.set_position_active(self.hr, self.math, True)
        item = self.math.competencies.get()
        catalog.remove_default(self.hr, self.math, 'competency', item.pk)
        actions = list(AuditLog.objects.filter(entity_id=str(self.math.pk))
                       .order_by('occurred_at').values_list('action', flat=True))
        for action in ('position.created', 'position.requirement_added', 'position.updated',
                       'position.deactivated', 'position.reactivated',
                       'position.requirement_removed'):
            self.assertIn(action, actions)
        updated = AuditLog.objects.get(action='position.updated', entity_id=str(self.math.pk))
        self.assertEqual(updated.reason, 'Curriculum change')
        self.assertEqual(updated.changes['description']['new'], 'Updated text.')

    def test_only_hr_manages_the_catalog(self):
        for user in (self.approver, self.dept_head, self.applicant,
                     make_user('sysadm', roles.SYSTEM_ADMIN)):
            with self.assertRaises(PermissionDenied):
                catalog.create_position(user, title='X', description='Y')
            with self.assertRaises(PermissionDenied):
                catalog.update_position(user, self.math, description='Changed')
            with self.assertRaises(PermissionDenied):
                catalog.set_position_active(user, self.math, False)
            with self.assertRaises(PermissionDenied):
                catalog.add_competency_default(user, self.math, name='Algebra')
            with self.assertRaises(PermissionDenied):
                catalog.remove_default(user, self.math, 'competency',
                                       self.math.competencies.get().pk)
        self.math.refresh_from_db()
        self.assertTrue(self.math.is_active)
        self.assertEqual(self.math.competencies.count(), 1)

    def test_used_positions_cannot_be_deleted(self):
        self.from_catalog()
        with self.assertRaises(ProtectedError):
            self.math.delete()


class CopyDefaultsTests(CatalogTestCase):
    def test_new_vacancy_copies_title_description_and_requirements(self):
        vacancy = self.from_catalog()
        self.assertEqual(vacancy.position, self.math)
        self.assertEqual(vacancy.position_title, self.math.title)
        self.assertEqual(vacancy.description, self.math.description)
        draft = vacancy.draft_requirements()
        self.assertEqual(draft.version, 1)
        self.assertEqual(self.contents(draft), self.catalog_contents(self.math))
        log = AuditLog.objects.get(action='vacancy.position_defaults_applied',
                                   entity_id=str(vacancy.pk))
        self.assertEqual(log.changes['position'], str(self.math.pk))
        self.assertFalse(log.changes['description_customized'])
        self.assertEqual(len(log.changes['copied']['standards']), 3)

    def test_hr_can_supply_an_edited_description_at_creation(self):
        vacancy = self.from_catalog(description='Evening classes only.')
        self.assertEqual(vacancy.description, 'Evening classes only.')
        self.assertEqual(self.math.description, 'Teach undergraduate mathematics courses.')
        log = AuditLog.objects.get(action='vacancy.position_defaults_applied',
                                   entity_id=str(vacancy.pk))
        self.assertTrue(log.changes['description_customized'])

    def test_copied_rows_are_separate_records(self):
        vacancy = self.from_catalog()
        draft = vacancy.draft_requirements()
        position_ids = set(self.math.competencies.values_list('pk', flat=True))
        draft_ids = set(draft.competencies.values_list('pk', flat=True))
        self.assertFalse(position_ids & draft_ids)

    def test_inactive_document_types_are_not_copied(self):
        self.tor.is_active = False
        self.tor.save()
        vacancy = self.from_catalog()
        codes = [d.document_type.code for d in vacancy.draft_requirements()
                 .document_requirements.all()]
        self.assertEqual(codes, ['PDS'])
        log = AuditLog.objects.get(action='vacancy.position_defaults_applied',
                                   entity_id=str(vacancy.pk))
        self.assertEqual(log.changes['skipped_inactive_documents'], [self.tor.name])

    def test_vacancy_from_complete_position_can_be_published(self):
        vacancy = self.from_catalog()
        self.assertEqual(services.publication_problems(vacancy, vacancy.draft_requirements()),
                         [])
        version = services.publish(self.hr, vacancy)
        self.assertEqual(len(version.qualification_rules['items']), 3)


class IndependenceTests(CatalogTestCase):
    def test_editing_a_vacancy_does_not_change_the_catalog(self):
        before = self.catalog_contents(self.math)
        vacancy = self.from_catalog()
        services.update_vacancy(self.hr, vacancy, description='Changed for this vacancy')
        draft = vacancy.draft_requirements()
        services.remove_requirement_item(self.hr, vacancy, 'competency',
                                         draft.competencies.get().pk)
        services.add_competency(self.hr, vacancy, name='Statistics')
        self.math.refresh_from_db()
        self.assertEqual(self.math.description, 'Teach undergraduate mathematics courses.')
        self.assertEqual(self.catalog_contents(self.math), before)

    def test_catalog_edits_do_not_change_existing_vacancies(self):
        draft_vacancy = self.from_catalog()
        published = self.from_catalog()
        services.publish(self.hr, published)
        draft_before = self.contents(draft_vacancy.draft_requirements())
        published_version = published.current_requirements()
        published_before = self.contents(published_version)
        manifest_before = published_version.qualification_rules

        catalog.update_position(self.hr, self.math, title='Instructor - Mathematics (revised)',
                                description='New default text.')
        catalog.remove_default(self.hr, self.math, 'standard',
                               self.math.qualification_standards.first().pk)
        catalog.add_competency_default(self.hr, self.math, name='Linear Algebra')

        for vacancy in (draft_vacancy, published):
            vacancy.refresh_from_db()
            self.assertEqual(vacancy.position_title, 'Part-Time Instructor - Mathematics')
            self.assertEqual(vacancy.description, 'Teach undergraduate mathematics courses.')
        self.assertEqual(self.contents(draft_vacancy.draft_requirements()), draft_before)
        version = VacancyRequirementVersion.objects.get(pk=published_version.pk)
        self.assertEqual(self.contents(version), published_before)
        self.assertEqual(version.qualification_rules, manifest_before)

    def test_vacancy_title_cannot_be_edited_away_from_its_position(self):
        vacancy = self.from_catalog()
        services.update_vacancy(self.hr, vacancy, position_title='Something else')
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.position_title, self.math.title)
        self.assertNotIn('position_title', VacancyForm(instance=vacancy).fields)

    def test_published_requirement_versions_stay_protected(self):
        vacancy = self.from_catalog()
        version = services.publish(self.hr, vacancy)
        with self.assertRaises(PermissionError):
            version.competencies.first().delete()


class InactivePositionTests(CatalogTestCase):
    def test_inactive_positions_cannot_start_vacancies_but_keep_existing_links(self):
        vacancy = self.from_catalog()
        catalog.set_position_active(self.hr, self.math, False)
        with self.assertRaises(ValidationError):
            self.from_catalog()
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.position_id, self.math.pk)
        services.publish(self.hr, vacancy)  # an existing draft still publishes
        self.assertEqual(Vacancy.objects.filter(position=self.math).count(), 1)

    def test_inactive_positions_are_not_offered_or_accepted_by_the_form(self):
        catalog.set_position_active(self.hr, self.math, False)
        self.assertNotIn(self.math, VacancyForm().fields['position'].queryset)
        self.client.force_login(self.hr)
        now = timezone.localtime()
        response = self.client.post(reverse('recruitment:vacancy_create'), {
            'position': self.math.pk, 'department': self.dept.pk,
            'recruitment_type': self.pti.pk, 'description': '', 'positions_available': 1,
            'opens_at': now.strftime('%Y-%m-%dT%H:%M'),
            'deadline_at': (now + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Vacancy.objects.exists())

    def test_vacancy_creation_requires_a_position(self):
        with self.assertRaises(ValidationError):
            services.create_vacancy(self.hr, position=None, department=self.dept,
                                    recruitment_type=self.pti)


class ReloadDefaultsTests(CatalogTestCase):
    def setUp(self):
        super().setUp()
        self.physics = catalog.create_position(self.hr, title='Part-Time Instructor - Physics',
                                               description='Teach physics.')
        catalog.add_document_default(self.hr, self.physics, document_type=self.pds)
        catalog.add_competency_default(self.hr, self.physics, name='Mechanics')

    def test_reload_requires_confirmation(self):
        vacancy = self.from_catalog()
        with self.assertRaises(ValidationError):
            services.apply_position_defaults(self.hr, vacancy, self.physics)
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.position, self.math)

    def test_confirmed_reload_replaces_draft_and_records_previous_values(self):
        vacancy = self.from_catalog(description='Edited by HR')
        services.add_competency(self.hr, vacancy, name='Added by HR')
        services.apply_position_defaults(self.hr, vacancy, self.physics, confirm=True)
        vacancy.refresh_from_db()
        self.assertEqual((vacancy.position, vacancy.position_title, vacancy.description),
                         (self.physics, self.physics.title, 'Teach physics.'))
        draft = vacancy.draft_requirements()
        self.assertEqual(draft.version, 1)
        self.assertEqual(self.contents(draft), self.catalog_contents(self.physics))
        log = AuditLog.objects.filter(action='vacancy.position_defaults_applied',
                                      entity_id=str(vacancy.pk)).order_by('-occurred_at')[0]
        self.assertEqual(log.changes['replaced']['description'], 'Edited by HR')
        self.assertIn('Added by HR', log.changes['replaced']['competencies'])

    def test_reload_is_refused_once_published(self):
        vacancy = self.from_catalog()
        services.publish(self.hr, vacancy)
        services.start_requirements_update(self.hr, vacancy)
        with self.assertRaises(ValidationError):
            services.apply_position_defaults(self.hr, vacancy, self.physics, confirm=True)

    def test_reload_requires_vacancy_management(self):
        vacancy = self.from_catalog()
        for user in (self.hr2, self.approver):
            with self.assertRaises(PermissionDenied):
                services.apply_position_defaults(user, vacancy, self.physics, confirm=True)

    def test_reload_refuses_inactive_positions(self):
        vacancy = self.from_catalog()
        catalog.set_position_active(self.hr, self.physics, False)
        with self.assertRaises(ValidationError):
            services.apply_position_defaults(self.hr, vacancy, self.physics, confirm=True)


class LegacyVacancyTests(CatalogTestCase):
    def legacy(self):
        """A vacancy as it existed before the catalog: no position link."""
        now = timezone.now()
        vacancy = Vacancy.objects.create(
            department=self.dept, recruitment_type=self.pti, position_title='Old title',
            description='Old description', opens_at=now - timedelta(hours=1),
            deadline_at=now + timedelta(days=5))
        VacancyRequirementVersion.objects.create(vacancy=vacancy, version=1)
        services.assign_staff(self.approver, vacancy, self.hr, 'hr')
        return vacancy

    def test_existing_vacancies_without_a_position_keep_working(self):
        self.assertTrue(Vacancy._meta.get_field('position').null)
        vacancy = self.legacy()
        form = VacancyForm(instance=vacancy)
        self.assertIn('position_title', form.fields)  # still editable as before
        services.update_vacancy(self.hr, vacancy, position_title='Renamed legacy title')
        self.complete(vacancy)
        services.publish(self.hr, vacancy)
        vacancy.refresh_from_db()
        self.assertEqual((vacancy.status, vacancy.position_title, vacancy.position_id),
                         (VacancyStatus.PUBLISHED, 'Renamed legacy title', None))
        self.client.force_login(self.hr)
        response = self.client.get(reverse('recruitment:vacancy_detail', args=[vacancy.pk]))
        self.assertContains(response, 'Created before the position catalog')

    def test_no_positions_are_inferred_from_existing_titles(self):
        self.legacy()
        self.assertFalse(Position.objects.filter(title='Old title').exists())

    def test_legacy_draft_can_be_linked_explicitly(self):
        vacancy = self.legacy()
        services.apply_position_defaults(self.hr, vacancy, self.math, confirm=True)
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.position, self.math)


class ScreenTests(CatalogTestCase):
    def test_hr_manages_catalog_through_screens(self):
        self.client.force_login(self.hr)
        self.assertContains(self.client.get(reverse('recruitment:position_list')),
                            self.math.title)
        response = self.client.post(reverse('recruitment:position_create'), {
            'title': 'Part-Time Instructor - English', 'description': 'Teach English.'})
        english = Position.objects.get(title='Part-Time Instructor - English')
        self.assertRedirects(response, reverse('recruitment:position_detail', args=[english.pk]))
        self.client.post(reverse('recruitment:position_item_add', args=[english.pk, 'standard']),
                         {'criterion_type': 'training', 'amount': '40',
                          'description': 'At least 40 hours of relevant training'})
        standard = english.qualification_standards.get()
        self.assertEqual((standard.unit, standard.required_value), ('hours', {'amount': '40'}))
        self.client.post(reverse('recruitment:position_edit', args=[english.pk]),
                         {'title': 'Part-Time Instructor - English', 'description': 'Revised.',
                          'reason': ''})
        english.refresh_from_db()
        self.assertEqual(english.description, 'Revised.')
        self.client.post(reverse('recruitment:position_set_active',
                                 args=[english.pk, 'deactivate']))
        english.refresh_from_db()
        self.assertFalse(english.is_active)
        # duplicate title through the form is rejected with a message, not a crash
        response = self.client.post(reverse('recruitment:position_create'), {
            'title': 'part-time instructor - english', 'description': 'Dup'})
        self.assertContains(response, 'already exists')

    def test_create_vacancy_screen_offers_active_positions_with_defaults(self):
        catalog.create_position(self.hr, title='Retired position', description='Old')
        catalog.set_position_active(self.hr, Position.objects.get(title='Retired position'),
                                    False)
        self.client.force_login(self.hr)
        response = self.client.get(reverse('recruitment:vacancy_create'))
        self.assertContains(response, self.math.title)
        self.assertNotContains(response, 'Retired position')
        self.assertContains(response, 'id="position-defaults"')
        self.assertContains(response, 'Differential and integral calculus')
        self.assertNotContains(response, 'name="position_title"')

    def test_create_vacancy_screen_copies_defaults_and_keeps_edited_description(self):
        self.client.force_login(self.hr)
        now = timezone.localtime()
        response = self.client.post(reverse('recruitment:vacancy_create'), {
            'position': self.math.pk, 'department': self.dept.pk,
            'recruitment_type': self.pti.pk, 'description': 'Saturday classes.',
            'positions_available': 2,
            'opens_at': now.strftime('%Y-%m-%dT%H:%M'),
            'deadline_at': (now + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')})
        vacancy = Vacancy.objects.get()
        self.assertRedirects(response, reverse('recruitment:vacancy_detail', args=[vacancy.pk]))
        self.assertEqual(vacancy.description, 'Saturday classes.')
        self.assertEqual(vacancy.draft_requirements().qualification_standards.count(), 3)
        detail = self.client.get(response.url)
        self.assertContains(detail, 'Load defaults from a position')

    def test_blank_description_uses_the_position_default(self):
        self.client.force_login(self.hr)
        now = timezone.localtime()
        self.client.post(reverse('recruitment:vacancy_create'), {
            'position': self.math.pk, 'department': self.dept.pk,
            'recruitment_type': self.pti.pk, 'description': '', 'positions_available': 1,
            'opens_at': now.strftime('%Y-%m-%dT%H:%M'),
            'deadline_at': (now + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')})
        self.assertEqual(Vacancy.objects.get().description, self.math.description)

    def test_reload_screen_needs_the_confirmation_tick(self):
        vacancy = self.from_catalog()
        url = reverse('recruitment:vacancy_apply_position', args=[vacancy.pk])
        self.client.force_login(self.hr)
        response = self.client.post(url, {'position': self.math.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AuditLog.objects.filter(action='vacancy.position_defaults_applied',
                                                 entity_id=str(vacancy.pk)).count(), 1)
        response = self.client.post(url, {'position': self.math.pk, 'confirm': 'on'})
        self.assertRedirects(response, reverse('recruitment:vacancy_detail', args=[vacancy.pk]))
        self.assertEqual(AuditLog.objects.filter(action='vacancy.position_defaults_applied',
                                                 entity_id=str(vacancy.pk)).count(), 2)

    def test_catalog_screens_are_hr_only(self):
        urls = [reverse('recruitment:position_list'), reverse('recruitment:position_create'),
                reverse('recruitment:position_detail', args=[self.math.pk]),
                reverse('recruitment:position_edit', args=[self.math.pk])]
        for user in (self.approver, self.dept_head, self.applicant,
                     make_user('sysadm', roles.SYSTEM_ADMIN)):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403, (user, url))
            response = self.client.post(reverse('recruitment:position_set_active',
                                                args=[self.math.pk, 'deactivate']))
            self.assertEqual(response.status_code, 403)
        self.client.logout()
        response = self.client.get(urls[0])
        self.assertEqual(response.status_code, 302)
        self.math.refresh_from_db()
        self.assertTrue(self.math.is_active)

    def test_unassigned_hr_cannot_reload_defaults_through_the_screen(self):
        vacancy = self.from_catalog()
        self.client.force_login(self.hr2)
        response = self.client.get(reverse('recruitment:vacancy_apply_position',
                                           args=[vacancy.pk]))
        self.assertEqual(response.status_code, 404)  # unassigned vacancies look absent
