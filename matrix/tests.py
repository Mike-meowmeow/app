"""Stage 6 tests: official roster generation (FR-021), review/approval/
versioning (FR-022), and Excel/PDF export (FR-023), including the guide's
Stage 6 gate and the core roster acceptance scenarios."""

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts import roles
from accounts.test_stage3 import make_user
from applicants.models import Applicant, EducationalRecord
from audit.models import AuditLog
from core.models import RecruitmentType
from documents import services as documents
from recruitment import application_services as applications
from recruitment.models import Application, ApplicationStatus, FactReviewStatus, FactType
from recruitment.test_applications import ApplicationTestCase, pdf

from . import builder, layout, services
from .models import (ApplicantRoster, ExportFormat, RosterEntry, RosterExport, RosterStatus,
                     TemplateStatus)

SIGNATORIES = {'schema_version': 1,
               'prepared_by': {'name': 'Sample Preparer', 'position': 'Administrative Aide'},
               'noted_by': {'name': 'Sample Head', 'position': 'Head, HRMDO'}}


class RosterTestCase(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.private = tempfile.mkdtemp()
        self.roster_override = override_settings(
            PRIVATE_STORAGE_ROOT=Path(self.private),
            EXPORT_STORAGE_ROOT=Path(self.private) / 'exports')
        self.roster_override.enable()
        source = layout.build_sample_template(Path(self.private) / 'source.xlsx')
        self.template = services.register_template(source, self.pti, name='PART-TIME test',
                                                   signatories=SIGNATORIES)
        services.approve_template(self.approver, self.template)
        self.counter = 0

    def tearDown(self):
        self.roster_override.disable()
        shutil.rmtree(self.private, ignore_errors=True)
        super().tearDown()

    def applicant_with(self, last_name, *, bachelors=(), vacancy=None):
        self.counter += 1
        user = make_user(f'app{self.counter}', roles.APPLICANT, last_name=last_name)
        profile = self.complete_profile(user)
        for program, institution, status in bachelors:
            EducationalRecord.objects.create(applicant=profile, degree_level='bachelor',
                                             program=program, institution=institution,
                                             completion_status=status,
                                             completed_on=date(2010, 3, 1))
        return user

    def submit_as(self, user, vacancy=None):
        vacancy = vacancy or self.vacancy
        application = applications.start_application(user, vacancy)[0]
        documents.upload(user, application, self.pds, pdf('pds.pdf'))
        applications.submit(user, application)
        application.refresh_from_db()
        return application

    def generate(self, **kwargs):
        return services.generate(self.hr, self.vacancy, **kwargs)

    def accept_all(self, application):
        for fact in application.facts.filter(review_status=FactReviewStatus.PENDING):
            applications.review_fact(self.hr, fact, decision=FactReviewStatus.ACCEPTED)
        for fact in application.facts.filter(fact_type__in=['experience', 'training'],
                                             review_status=FactReviewStatus.ACCEPTED):
            applications.set_relevance(self.hr, fact, 'relevant')
        for doc in application.documents.all():
            documents.verify(self.hr, doc.current_version(), status='verified')

    def acknowledge_all(self, roster):
        for entry in roster.entries.all():
            if entry.needs_acknowledgment:
                services.update_entry(self.hr, entry, acknowledge=True)

    def pending_roster(self):
        self.submit_as(self.applicant_with('Reyes'))
        roster = self.generate()
        self.acknowledge_all(roster)
        self.assertEqual(services.submit_for_approval(self.hr, roster), [])
        roster.refresh_from_db()
        return roster

    def export_files(self):
        return [p for p in (Path(self.private) / 'exports').rglob('*') if p.is_file()]


class MembershipTests(RosterTestCase):
    def test_gate_all_submitted_states_once_drafts_and_other_vacancies_excluded(self):
        submitted = self.submit_as(self.applicant_with('Abad'))
        shortlisted = self.submit_as(self.applicant_with('Bautista'))
        not_selected = self.submit_as(self.applicant_with('Cruz'))
        withdrawn = self.submit_as(self.applicant_with('Diaz'))
        Application.objects.filter(pk=shortlisted.pk).update(status=ApplicationStatus.SHORTLISTED)
        Application.objects.filter(pk=not_selected.pk).update(
            status=ApplicationStatus.NOT_SELECTED)
        applications.withdraw(withdrawn.applicant.user, withdrawn)
        draft_user = self.applicant_with('Esteban')
        applications.start_application(draft_user, self.vacancy)
        other_vacancy = self.published(position_title='Part-Time Instructor - English')
        other = self.submit_as(self.applicant_with('Flores'), vacancy=other_vacancy)

        roster = self.generate()
        ids = list(roster.entries.values_list('application_id', flat=True))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {submitted.pk, shortlisted.pk, not_selected.pk, withdrawn.pk})
        self.assertNotIn(other.pk, ids)
        remarks = dict(roster.entries.values_list('application_id', 'remarks'))
        self.assertIn('Withdrawn', remarks[withdrawn.pk])
        self.assertIn('Not selected', remarks[not_selected.pk])
        # stable display order: last name, first name; sequence = position only
        names = [e.exported_values['B'] for e in roster.entries.order_by('sequence_number')]
        self.assertEqual([n.removesuffix(builder.UNVERIFIED_MARK) for n in names],
                         ['Abad', 'Bautista', 'Cruz', 'Diaz'])
        self.assertEqual(roster.source_revision_manifest['applications'][str(withdrawn.pk)],
                         Application.objects.get(pk=withdrawn.pk).revision)

    def test_cutoff_and_empty_roster(self):
        application = self.submit_as(self.applicant_with('Abad'))
        roster = self.generate(cutoff_at=application.submitted_at - timedelta(seconds=1))
        self.assertEqual(roster.entries.count(), 0)
        export = services.create_export(self.hr, roster, ExportFormat.XLSX)
        ws = load_workbook(services.export_path(export))[layout.SHEET]
        self.assertIn('No submitted applications', ws[f'A{layout.FIRST_DATA_ROW}'].value)

    def test_pending_verification_keeps_row_with_flags_instead_of_excluding(self):
        application = self.submit_as(self.applicant_with('Abad'))
        roster = self.generate()
        entry = roster.entries.get()
        self.assertEqual(entry.application_id, application.pk)
        self.assertEqual(entry.review_flags['documents']['count'], 1)
        self.assertEqual(entry.flagged_columns['B'], builder.UNVERIFIED)
        self.assertTrue(entry.exported_values['B'].endswith(builder.UNVERIFIED_MARK))
        self.assertIn('Document verification pending', entry.remarks)
        self.assertTrue(entry.needs_acknowledgment)

    def test_one_version_in_preparation_at_a_time(self):
        self.generate()
        with self.assertRaises(ValidationError):
            self.generate()


