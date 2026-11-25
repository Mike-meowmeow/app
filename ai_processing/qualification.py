"""Deterministic advisory qualification checks (13.4, FR-009).

Each published qualification standard is compared with the application's
reviewed facts by fixed, written rules. The result is one of the four outcomes
HR already uses, with the facts used and the reasoning. Nothing here is
learned or weighted, and nothing changes an application: HR records the
official outcome separately.

Rules, in short:
- Only facts HR accepted are used. Pending facts are not used, but they turn a
  "Does Not Meet" into "Needs Review" because reviewing them could change it.
  Rejected and superseded facts are never used.
- Experience and training count only when HR marked them relevant; entries not
  yet assessed for relevance are reported and can make the result Needs Review.
- Overlapping work periods are counted once. Ongoing work counts up to the
  submission date, so a re-run gives the same answer.
- Repeated trainings (same title and start date) are counted once; trainings
  without hours are reported, not guessed.
- A degree counts only when completed; an ongoing degree at the required level
  is Needs Review, not Meets.
- Name matching (degree field, eligibility, license) needs every significant
  word of the requirement; a partial match is Needs Review.
- Free-text ("Other") standards are always Needs Review.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from applicants.models import DEGREE_RANK, CompletionStatus, DegreeLevel
from recruitment.models import CriterionType, FactReviewStatus, RelevanceStatus

RULES_VERSION = 'qualification-rules-1'

MEETS = 'meets'
DOES_NOT_MEET = 'does_not_meet'
NEEDS_REVIEW = 'needs_review'
NOT_REQUIRED = 'not_required'

DAYS_PER_YEAR = Decimal('365.25')
# Words that do not identify a field or eligibility by themselves.
STOP_WORDS = {
    'a', 'an', 'and', 'the', 'of', 'in', 'on', 'for', 'with', 'major', 'minor', 'degree',
    'program', 'course', 'graduate', 'bachelor', 'bachelors', 'master', 'masters', 'doctor',
    'doctorate', 'ba', 'bs', 'bsc', 'ab', 'ma', 'ms', 'msc', 'phd', 'edd', 'related', 'field',
    'any',
}
DEGREE_LABELS = dict(DegreeLevel.choices)


class Outcome:
    def __init__(self, result, lines, evidence=()):
        self.result = result
        self.lines = list(lines)
        self.evidence = [str(f.pk) for f in evidence]

    @property
    def explanation(self):
        return '\n'.join(self.lines)


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _decimal(value):
    try:
        return Decimal(str(value)) if value not in (None, '') else None
    except InvalidOperation:
        return None


def words(text):
    # Hyphenated words stay whole: "Sub-Professional" is not "Professional".
    return {w for w in re.findall(r'[a-z0-9]+(?:-[a-z0-9]+)*', (text or '').lower())
            if w not in STOP_WORDS}


def _plural(n, word):
    if n == 1:
        return f'{n} {word}'
    return f'{n} {word[:-1]}ies' if word.endswith('y') else f'{n} {word}s'


def _years(days):
    return (Decimal(days) / DAYS_PER_YEAR).quantize(Decimal('0.1'))


def _split(facts, fact_type):
    same = [f for f in facts if f.fact_type == fact_type]
    return ([f for f in same if f.review_status == FactReviewStatus.ACCEPTED],
            [f for f in same if f.review_status == FactReviewStatus.PENDING],
            [f for f in same if f.review_status == FactReviewStatus.REJECTED])


def describe(fact):
    """Short, name-free description of a fact for explanations."""
    v = fact.value
    if fact.fact_type == 'education':
        status = dict(CompletionStatus.choices).get(v.get('completion_status'), '')
        return (f"{DEGREE_LABELS.get(v.get('degree_level'), 'Degree')}, {v.get('program') or '?'}"
                f" ({status.lower() or 'status not given'})")
    if fact.fact_type == 'experience':
        return (f"{v.get('position') or '?'} at {v.get('organization') or '?'}, "
                f"{v.get('start_date') or '?'} to {v.get('end_date') or 'present'}")
    if fact.fact_type == 'training':
        hours = v.get('hours')
        return f"{v.get('title') or '?'} ({f'{hours} hours' if hours else 'hours not given'})"
    if fact.fact_type == 'eligibility':
        return v.get('eligibility_type') or '?'
    if fact.fact_type == 'license':
        return f"{v.get('profession') or '?'} license {v.get('license_number') or ''}".strip()
    return fact.get_fact_type_display()


def _relevance_split(accepted):
    return ([f for f in accepted if f.relevance_status == RelevanceStatus.RELEVANT],
            [f for f in accepted if f.relevance_status in (RelevanceStatus.NOT_ASSESSED,
                                                            RelevanceStatus.NOT_APPLICABLE)],
            [f for f in accepted if f.relevance_status == RelevanceStatus.NOT_RELEVANT])


def _finish(outcome, pending, rejected, label):
    """Shared notes: rejected facts are ignored, and pending facts make a
    negative result uncertain."""
    if rejected:
        outcome.lines.append(f'{_plural(len(rejected), label)} rejected by HR '
                             f'{"was" if len(rejected) == 1 else "were"} not used.')
    if pending:
        outcome.lines.append(f'{_plural(len(pending), label)} not yet reviewed by HR '
                             f'{"was" if len(pending) == 1 else "were"} not used.')
        if outcome.result == DOES_NOT_MEET:
            outcome.result = NEEDS_REVIEW
            outcome.lines.append('Reviewing the pending entries could change this result.')
    return outcome


# --------------------------------------------------------------------------
# One function per criterion type
# --------------------------------------------------------------------------

def check_education(standard, facts, as_of):
    required = standard.required_value.get('degree_level')
    required_rank = DEGREE_RANK.get(required, 0)
    accepted, pending, rejected = _split(facts, 'education')
    if not required_rank:
        return Outcome(NEEDS_REVIEW, ['The required degree level is not one the check can '
                                      'compare; HR decides.'], accepted)
    label = DEGREE_LABELS.get(required, required)
    field = (standard.required_value.get('field') or '').strip()
    lines = [f'Requires at least a completed {label}' + (f' in {field}.' if field else '.')]
    at_level = [f for f in accepted
                if DEGREE_RANK.get(f.value.get('degree_level'), 0) >= required_rank]
    for f in accepted:
        if f not in at_level:
            lines.append(f'Below the required level: {describe(f)}.')
    completed = [f for f in at_level
                 if f.value.get('completion_status') == CompletionStatus.COMPLETED]
    ongoing = [f for f in at_level if f.value.get('completion_status') == CompletionStatus.ONGOING]
    if completed:
        if not field:
            lines += [f'Meets the level: {describe(f)}.' for f in completed]
            return _finish(Outcome(MEETS, lines, completed), pending, rejected, 'education entry')
        required_words = words(field)
        matching = [f for f in completed if required_words
                    and required_words <= words(f.value.get('program'))]
        if matching:
            lines += [f'Meets the level and the program matches "{field}": {describe(f)}.'
                      for f in matching]
            return _finish(Outcome(MEETS, lines, matching), pending, rejected, 'education entry')
        lines += [f'Meets the level, but the program name does not contain "{field}": '
                  f'{describe(f)}.' for f in completed]
        lines.append('HR decides whether the field is the same or related.')
        return _finish(Outcome(NEEDS_REVIEW, lines, completed), pending, rejected,
                       'education entry')
    if ongoing:
        lines += [f'At the required level but not completed: {describe(f)}.' for f in ongoing]
        lines.append('The standard asks for a completed degree; HR decides whether an ongoing '
                     'degree is acceptable.')
        return _finish(Outcome(NEEDS_REVIEW, lines, ongoing), pending, rejected,
                       'education entry')
    if at_level:
        lines += [f'At the required level but recorded as not completed: {describe(f)}.'
                  for f in at_level]
    elif not accepted:
        lines.append('No accepted education entries.')
    return _finish(Outcome(DOES_NOT_MEET, lines, at_level), pending, rejected, 'education entry')


def check_experience(standard, facts, as_of):
    required = _decimal(standard.required_value.get('amount'))
    if required is None:
        return Outcome(NEEDS_REVIEW, ['The required number of years is missing; HR decides.'])
    if required <= 0:
        return Outcome(NOT_REQUIRED, ['The standard asks for no minimum experience.'])
    accepted, pending, rejected = _split(facts, 'experience')
    relevant, unassessed, not_relevant = _relevance_split(accepted)
    lines = [f'Requires at least {required.normalize()} years of relevant experience, counted '
             f'up to the submission date ({as_of.isoformat()}).']
    periods, used, unknown = [], [], []
    for f in relevant:
        start, end = _date(f.value.get('start_date')), _date(f.value.get('end_date'))
        if start is None:
            unknown.append(f)
            lines.append(f'Not counted, no start date: {describe(f)}.')
            continue
        end = min(end or as_of, as_of)
        if end < start:
            unknown.append(f)
            lines.append(f'Not counted, the end date is before the start date: {describe(f)}.')
            continue
        periods.append((start, end))
        used.append(f)
        lines.append(f'Counted: {describe(f)} ({_years((end - start).days + 1)} years).')
    if not_relevant:
        lines.append(f'{_plural(len(not_relevant), "entry")} marked not relevant '
                     f'{"was" if len(not_relevant) == 1 else "were"} not counted.')
    if unassessed:
        lines.append(f'{_plural(len(unassessed), "entry")} not yet marked relevant or not '
                     f'relevant {"was" if len(unassessed) == 1 else "were"} not counted.')
    # Merge overlapping periods so concurrent jobs are counted once.
    merged = []
    for start, end in sorted(periods):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    union_days = sum((end - start).days + 1 for start, end in merged)
    separate_days = sum((end - start).days + 1 for start, end in periods)
    total = _years(union_days)
    if separate_days > union_days:
        lines.append(f'Overlapping periods ({separate_days - union_days} days) are counted once.')
    lines.append(f'Total relevant experience: {total} years.')
    if total >= required:
        return _finish(Outcome(MEETS, lines, used), pending, rejected, 'experience entry')
    if unassessed or unknown:
        lines.append('Entries not counted above could change this result; HR decides.')
        return _finish(Outcome(NEEDS_REVIEW, lines, used + unassessed + unknown), pending,
                       rejected, 'experience entry')
    return _finish(Outcome(DOES_NOT_MEET, lines, used), pending, rejected, 'experience entry')


def check_training(standard, facts, as_of):
    required = _decimal(standard.required_value.get('amount'))
    if required is None:
        return Outcome(NEEDS_REVIEW, ['The required number of hours is missing; HR decides.'])
    if required <= 0:
        return Outcome(NOT_REQUIRED, ['The standard asks for no minimum training hours.'])
    accepted, pending, rejected = _split(facts, 'training')
    relevant, unassessed, not_relevant = _relevance_split(accepted)
    lines = [f'Requires at least {required.normalize()} hours of relevant training.']
    seen, used, unknown, total = set(), [], [], Decimal(0)
    for f in relevant:
        key = (' '.join(sorted(words(f.value.get('title')))), f.value.get('start_date'))
        if key in seen:
            lines.append(f'Counted once, repeated entry: {describe(f)}.')
            continue
        seen.add(key)
        hours = _decimal(f.value.get('hours'))
        if hours is None or hours <= 0:
            unknown.append(f)
            lines.append(f'Not counted, hours not recorded: {describe(f)}.')
            continue
        total += hours
        used.append(f)
        lines.append(f'Counted: {describe(f)}.')
    if not_relevant:
        lines.append(f'{_plural(len(not_relevant), "training")} marked not relevant '
                     f'{"was" if len(not_relevant) == 1 else "were"} not counted.')
    if unassessed:
        lines.append(f'{_plural(len(unassessed), "training")} not yet marked relevant or not '
                     f'relevant {"was" if len(unassessed) == 1 else "were"} not counted.')
    lines.append(f'Total relevant training: {total.normalize():f} hours.')
    if total >= required:
        return _finish(Outcome(MEETS, lines, used), pending, rejected, 'training')
    if unassessed or unknown:
        lines.append('Trainings not counted above could change this result; HR decides.')
        return _finish(Outcome(NEEDS_REVIEW, lines, used + unassessed + unknown), pending,
                       rejected, 'training')
    return _finish(Outcome(DOES_NOT_MEET, lines, used), pending, rejected, 'training')


def _name_matches(name, facts, field):
    required = words(name)
    full = [f for f in facts if required and required <= words(f.value.get(field))]
    partial = [f for f in facts if f not in full and required & words(f.value.get(field))]
    return full, partial


def check_eligibility(standard, facts, as_of):
    name = standard.required_value.get('name') or ''
    accepted, pending, rejected = _split(facts, 'eligibility')
    lines = [f'Requires the eligibility "{name}".']
    full, partial = _name_matches(name, accepted, 'eligibility_type')
    if full:
        lines += [f'Matches: {describe(f)}.' for f in full]
        return _finish(Outcome(MEETS, lines, full), pending, rejected, 'eligibility')
    if partial:
        lines += [f'Partly matches: {describe(f)}.' for f in partial]
        lines.append('HR decides whether this is the required eligibility.')
        return _finish(Outcome(NEEDS_REVIEW, lines, partial), pending, rejected, 'eligibility')
    lines.append('No accepted eligibility matches.' if accepted
                 else 'No accepted eligibility entries.')
    return _finish(Outcome(DOES_NOT_MEET, lines), pending, rejected, 'eligibility')


def check_license(standard, facts, as_of):
    name = standard.required_value.get('name') or ''
    accepted, pending, rejected = _split(facts, 'license')
    lines = [f'Requires a professional license: "{name}".']
    full, partial = _name_matches(name, accepted, 'profession')
    if full:
        current = []
        for f in full:
            expires = _date(f.value.get('expires_on'))
            if expires and expires < as_of:
                lines.append(f'Matches, but the card shows it expired on {expires.isoformat()}: '
                             f'{describe(f)}.')
            else:
                current.append(f)
                lines.append(f'Matches: {describe(f)}' + ('' if expires else
                                                          ' (no expiry date recorded)') + '.')
        if current:
            return _finish(Outcome(MEETS, lines, current), pending, rejected, 'license')
        lines.append('HR confirms whether the registration is still valid.')
        return _finish(Outcome(NEEDS_REVIEW, lines, full), pending, rejected, 'license')
    if partial:
        lines += [f'Partly matches: {describe(f)}.' for f in partial]
        lines.append('HR decides whether this is the required license.')
        return _finish(Outcome(NEEDS_REVIEW, lines, partial), pending, rejected, 'license')
    lines.append('No accepted license matches.' if accepted else 'No accepted license entries.')
    return _finish(Outcome(DOES_NOT_MEET, lines), pending, rejected, 'license')


def check_other(standard, facts, as_of):
    return Outcome(NEEDS_REVIEW, ['This is a written requirement without a fixed rule; HR '
                                  'judges it against the evidence.'])


CHECKS = {
    CriterionType.EDUCATION: check_education,
    CriterionType.EXPERIENCE: check_experience,
    CriterionType.TRAINING: check_training,
    CriterionType.ELIGIBILITY: check_eligibility,
    CriterionType.LICENSE: check_license,
    CriterionType.OTHER: check_other,
}


def evaluate(standard, facts, as_of):
    check = CHECKS.get(standard.criterion_type, check_other)
    return check(standard, facts, as_of)


def overall(results):
    """Does Not Meet if any standard is not met, else Needs Review if any needs
    review, else Meets."""
    results = set(results)
    if DOES_NOT_MEET in results:
        return DOES_NOT_MEET
    if NEEDS_REVIEW in results:
        return NEEDS_REVIEW
    return MEETS if MEETS in results else NOT_REQUIRED
