"""Stage 7 tests: committees and conflicts (FR-010), interviews (FR-011),
evaluations (FR-010), and human decisions with separate release (FR-019,
FR-013), including the guide's Stage 7 gate:

- conflicted members cannot assess affected applicants;
- unrelated committee members cannot see records;
- rescheduling preserves the old schedule;
- evaluation amendments retain history;
- an appointment is distinguished from a favorable recommendation.
"""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.test_stage3 import make_user
from audit.models import AuditLog
from documents.models import DocumentVersion
from recruitment import application_services as applications
from recruitment import decision_services as decisions
from recruitment.models import (ApplicationStatus, DecisionOutcome, DecisionType,
                                RecruitmentDecision, Vacancy, VacancyStatus, WorkflowLog,
                                WorkflowVisibility)
from recruitment.test_applications import ApplicationTestCase

from . import selectors, services
from .models import (AttendanceStatus, ConflictStatus, EvaluationStatus, Interview,
                     InterviewEvaluation, InterviewEventType, InterviewMode, InterviewStatus,
                     MemberRole)

S = ApplicationStatus
T = DecisionType
O = DecisionOutcome
PASSWORD = 'Str0ng-Passw0rd!'


class CommitteeTestCase(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.psb1 = make_user('psb1', roles.PSB_MEMBER)
        self.psb2 = make_user('psb2', roles.PSB_MEMBER)
        self.committee = services.create_committee(self.hr, self.vacancy, name='PSB - COED')
        self.chair = services.add_member(self.hr, self.committee, self.psb1,
                                         member_role=MemberRole.CHAIR)
        self.member2 = services.add_member(self.hr, self.committee, self.psb2)
        self.application = self.submitted()
        applications.start_review(self.hr, self.application)

    def decide(self, decision_type, outcome, actor=None, **kwargs):
        actor = actor or (self.approver if decision_type in (T.FINAL_OUTCOME, T.APPOINTMENT)
                          else self.hr)
        decision = decisions.record_decision(actor, self.application,
                                             decision_type=decision_type, outcome=outcome,
                                             reason=kwargs.pop('reason', 'Recorded in minutes'),
                                             **kwargs)
        self.application.refresh_from_db()
        return decision

    def shortlist(self, release=True):
        decision = self.decide(T.SHORTLISTING, O.SHORTLISTED)
        if release:
            decisions.release_decision(self.hr, decision, note='You are shortlisted.')
        return decision

    def schedule(self, panel=None, **overrides):
        start = timezone.now() + timedelta(days=2)
        data = dict(starts_at=start, ends_at=start + timedelta(hours=1),
                    mode=InterviewMode.IN_PERSON, location='HR Office, EVSU-Ormoc')
        data.update(overrides)
        interview = services.schedule_interview(self.hr, self.application,
                                                panel=[self.chair, self.member2] if panel is None
                                                else panel, **data)
        self.application.refresh_from_db()
        return interview

    def attend(self, interview, attendance=AttendanceStatus.ATTENDED):
        past = timezone.now() - timedelta(hours=2)
        Interview.objects.filter(pk=interview.pk).update(starts_at=past,
                                                         ends_at=past + timedelta(hours=1))
        return services.record_attendance(self.hr, interview, attendance=attendance)


# ==========================================================================
# Committees, access, and conflicts
# ==========================================================================

class CommitteeAccessTests(CommitteeTestCase):
    def test_only_assigned_hr_manages_and_members_need_a_committee_role(self):
        with self.assertRaises(PermissionDenied):
            services.create_committee(self.hr2, self.vacancy, name='Other')
        with self.assertRaises(ValidationError):
            services.add_member(self.hr, self.committee, self.other_applicant)
        with self.assertRaises(ValidationError):  # one chair
            services.add_member(self.hr, self.committee, self.dept_head,
                                member_role=MemberRole.CHAIR)
        with self.assertRaises(ValidationError):  # already a member
            services.add_member(self.hr, self.committee, self.psb2)
        services.end_member(self.hr, self.member2, reason='Reassigned')
        again = services.add_member(self.hr, self.committee, self.psb2)
        self.assertEqual(again.pk, self.member2.pk)  # reactivated, not duplicated
        self.assertTrue(AuditLog.objects.filter(action='committee.member_reactivated').exists())

    def test_members_see_only_shortlisted_candidates_of_their_vacancy(self):
        self.assertFalse(selectors.can_view_candidate(self.psb1, self.application))
        self.shortlist(release=False)
        self.assertTrue(selectors.can_view_candidate(self.psb1, self.application))
        self.assertTrue(applications.access.can_view(self.psb1, self.application))

    def test_unrelated_committee_member_cannot_see_records(self):
        """Gate: unrelated committee members cannot see records."""
        self.shortlist()
        other_vacancy = self.published()
        outsider = make_user('outsider', roles.PSB_MEMBER)
        other = services.create_committee(self.hr, other_vacancy, name='PSB - other')
        services.add_member(self.hr, other, outsider)
        self.assertFalse(selectors.can_view_candidate(outsider, self.application))
        self.assertFalse(applications.access.can_view(outsider, self.application))
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(
            reverse('committee:candidate', args=[self.application.pk])).status_code, 404)
        version = DocumentVersion.objects.filter(document__application=self.application).first()
        self.assertEqual(self.client.get(
            reverse('documents:download', args=[version.pk])).status_code, 404)
        self.assertEqual(self.client.get(
            reverse('recruitment:application_selection', args=[self.application.pk])
        ).status_code, 403)
        # The member of the right committee can open the candidate and the file.
        self.client.force_login(self.psb1)
        self.assertEqual(self.client.get(
            reverse('committee:candidate', args=[self.application.pk])).status_code, 200)
        self.assertEqual(self.client.get(
            reverse('documents:download', args=[version.pk])).status_code, 200)

    def test_ended_membership_or_revoked_role_ends_access(self):
        self.shortlist()
        services.end_member(self.hr, self.member2, reason='Left the board')
        self.assertFalse(selectors.can_view_candidate(self.psb2, self.application))
        self.psb1.groups.clear()
        self.psb1.__dict__.pop('_role_names_cache', None)
        self.assertFalse(selectors.can_view_candidate(self.psb1, self.application))

    def test_conflicted_member_cannot_assess(self):
        """Gate: conflicted members cannot assess affected applicants."""
        self.shortlist()
        interview = self.schedule(panel=[self.chair])
        conflict = services.declare_conflict(self.psb2, self.member2, self.application,
                                             declaration='Applicant is my cousin.')
        self.assertFalse(selectors.can_view_candidate(self.psb2, self.application))
        with self.assertRaises(PermissionDenied):
            services.start_evaluation(self.psb2, self.application, findings='Strong.')
        with self.assertRaises(ValidationError):
            services.add_panel_member(self.hr, interview, self.member2)
        self.assertNotIn(self.member2, services.panel_candidates(self.application))
        self.client.force_login(self.psb2)
        self.assertEqual(self.client.get(
            reverse('committee:candidate', args=[self.application.pk])).status_code, 404)
        with self.assertRaises(ValidationError):  # declared once
            services.declare_conflict(self.psb2, self.member2, self.application,
                                      declaration='Again')

        # Only an authorized resolver, with a reason; recused stays blocked.
        with self.assertRaises(PermissionDenied):
            services.resolve_conflict(self.hr, conflict, status=ConflictStatus.CLEARED,
                                      resolution='Distant relative')
        with self.assertRaises(ValidationError):
            services.resolve_conflict(self.approver, conflict, status=ConflictStatus.CLEARED,
                                      resolution='')
        services.resolve_conflict(self.approver, conflict, status=ConflictStatus.CLEARED,
                                  resolution='Not a close relative under the policy')
        self.assertTrue(selectors.can_view_candidate(self.psb2, self.application))
        services.start_evaluation(self.psb2, self.application, findings='Clear lesson plans.')

    def test_panel_member_with_new_conflict_is_flagged(self):
        self.shortlist()
        interview = self.schedule()
        services.declare_conflict(self.psb2, self.member2, self.application,
                                  declaration='Former business partner')
        flagged = services.panel_conflicts(interview)
        self.assertEqual([s.committee_member_id for s in flagged], [self.member2.pk])
        services.remove_panel_member(self.hr, interview, self.member2, reason='Conflict')
        self.assertEqual(services.panel_conflicts(interview), [])
        self.assertTrue(interview.events.filter(
            event_type=InterviewEventType.PANEL_REMOVED).exists())