class ValueTests(RosterTestCase):
    def test_degree_institution_pairing_ongoing_label_and_text_values(self):
        user = self.applicant_with('Abad', bachelors=[
            ('BS Mathematics', 'EVSU Ormoc', 'completed'),
            ('BS Education', 'Leyte Normal University', 'completed')])
        profile = Applicant.objects.get(user=user)
        EducationalRecord.objects.create(applicant=profile, degree_level='doctorate',
                                         program='PhD Mathematics', institution='UP Diliman',
                                         completion_status='ongoing')
        application = self.submit_as(user)
        self.accept_all(application)
        values = self.generate().entries.get().exported_values
        self.assertEqual(values['L'], '1. BS Mathematics\n2. BS Education')
        self.assertEqual(values['O'], '1. EVSU Ormoc\n2. Leyte Normal University')
        self.assertEqual(values['M'], 'MA Mathematics')
        self.assertEqual(values['P'], 'EVSU')
        self.assertEqual(values['N'], 'PhD Mathematics (Ongoing)')
        self.assertEqual(values['Q'], 'UP Diliman')
        self.assertEqual(values['E'], '09171234567')  # leading zero kept as text
        self.assertEqual(values['G'], 'January 1, 1990')
        self.assertEqual(values['K'], builder.NOT_APPLICABLE_TEXT)

    def test_missing_bachelor_flagged_and_accepted_facts_clear_flags(self):
        application = self.submit_as(self.applicant_with('Abad'))  # master only
        entry = self.generate().entries.get()
        self.assertEqual(entry.flagged_columns['L'], builder.MISSING)
        self.assertEqual(entry.exported_values['L'], builder.MISSING_TEXT)

        self.accept_all(application)
        roster = ApplicantRoster.objects.get()
        self.assertTrue(roster.requires_regeneration)  # facts changed after generation
        services.regenerate(self.hr, roster)
        entry = roster.entries.get()
        self.assertNotIn('B', entry.flagged_columns)
        self.assertNotIn('documents', entry.review_flags)
        self.assertEqual(entry.exported_values['B'], 'Abad')
        self.assertTrue(entry.needs_acknowledgment)  # bachelor still missing

    def test_rejected_items_are_omitted_and_hr_corrections_used(self):
        user = self.applicant_with('Abad', bachelors=[('BS Math', 'EVSU', 'completed')])
        application = self.submit_as(user)
        master = application.facts.get(fact_type=FactType.EDUCATION,
                                       value__degree_level='master')
        applications.review_fact(self.hr, master, decision=FactReviewStatus.REJECTED,
                                 reason='No diploma submitted')
        bachelor = application.facts.get(fact_type=FactType.EDUCATION,
                                         value__degree_level='bachelor')
        applications.correct_fact(self.hr, bachelor, {**bachelor.value,
                                                      'program': 'BS Mathematics'},
                                  reason='Per transcript')
        values = self.generate().entries.get().exported_values
        self.assertEqual(values['M'], '')
        self.assertEqual(values['L'], 'BS Mathematics')


