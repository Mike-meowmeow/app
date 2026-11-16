"""Committees, conflicts, interviews, and narrative evaluations (Batch E;
FR-010, FR-011). Business rules live in services.py; these models enforce
local invariants and protect submitted history."""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.models import AppendOnlyModel, BaseModel


class Committee(BaseModel):
    """A selection committee (PSB) for one vacancy."""

    vacancy = models.ForeignKey('recruitment.Vacancy', on_delete=models.PROTECT,
                                related_name='committees')
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'committees'
        ordering = ['-is_active', 'created_at']

    def __str__(self):
        return self.name

    def active_members(self):
        return self.members.filter(ended_at__isnull=True).select_related('user')


class MemberRole(models.TextChoices):
    CHAIR = 'chair', 'Chair'
    MEMBER = 'member', 'Member'


class CommitteeMember(BaseModel):
    """Membership of one user. Ended memberships stay as history."""

    committee = models.ForeignKey(Committee, on_delete=models.PROTECT, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name='committee_memberships')
    member_role = models.CharField(max_length=20, choices=MemberRole.choices,
                                   default=MemberRole.MEMBER)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'committee_members'
        ordering = ['member_role', 'created_at']
        constraints = [
            models.UniqueConstraint(fields=['committee', 'user'], name='committee_member_unique'),
        ]

    def __str__(self):
        return f'{self.user.full_name or self.user.username} ({self.get_member_role_display()})'

    @property
    def is_active(self):
        return self.ended_at is None


class ConflictStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    RECUSED = 'recused', 'Resolved - member recused'
    CLEARED = 'cleared', 'Resolved - member may participate'


BLOCKING_CONFLICTS = (ConflictStatus.OPEN, ConflictStatus.RECUSED)


class CommitteeConflict(BaseModel):
    """A declared conflict of interest between a member and one application.
    Open and recused conflicts block that member's participation."""

    committee_member = models.ForeignKey(CommitteeMember, on_delete=models.PROTECT,
                                         related_name='conflicts')
    application = models.ForeignKey('recruitment.Application', on_delete=models.PROTECT,
                                    related_name='committee_conflicts')
    declaration = models.TextField()
    status = models.CharField(max_length=20, choices=ConflictStatus.choices,
                              default=ConflictStatus.OPEN)
    declared_at = models.DateTimeField(default=timezone.now)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)

    class Meta:
        db_table = 'committee_conflicts'
        ordering = ['-declared_at']
        constraints = [
            models.CheckConstraint(
                condition=(Q(status=ConflictStatus.OPEN) & Q(resolved_at__isnull=True))
                | (~Q(status=ConflictStatus.OPEN) & Q(resolved_at__isnull=False)
                   & Q(resolved_by__isnull=False)),
                name='conflict_resolution_complete'),
        ]

    @property
    def is_blocking(self):
        return self.status in BLOCKING_CONFLICTS


class InterviewStatus(models.TextChoices):
    SCHEDULED = 'scheduled', 'Scheduled'
    COMPLETED = 'completed', 'Completed'
    CANCELED = 'canceled', 'Canceled'


class AttendanceStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    ATTENDED = 'attended', 'Attended'
    NO_SHOW = 'no_show', 'No show'
    NOT_APPLICABLE = 'not_applicable', 'Not applicable'


class InterviewMode(models.TextChoices):
    IN_PERSON = 'in_person', 'In person'
    ONLINE = 'online', 'Online'


class Interview(BaseModel):
    """One applicant's interview. starts_at/ends_at hold the current
    appointment; InterviewEvent keeps every earlier schedule."""

    application = models.ForeignKey('recruitment.Application', on_delete=models.PROTECT,
                                    related_name='interviews')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    timezone = models.CharField(max_length=64, default='Asia/Manila')
    mode = models.CharField(max_length=20, choices=InterviewMode.choices)
    location = models.TextField(blank=True)
    meeting_link = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=InterviewStatus.choices,
                              default=InterviewStatus.SCHEDULED)
    attendance_status = models.CharField(max_length=20, choices=AttendanceStatus.choices,
                                         default=AttendanceStatus.PENDING)
    scheduled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     related_name='+')

    class Meta:
        db_table = 'interviews'
        ordering = ['starts_at']
        indexes = [models.Index(fields=['starts_at'], name='interview_starts_idx')]
        constraints = [
            models.CheckConstraint(condition=Q(starts_at__lt=F('ends_at')),
                                   name='interview_starts_before_end'),
        ]

    def __str__(self):
        return f'Interview {self.starts_at:%Y-%m-%d %H:%M} ({self.get_status_display()})'

    def schedule(self):
        """JSON-safe schedule, as stored in InterviewEvent."""
        return {'starts_at': self.starts_at.isoformat(), 'ends_at': self.ends_at.isoformat(),
                'timezone': self.timezone, 'mode': self.mode, 'location': self.location,
                'meeting_link': self.meeting_link}