# ==========================================================================
# Interviews
# ==========================================================================

class InterviewTests(CommitteeTestCase):
    def test_scheduling_requires_released_shortlisting(self):
        with self.assertRaises(ValidationError):
            self.schedule()
        decision = self.shortlist(release=False)
        with self.assertRaises(ValidationError):
            self.schedule()
        decisions.release_decision(self.hr, decision)
        interview = self.schedule()
        self.assertEqual(self.application.status, S.FOR_INTERVIEW)
        log = self.application.workflow_logs.order_by('-occurred_at').first()
        self.assertEqual(log.visibility, WorkflowVisibility.APPLICANT)
        self.assertEqual(interview.events.get().event_type, InterviewEventType.SCHEDULED)
        with self.assertRaises(ValidationError):  # one scheduled interview at a time
            self.schedule()

    def test_schedule_validation(self):
        self.shortlist()
        with self.assertRaises(ValidationError):
            self.schedule(mode=InterviewMode.ONLINE, meeting_link='javascript:alert(1)')
        with self.assertRaises(ValidationError):
            self.schedule(location='')
        with self.assertRaises(ValidationError):
            self.schedule(panel=[])
        start = timezone.now() - timedelta(hours=1)
        with self.assertRaises(ValidationError):
            self.schedule(starts_at=start, ends_at=start + timedelta(hours=1))
        with self.assertRaises(PermissionDenied):
            services.schedule_interview(self.hr2, self.application, panel=[self.chair],
                                        starts_at=timezone.now() + timedelta(days=1),
                                        ends_at=timezone.now() + timedelta(days=1, hours=1),
                                        mode=InterviewMode.IN_PERSON, location='Room 1')

    def test_rescheduling_preserves_old_schedule(self):
        """Gate: rescheduling preserves the old schedule."""
        self.shortlist()
        interview = self.schedule()
        original = interview.schedule()
        new_start = interview.starts_at + timedelta(days=1)
        with self.assertRaises(ValidationError):  # reason required
            services.reschedule_interview(self.hr, interview, reason='', starts_at=new_start,
                                          ends_at=new_start + timedelta(hours=1),
                                          mode=InterviewMode.ONLINE,
                                          meeting_link='https://meet.example.com/x')
        services.reschedule_interview(self.hr, interview, reason='Panel chair unavailable',
                                      starts_at=new_start, ends_at=new_start + timedelta(hours=1),
                                      mode=InterviewMode.ONLINE,
                                      meeting_link='https://meet.example.com/x')
        interview.refresh_from_db()
        self.assertEqual(interview.starts_at, new_start)
        event = interview.events.get(event_type=InterviewEventType.RESCHEDULED)
        self.assertEqual(event.old_schedule, original)
        self.assertEqual(event.new_schedule['mode'], InterviewMode.ONLINE)
        self.assertEqual(event.reason, 'Panel chair unavailable')
        with self.assertRaises(PermissionError):
            event.delete()

        services.cancel_interview(self.hr, interview, reason='Applicant asked to reschedule')
        interview.refresh_from_db()
        self.assertEqual(interview.status, InterviewStatus.CANCELED)
        self.assertEqual(interview.attendance_status, AttendanceStatus.NOT_APPLICABLE)
        self.assertEqual(interview.events.count(), 3)

    def test_attendance_after_start_completes_interview(self):
        self.shortlist()
        interview = self.schedule()
        with self.assertRaises(ValidationError):  # not started yet
            services.record_attendance(self.hr, interview, attendance=AttendanceStatus.ATTENDED)
        self.attend(interview, AttendanceStatus.NO_SHOW)
        interview.refresh_from_db()
        self.assertEqual((interview.status, interview.attendance_status),
                         (InterviewStatus.COMPLETED, AttendanceStatus.NO_SHOW))
        with self.assertRaises(ValidationError):  # recorded once
            services.record_attendance(self.hr, interview, attendance=AttendanceStatus.ATTENDED)

    def test_applicant_sees_own_invitation(self):
        self.shortlist()
        self.schedule()
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('recruitment:my_application',
                                           args=[self.application.pk]))
        self.assertContains(response, 'Interview invitations')
        self.assertContains(response, 'HR Office, EVSU-Ormoc')
        self.assertEqual(response.context['public_status'].status, S.FOR_INTERVIEW)