class WorkflowTests(RosterTestCase):
    def test_submission_for_approval_needs_resolved_flags(self):
        self.submit_as(self.applicant_with('Reyes'))
        roster = self.generate()
        problems = services.submit_for_approval(self.hr, roster)
        self.assertTrue(any('unresolved flags' in p for p in problems))
        roster.refresh_from_db()
        self.assertEqual(roster.status, RosterStatus.DRAFT)
        self.acknowledge_all(roster)
        self.assertEqual(services.submit_for_approval(self.hr, roster), [])
        roster.refresh_from_db()
        self.assertEqual(roster.status, RosterStatus.PENDING_APPROVAL)
        self.assertEqual(list(roster.events.values_list('to_status', flat=True)),
                         ['draft', 'pending_approval'])

    def test_signatories_required(self):
        self.submit_as(self.applicant_with('Reyes'))
        roster = self.generate()
        self.acknowledge_all(roster)
        services.update_signatories(self.hr, roster, {
            'schema_version': 1, 'prepared_by': {'name': '', 'position': ''},
            'noted_by': SIGNATORIES['noted_by']})
        problems = services.submit_for_approval(self.hr, roster)
        self.assertTrue(any('Prepared by' in p for p in problems))

    def test_only_designated_approver_and_no_self_approval(self):
        roster = self.pending_roster()
        with self.assertRaises(PermissionDenied):
            services.approve(self.hr, roster)
        self.hr.groups.add(*roles_groups(roles.APPROVER))
        roles.clear_role_cache(self.hr)
        problems = services.approve(self.hr, roster)
        self.assertTrue(any('another designated approver' in p for p in problems))
        with override_settings(ROSTER_ALLOW_SELF_APPROVAL=True):
            self.assertEqual(services.approve(self.hr, roster), [])

    def test_approve_records_details_and_locks_content(self):
        roster = self.pending_roster()
        self.assertEqual(services.approve(self.approver, roster, comments='OK'), [])
        roster.refresh_from_db()
        self.assertEqual(roster.status, RosterStatus.APPROVED)
        self.assertEqual(roster.approved_by, self.approver)
        log = AuditLog.objects.get(action='roster.approved')
        self.assertEqual(log.changes['template_version'], self.template.version)
        self.assertIn('manifest', log.changes)
        entry = roster.entries.get()
        with self.assertRaises(ValidationError):
            services.update_entry(self.hr, entry, remarks='changed')
        entry.remarks = 'sneaky'
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(ValidationError):
            services.create_export(self.hr, roster, ExportFormat.XLSX)  # no new draft files

    def test_return_needs_comments_then_hr_resubmits(self):
        roster = self.pending_roster()
        with self.assertRaises(ValidationError):
            services.return_for_correction(self.approver, roster, comments=' ')
        services.return_for_correction(self.approver, roster, comments='Fix remarks')
        roster.refresh_from_db()
        self.assertEqual(roster.status, RosterStatus.RETURNED)
        services.update_entry(self.hr, roster.entries.get(), remarks='Complete')
        self.assertEqual(services.submit_for_approval(self.hr, roster), [])

    def test_new_submission_before_approval_is_detected(self):
        roster = self.pending_roster()
        self.submit_as(self.applicant_with('Zamora'))  # within cutoff (deadline)
        roster.refresh_from_db()
        self.assertTrue(roster.requires_regeneration)
        problems = services.approve(self.approver, roster)
        self.assertTrue(problems)
        roster.refresh_from_db()
        self.assertEqual(roster.status, RosterStatus.PENDING_APPROVAL)

    def test_change_detected_at_approval_even_without_flag(self):
        roster = self.pending_roster()
        application = self.submit_as(self.applicant_with('Zamora'))
        ApplicantRoster.objects.filter(pk=roster.pk).update(requires_regeneration=False)
        problems = services.approve(self.approver, roster)
        self.assertTrue(any('1 new submission' in p for p in problems))
        roster.refresh_from_db()
        self.assertTrue(roster.requires_regeneration)  # saved although approval refused
        self.assertIsNotNone(application.pk)

    def test_gate_approved_roster_survives_later_changes_and_new_version_supersedes(self):
        roster = self.pending_roster()
        services.approve(self.approver, roster)
        roster.refresh_from_db()
        services.release(self.approver, roster)
        roster.refresh_from_db()
        self.assertIsNotNone(roster.released_at)
        official = list(roster.exports.filter(is_draft=False))
        self.assertEqual({e.format for e in official}, {'xlsx', 'pdf'})
        before_values = list(roster.entries.values_list('exported_values', flat=True))
        before_hashes = {e.pk: services.export_path(e).read_bytes() for e in official}

        application = roster.entries.get().application
        applications.withdraw(application.applicant.user, application)  # later change
        roster.refresh_from_db()
        self.assertTrue(roster.requires_regeneration)
        self.assertEqual(list(roster.entries.values_list('exported_values', flat=True)),
                         before_values)

        new = services.generate(self.hr, self.vacancy, cutoff_at=roster.cutoff_at)
        self.assertEqual(new.version, 2)
        self.assertIn('Withdrawn', new.entries.get().remarks)
        self.acknowledge_all(new)
        self.assertEqual(services.submit_for_approval(self.hr, new), [])
        self.assertEqual(services.approve(self.approver, new), [])
        roster.refresh_from_db()
        self.assertEqual(roster.status, RosterStatus.SUPERSEDED)
        for export in official:
            self.assertEqual(services.export_path(export).read_bytes(), before_hashes[export.pk])

    def test_regeneration_keeps_hr_remarks_and_unchanged_acknowledgments(self):
        self.submit_as(self.applicant_with('Reyes'))
        roster = self.generate()
        entry = roster.entries.get()
        services.update_entry(self.hr, entry, remarks='Pending TOR', acknowledge=True)
        services.regenerate(self.hr, roster)
        entry = roster.entries.get()
        self.assertEqual(entry.remarks, 'Pending TOR')
        self.assertEqual(entry.exported_values['T'], 'Pending TOR')
        self.assertTrue(entry.review_flags.get('acknowledged'))

    def test_release_requires_approval(self):
        roster = self.pending_roster()
        with self.assertRaises(ValidationError):
            services.release(self.approver, roster)