class InterviewPanelMember(BaseModel):
    interview = models.ForeignKey(Interview, on_delete=models.PROTECT, related_name='panel')
    committee_member = models.ForeignKey(CommitteeMember, on_delete=models.PROTECT,
                                         related_name='panels')

    class Meta:
        db_table = 'interview_panel_members'
        constraints = [
            models.UniqueConstraint(fields=['interview', 'committee_member'],
                                    name='interview_panel_unique'),
        ]


class InterviewEventType(models.TextChoices):
    SCHEDULED = 'scheduled', 'Scheduled'
    RESCHEDULED = 'rescheduled', 'Rescheduled'
    CANCELED = 'canceled', 'Canceled'
    COMPLETED = 'completed', 'Completed'
    PANEL_ADDED = 'panel_added', 'Panel member added'
    PANEL_REMOVED = 'panel_removed', 'Panel member removed'


class InterviewEvent(AppendOnlyModel):
    """Schedule history: each reschedule/cancellation keeps old and new values."""

    interview = models.ForeignKey(Interview, on_delete=models.PROTECT, related_name='events')
    event_type = models.CharField(max_length=20, choices=InterviewEventType.choices)
    old_schedule = models.JSONField(null=True, blank=True)
    new_schedule = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name='+')
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'interview_events'
        ordering = ['occurred_at', 'created_at']


class EvaluationStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    SUBMITTED = 'submitted', 'Submitted'


class InterviewEvaluation(BaseModel):
    """A member's narrative assessment of one application, for an interview
    or (interview empty) for a document-only review. Submitted versions are
    immutable; an amendment is a new version that supersedes the previous one.
    No score is required."""

    application = models.ForeignKey('recruitment.Application', on_delete=models.PROTECT,
                                    related_name='evaluations')
    interview = models.ForeignKey(Interview, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='evaluations')
    committee_member = models.ForeignKey(CommitteeMember, on_delete=models.PROTECT,
                                         related_name='evaluations')
    version = models.PositiveIntegerField(default=1)
    supersedes = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='superseded_by')
    status = models.CharField(max_length=20, choices=EvaluationStatus.choices,
                              default=EvaluationStatus.DRAFT)
    findings = models.TextField()
    recommendation = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    amendment_reason = models.TextField(blank=True)

    class Meta:
        db_table = 'interview_evaluations'
        ordering = ['application', 'committee_member', 'version']
        constraints = [
            # Document-only chains (interview NULL) are kept unique by the
            # service, which numbers versions under the application row lock.
            models.UniqueConstraint(fields=['application', 'committee_member', 'interview',
                                            'version'], name='evaluation_version_unique'),
            models.CheckConstraint(condition=Q(version__gt=0), name='evaluation_version_positive'),
            models.CheckConstraint(
                condition=(Q(status=EvaluationStatus.DRAFT) & Q(submitted_at__isnull=True))
                | (Q(status=EvaluationStatus.SUBMITTED) & Q(submitted_at__isnull=False)),
                name='evaluation_submitted_at_matches_status'),
            models.CheckConstraint(condition=Q(version=1) | ~Q(amendment_reason=''),
                                   name='evaluation_amendment_has_reason'),
        ]

    def _stored_status(self):
        return type(self).objects.filter(pk=self.pk).values_list('status', flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding and self._stored_status() == EvaluationStatus.SUBMITTED:
            raise PermissionError('Submitted evaluations cannot be changed; amend them instead.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._stored_status() == EvaluationStatus.SUBMITTED:
            raise PermissionError('Submitted evaluations cannot be deleted.')
        return super().delete(*args, **kwargs)

    @property
    def is_submitted(self):
        return self.status == EvaluationStatus.SUBMITTED

    @property
    def is_current(self):
        """False once a submitted amendment replaces this version."""
        nxt = getattr(self, 'superseded_by', None)
        return nxt is None or not nxt.is_submitted