# ==========================================================================
# Evaluations
# ==========================================================================

class EvaluationTests(CommitteeTestCase):
    def setUp(self):
        super().setUp()
        self.shortlist()
        self.interview = self.schedule()

    def test_interview_findings_need_panel_seat_and_attendance(self):
        with self.assertRaises(ValidationError):  # not completed yet
            services.start_evaluation(self.psb1, self.application, interview=self.interview,
                                      findings='Good')
        self.attend(self.interview)
        outsider_member = services.add_member(self.hr, self.committee, self.dept_head)
        with self.assertRaises(PermissionDenied):  # not on the panel
            services.start_evaluation(self.dept_head, self.application,
                                      interview=self.interview, findings='Good')
        evaluation = services.start_evaluation(self.psb1, self.application,
                                               interview=self.interview, findings='Good')
        self.assertEqual(evaluation.version, 1)
        with self.assertRaises(ValidationError):  # one chain per member and interview
            services.start_evaluation(self.psb1, self.application, interview=self.interview,
                                      findings='Again')
        # Document-only assessment is a separate chain.
        services.start_evaluation(outsider_member.user, self.application, findings='Docs fine')

    def test_drafts_are_private_until_submitted(self):
        evaluation = services.start_evaluation(self.psb1, self.application, findings='Draft')
        self.assertNotIn(evaluation, selectors.visible_evaluations(self.hr, self.application))
        self.assertNotIn(evaluation, selectors.visible_evaluations(self.psb2, self.application))
        with self.assertRaises(PermissionDenied):
            services.submit_evaluation(self.psb2, evaluation)
        services.submit_evaluation(self.psb1, evaluation)
        self.assertIn(evaluation, selectors.visible_evaluations(self.hr, self.application))
        self.assertIn(evaluation, selectors.visible_evaluations(self.approver, self.application))
        self.assertNotIn(evaluation, selectors.visible_evaluations(self.psb2, self.application))

    def test_amendments_retain_history(self):
        """Gate: evaluation amendments retain history."""
        v1 = services.start_evaluation(self.psb1, self.application, findings='Original',
                                       recommendation='Recommend')
        services.submit_evaluation(self.psb1, v1)
        v1.refresh_from_db()
        v1.findings = 'Edited in place'
        with self.assertRaises(PermissionError):
            v1.save()
        with self.assertRaises(PermissionError):
            v1.delete()
        with self.assertRaises(ValidationError):
            services.update_draft(self.psb1, v1, findings='Edited')
        with self.assertRaises(ValidationError):  # reason required
            services.amend_evaluation(self.psb1, v1, findings='Revised', reason='')
        v2 = services.amend_evaluation(self.psb1, v1, findings='Revised',
                                       reason='Misread the teaching demo notes')
        self.assertEqual((v2.version, v2.supersedes_id, v2.status), (2, v1.pk,
                                                                    EvaluationStatus.DRAFT))
        v1.refresh_from_db()
        self.assertTrue(v1.is_current)  # until the amendment is submitted
        services.submit_evaluation(self.psb1, v2)
        v1 = InterviewEvaluation.objects.get(pk=v1.pk)
        self.assertEqual(v1.findings, 'Original')
        self.assertFalse(v1.is_current)
        self.assertEqual(services.current_submitted(self.application), [v2])
        with self.assertRaises(ValidationError):  # v1 already amended
            services.amend_evaluation(self.psb1, v1, findings='x', reason='y')
        self.assertTrue(AuditLog.objects.filter(action='evaluation.submitted',
                                                entity_id=str(v2.pk)).exists())

    def test_member_screens_render(self):
        evaluation = services.start_evaluation(self.psb1, self.application, findings='Draft')
        self.client.force_login(self.psb1)
        for url in (reverse('committee:my_candidates'),
                    reverse('committee:candidate', args=[self.application.pk]),
                    reverse('committee:evaluation_edit', args=[evaluation.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        response = self.client.post(reverse('committee:evaluation_edit', args=[evaluation.pk]),
                                    {'findings': 'Final findings', 'submit': '1'})
        self.assertEqual(response.status_code, 302)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, EvaluationStatus.SUBMITTED)
        self.client.force_login(self.psb2)  # another member cannot open it
        self.assertEqual(self.client.get(
            reverse('committee:evaluation_edit', args=[evaluation.pk])).status_code, 404)


# ==========================================================================
# Decisions and release
# ==========================================================================

class DecisionTests(CommitteeTestCase):
    def test_recording_is_internal_until_release(self):
        decision = self.shortlist(release=False)
        self.assertEqual(self.application.status, S.SHORTLISTED)
        log = self.application.workflow_logs.order_by('-occurred_at').first()
        self.assertEqual(log.visibility, WorkflowVisibility.INTERNAL)
        self.assertEqual(decisions.applicant_status(self.application), S.UNDER_REVIEW)
        self.assertEqual(decisions.released_decisions(self.application), [])
        self.assertIn(decision, decisions.pending_release(self.hr))
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('recruitment:my_application',
                                           args=[self.application.pk]))
        self.assertEqual(response.context['public_status'].status, S.UNDER_REVIEW)
        self.assertNotContains(response, 'Shortlisting:')

        decisions.release_decision(self.hr, decision, note='Please wait for the schedule.')
        self.assertEqual(decisions.applicant_status(self.application), S.SHORTLISTED)
        response = self.client.get(reverse('recruitment:my_application',
                                           args=[self.application.pk]))
        self.assertContains(response, 'Please wait for the schedule.')
        with self.assertRaises(ValidationError):
            decisions.release_decision(self.hr, decision)

    def test_authority_follows_configuration(self):
        for actor in (self.approver, self.hr2, self.psb1):
            with self.assertRaises(PermissionDenied):
                self.decide(T.SHORTLISTING, O.SHORTLISTED, actor=actor)
        self.shortlist()
        self.schedule()
        with self.assertRaises(ValidationError):  # recommendation needs the committee
            self.decide(T.COMMITTEE_RECOMMENDATION, O.RECOMMENDED)
        self.decide(T.COMMITTEE_RECOMMENDATION, O.RECOMMENDED, committee=self.committee)
        with self.assertRaises(PermissionDenied):  # HR does not record final outcomes
            self.decide(T.FINAL_OUTCOME, O.SELECTED, actor=self.hr)
        recommendation = decisions.current(self.application, T.COMMITTEE_RECOMMENDATION)
        with self.assertRaises(ValidationError):  # recommendations stay internal
            decisions.release_decision(self.approver, recommendation)

    def test_invalid_stage_is_refused(self):
        with self.assertRaises(ValidationError):
            self.decide(T.COMMITTEE_RECOMMENDATION, O.RECOMMENDED, committee=self.committee)
        with self.assertRaises(ValidationError):
            self.decide(T.APPOINTMENT, O.APPOINTED, appointment_reference='A-1',
                        appointment_at=timezone.now())
        with self.assertRaises(ValidationError):
            self.decide(T.SHORTLISTING, O.SHORTLISTED,
                        decision_at=timezone.now() + timedelta(days=1))

    def test_appointment_is_distinct_from_recommendation(self):
        """Gate: an appointment is distinguished from a favorable recommendation."""
        Vacancy.objects.filter(pk=self.vacancy.pk).update(positions_available=1)
        self.shortlist()
        self.schedule()
        self.decide(T.COMMITTEE_RECOMMENDATION, O.RECOMMENDED, committee=self.committee)
        self.assertEqual(self.application.status, S.RECOMMENDED)
        with self.assertRaises(ValidationError):  # no final outcome yet
            self.decide(T.APPOINTMENT, O.APPOINTED, appointment_reference='APPT-2026-001',
                        appointment_at=timezone.now())
        self.decide(T.FINAL_OUTCOME, O.SELECTED)
        self.assertEqual(self.application.status, S.RECOMMENDED)  # selection is not hiring
        with self.assertRaises(ValidationError):  # reference and date required
            self.decide(T.APPOINTMENT, O.APPOINTED, appointment_reference='')
        appointment = self.decide(T.APPOINTMENT, O.APPOINTED,
                                  appointment_reference='APPT-2026-001',
                                  appointment_at=timezone.now())
        self.assertEqual(self.application.status, S.HIRED)
        self.vacancy.refresh_from_db()
        self.assertEqual(self.vacancy.status, VacancyStatus.FILLED)
        self.assertIsNotNone(self.vacancy.filled_at)
        # Hired is internal until the appointment is released.
        self.assertEqual(decisions.applicant_status(self.application), S.FOR_INTERVIEW)
        decisions.release_decision(self.approver, appointment, note='Welcome to EVSU-Ormoc.')
        self.assertEqual(decisions.applicant_status(self.application), S.HIRED)
        types = list(self.application.decisions.values_list('decision_type', flat=True))
        self.assertEqual(types, [T.SHORTLISTING, T.COMMITTEE_RECOMMENDATION, T.FINAL_OUTCOME,
                                 T.APPOINTMENT])

    def test_corrections_supersede_and_records_are_protected(self):
        decision = self.shortlist(release=False)
        with self.assertRaises(PermissionError):
            decision.save()
        with self.assertRaises(PermissionError):
            decision.delete()
        with self.assertRaises(ValidationError):
            decisions.correct_decision(self.hr, decision, correction_reason='',
                                       reason='Updated')
        corrected = decisions.correct_decision(
            self.hr, decision, correction_reason='Wrong minutes number',
            reason='Meets all standards', references='Minutes 2026-14')
        decision.refresh_from_db()
        self.assertFalse(decision.is_current)
        self.assertEqual(corrected.supersedes_id, decision.pk)
        self.assertEqual(corrected.outcome, O.SHORTLISTED)
        self.assertEqual(corrected.supporting_references['references'], ['Minutes 2026-14'])
        self.assertEqual(decisions.current(self.application, T.SHORTLISTING), corrected)
        with self.assertRaises(ValidationError):  # the old record cannot be released
            decisions.release_decision(self.hr, decision)
        self.assertTrue(AuditLog.objects.filter(action='decision.corrected').exists())

    def test_not_shortlisted_concludes_and_marks_roster_outdated(self):
        revision = self.application.revision
        self.decide(T.SHORTLISTING, O.NOT_SHORTLISTED)
        self.assertEqual(self.application.status, S.NOT_SELECTED)
        self.assertEqual(self.application.revision, revision + 1)
        # Reopening and a new decision supersede the earlier one.
        applications.reopen(self.hr, self.application, reason='Appeal granted')
        self.application.refresh_from_db()
        new = self.decide(T.SHORTLISTING, O.SHORTLISTED)
        self.assertEqual(new.supersedes.outcome, O.NOT_SHORTLISTED)
        self.assertEqual(RecruitmentDecision.objects.filter(
            application=self.application).count(), 2)

    def test_staff_screens_render(self):
        decision = self.shortlist(release=False)
        self.client.force_login(self.hr)
        for url in (reverse('recruitment:application_selection', args=[self.application.pk]),
                    reverse('recruitment:decisions_pending'),
                    reverse('committee:vacancy_committees', args=[self.vacancy.pk]),
                    reverse('recruitment:decision_release',
                            args=[self.application.pk, decision.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        response = self.client.post(reverse('recruitment:decision_release',
                                            args=[self.application.pk, decision.pk]),
                                    {'note': 'Shortlisted'})
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('committee:interview_new', args=[self.application.pk]))
        self.assertEqual(response.status_code, 200)
        start = timezone.localtime() + timedelta(days=3)
        response = self.client.post(
            reverse('committee:interview_new', args=[self.application.pk]),
            {'starts_at': start.strftime('%Y-%m-%dT%H:%M'),
             'ends_at': (start + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
             'mode': 'online', 'meeting_link': 'https://meet.example.com/abc',
             'panel': [str(self.chair.pk)]})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Interview.objects.filter(application=self.application).exists())
        self.client.force_login(self.approver)
        self.assertEqual(self.client.get(reverse('recruitment:decisions_pending')).status_code,
                         200)
        self.assertEqual(self.client.get(
            reverse('recruitment:decision_new', args=[self.application.pk, 'shortlisting'])
        ).status_code, 403)