def roles_groups(*names):
    from django.contrib.auth.models import Group
    return Group.objects.filter(name__in=names)


class ExportTests(RosterTestCase):
    def test_gate_seven_plus_rows_text_only_no_t29_signatories_below(self):
        for i, name in enumerate(['Abad', 'Bautista', 'Cruz', 'Diaz', 'Esteban', 'Flores',
                                  'Garcia', 'Hernandez']):
            user = self.applicant_with(name)
            if i == 0:
                profile = Applicant.objects.get(user=user)
                profile.address = '=HYPERLINK("http://example.com","x")'
                profile.save()
            self.submit_as(user)
        roster = self.generate()
        entry = roster.entries.get(sequence_number=1)
        services.update_entry(self.hr, entry, remarks='+1 formula-like remark')
        export = services.create_export(self.hr, roster, ExportFormat.XLSX)
        self.assertTrue(export.is_draft)
        ws = load_workbook(services.export_path(export))[layout.SHEET]
        first = layout.FIRST_DATA_ROW
        self.assertEqual(ws[f'A{first + 7}'].value, '8')
        self.assertEqual(ws[f'I{first}'].data_type, 's')
        self.assertTrue(ws[f'I{first}'].value.startswith('=HYPERLINK'))
        self.assertEqual(ws[f'T{first}'].value, '+1 formula-like remark')
        formulas = [c.coordinate for row in ws.iter_rows() for c in row if c.data_type == 'f']
        self.assertEqual(formulas, [])
        self.assertNotEqual(ws['T29'].value, '=38+16')
        names = [ws[f'A{r}'].value for r in range(first + 8, ws.max_row + 1)]
        self.assertIn('SAMPLE PREPARER', names)
        sig_row = next(r for r in range(first, ws.max_row + 1)
                       if ws[f'A{r}'].value == 'Prepared by:')
        self.assertGreater(sig_row, first + 7)
        self.assertEqual(ws.print_title_rows, '$8:$10')
        self.assertEqual(ws.page_setup.orientation, 'landscape')
        self.assertIn('DRAFT', ws['A7'].value)
        self.assertEqual(ws[f'A{layout.DEPARTMENT_ROW}'].value, 'COLLEGE OF EDUCATION')

        pdf_export = services.create_export(self.hr, roster, ExportFormat.PDF)
        content = services.export_path(pdf_export).read_bytes()
        self.assertTrue(content.startswith(b'%PDF'))

    def test_long_roster_pdf_spans_pages_and_xlsx_keeps_all_rows(self):
        from . import exporters
        path = services.template_path(self.template)
        header = layout.read_header(load_workbook(path))
        header.update(department='COLLEGE OF EDUCATION', cutoff_display='Nov 30, 2026')
        long_text = 'Teacher I, Department of Education (Jun 2015 - Present)\n' * 4
        rows = [{**{c: f'value {c}' for c in layout.COLUMNS}, 'A': str(i), 'R': long_text}
                for i in range(1, 41)]
        data = {'header': header, 'signatories': SIGNATORIES, 'rows': rows, 'draft': False,
                'version': 1, 'reference': 'VAC-TEST', 'cutoff': 'Nov 30, 2026'}
        content = exporters.build_pdf(path, data)
        pages = content.count(b'/Type /Page') - content.count(b'/Type /Pages')
        self.assertGreaterEqual(pages, 3)
        xlsx, info = exporters.build_xlsx(path, data)
        self.assertEqual(info['last_entry_row'], layout.FIRST_DATA_ROW + 39)
        self.assertGreater(info['signatory_row'], info['last_entry_row'])

    def test_export_failure_leaves_no_file_or_record(self):
        self.submit_as(self.applicant_with('Abad'))
        roster = self.generate()
        with mock.patch('matrix.exporters.build_xlsx', side_effect=RuntimeError('disk full')):
            with self.assertRaises(services.ExportFailed) as ctx:
                services.create_export(self.hr, roster, ExportFormat.XLSX)
        self.assertIn('Nothing was saved', str(ctx.exception))
        self.assertFalse(RosterExport.objects.exists())
        self.assertEqual(self.export_files(), [])
        self.assertTrue(AuditLog.objects.filter(action='roster.export_failed').exists())

    def test_files_are_never_overwritten(self):
        self.submit_as(self.applicant_with('Abad'))
        roster = self.generate()
        first = services.create_export(self.hr, roster, ExportFormat.PDF)
        second = services.create_export(self.hr, roster, ExportFormat.PDF)
        self.assertNotEqual(first.storage_key, second.storage_key)
        self.assertEqual(len(self.export_files()), 2)


