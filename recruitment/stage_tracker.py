"""Vertical stage tracker for HR's application review page.

Shows where an application is in the workflow (Submitted -> Hired), built
only from the recorded status and workflow history. Display only: it never
changes a status. Drafts never reach HR, so there is no Draft step.

Step states:
  done      reached and passed (light colour)
  current   where the application is now (highlighted)
  ended     the application stopped at this step (withdrawn, not selected,
            closed without selection)
  skipped   passed over (an interview after a document-only committee review)
  complete  the last step once Hired (highlighted in green)
  upcoming  not reached yet (greyed out)
"""

from . import decision_services as decisions
from .models import ApplicationStatus, DecisionOutcome, DecisionType

S = ApplicationStatus

STEPS = (
    ('submitted', 'Submitted', {S.SUBMITTED}),
    ('under_review', 'Under review', {S.UNDER_REVIEW, S.CORRECTION_REQUESTED}),
    ('shortlisted', 'Shortlisted', {S.SHORTLISTED}),
    ('interview', 'Interview', {S.FOR_INTERVIEW}),
    ('recommended', 'Recommended', {S.RECOMMENDED}),
    ('hired', 'Hired', {S.HIRED}),
)
ENDED = {S.WITHDRAWN, S.NOT_SELECTED, S.CLOSED_WITHOUT_SELECTION}


def _step_of(status):
    for index, (_, _, statuses) in enumerate(STEPS):
        if status in statuses:
            return index
    return None


def _ended_at(application, logs):
    """The step the application was in when it ended (the latest move into
    its present concluded status). Falls back to the first step."""
    for log in reversed(logs):
        if log.to_status == application.status:
            index = _step_of(log.from_status)
            return (index if index is not None else 0), log.from_status
    return 0, ''


def _ended_note(application, from_status):
    if application.status == S.NOT_SELECTED and from_status in (S.UNDER_REVIEW,
                                                                S.CORRECTION_REQUESTED):
        return 'Not shortlisted'
    return S(application.status).label


def build(application):
    """List of step dicts: key, label, state, note."""
    logs = list(application.workflow_logs.order_by('occurred_at', 'created_at'))
    reached = {_step_of(log.to_status) for log in logs} | {0}
    status = application.status
    ended = status in ENDED

    if status == S.HIRED:
        position = len(STEPS) - 1
    elif ended:
        position, from_status = _ended_at(application, logs)
    else:
        position = _step_of(status) or 0

    notes = {}
    if status == S.CORRECTION_REQUESTED:
        notes[1] = 'Correction requested'
    if status == S.RECOMMENDED:
        final = decisions.current(application, DecisionType.FINAL_OUTCOME)
        if final and final.outcome == DecisionOutcome.SELECTED:
            notes[position] = 'Selected; waiting for appointment'
    if status in (S.SHORTLISTED, S.FOR_INTERVIEW):
        rec = decisions.current(application, DecisionType.COMMITTEE_RECOMMENDATION)
        if rec and rec.outcome == DecisionOutcome.NOT_RECOMMENDED:
            notes[position] = 'Committee: not recommended'

    steps = []
    for index, (key, label, _) in enumerate(STEPS):
        note = notes.get(index, '')
        if index < position:
            state = 'done' if index in reached else 'skipped'
            if state == 'skipped':
                note = 'Skipped'
        elif index == position:
            if ended:
                state, note = 'ended', _ended_note(application, from_status)
            elif status == S.HIRED:
                state, note = 'complete', 'Appointment recorded'
            else:
                state = 'current'
        else:
            state = 'upcoming'
            if ended:
                note = 'Not reached'
        steps.append({'key': key, 'label': label, 'state': state, 'note': note,
                      'number': index + 1})
    return steps