class TemplateTests(RosterTestCase):
    def test_part_time_form_restricted_to_its_recruitment_type(self):
        other = RecruitmentType.objects.create(code='NT', name='Non-Teaching')
        source = layout.build_sample_template(Path(self.private) / 'other.xlsx')
        with self.assertRaises(ValidationError):
            services.register_template(source, other, name='Wrong form')

    def test_wrong_layout_rejected(self):
        from openpyxl import Workbook
        path = Path(self.private) / 'bad.xlsx'
        Workbook().save(path)
        with self.assertRaises(ValidationError):
            services.register_template(path, self.pti, name='Bad')

    def test_no_approved_template_blocks_generation(self):
        self.template.status = TemplateStatus.RETIRED
        self.template.save()
        with self.assertRaises(ValidationError) as ctx:
            self.generate()
        self.assertIn('No approved roster template', ' '.join(ctx.exception.messages))

    def test_changed_template_file_is_refused(self):
        path = services.template_root() / self.template.storage_key
        path.write_bytes(path.read_bytes() + b'tampered')
        with self.assertRaises(ValidationError):
            self.generate()

    def test_new_template_version_retires_old(self):
        source = layout.build_sample_template(Path(self.private) / 'v2.xlsx')
        v2 = services.register_template(source, self.pti, name='PART-TIME test')
        self.assertEqual(v2.version, 2)
        with self.assertRaises(PermissionDenied):
            services.approve_template(self.hr, v2)
        services.approve_template(self.approver, v2)
        self.template.refresh_from_db()
        self.assertEqual(self.template.status, TemplateStatus.RETIRED)


class AccessTests(RosterTestCase):
    def setUp(self):
        super().setUp()
        self.submit_as(self.applicant_with('Abad'))
        self.roster = self.generate()
        self.export = services.create_export(self.hr, self.roster, ExportFormat.PDF)

    def test_unauthorized_users_cannot_open_preview_or_download(self):
        detail = reverse('matrix:roster_detail', args=[self.roster.pk])
        download = reverse('matrix:download', args=[self.export.pk])
        for user, expected in ((self.hr2, 404), (self.applicant, 403), (self.dept_head, 403)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(detail).status_code, expected, user)
            self.assertEqual(self.client.get(download).status_code, expected, user)
        with self.assertRaises(PermissionDenied):
            services.generate(self.hr2, self.vacancy)

    def test_assigned_hr_and_approver_can_view_and_download_is_audited(self):
        for user in (self.hr, self.approver):
            self.client.force_login(user)
            self.assertEqual(self.client.get(
                reverse('matrix:roster_detail', args=[self.roster.pk])).status_code, 200)
            response = self.client.get(reverse('matrix:download', args=[self.export.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            b''.join(response.streaming_content)
        self.assertEqual(AuditLog.objects.filter(action='roster.export_downloaded').count(), 2)

    def test_pages_render(self):
        self.client.force_login(self.hr)
        entry = self.roster.entries.get()
        for name, args in (('matrix:vacancy_rosters', [self.vacancy.pk]),
                           ('matrix:entry_edit', [self.roster.pk, entry.pk]),
                           ('matrix:signatories', [self.roster.pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        self.client.force_login(self.approver)
        for name in ('matrix:pending', 'matrix:templates'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_generate_and_approve_through_screens(self):
        self.client.force_login(self.hr)
        entry = self.roster.entries.get()
        self.client.post(reverse('matrix:entry_edit', args=[self.roster.pk, entry.pk]),
                         {'remarks': 'Checked', 'acknowledge': 'on'})
        self.client.post(reverse('matrix:submit_for_approval', args=[self.roster.pk]))
        self.client.force_login(self.approver)
        self.client.post(reverse('matrix:approve', args=[self.roster.pk]), {'comments': ''})
        self.client.post(reverse('matrix:release', args=[self.roster.pk]))
        self.roster.refresh_from_db()
        self.assertEqual(self.roster.status, RosterStatus.APPROVED)
        self.assertIsNotNone(self.roster.released_at)
        self.assertEqual(self.roster.exports.filter(is_draft=False).count(), 2)
