from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts import roles
from accounts.access import role_required

# What each workspace will hold, and the stage that delivers it. Shown as
# placeholders so the layout is testable before the features exist.
WORKSPACE_PLAN = {
    # (label, stage, URL name or None while planned)
    'applicant': ('Applicant workspace', roles.APPLICANT, [
        ('My profile and qualifications', 'Stage 4', 'applicants:profile'),
        ('Open vacancies', 'Stage 4', 'recruitment:public_list'),
        ('My applications and documents', 'Stage 5', 'recruitment:my_applications'),
        ('Released updates and decisions', 'Stage 7', 'recruitment:my_applications'),
    ]),
    'hr': ('HR workspace', roles.HR, [
        ('Vacancies and requirements', 'Stage 4', 'recruitment:vacancy_list'),
        ('Document verification queue', 'Stage 5', 'recruitment:verification_queue'),
        ('Applications: fact review and corrections', 'Stage 5', 'recruitment:vacancy_list'),
        ('Official roster preparation (open a vacancy, then Official roster)', 'Stage 6',
         'recruitment:vacancy_list'),
        ('Committees, interviews, and decisions (open a vacancy, then Committees; '
         'an application, then Selection)', 'Stage 7', 'recruitment:vacancy_list'),
        ('Shortlisting decisions awaiting release', 'Stage 7', 'recruitment:decisions_pending'),
        ('Search applications across your vacancies', 'Stage 8', 'recruitment:application_search'),
        ('Recruitment analytics and reports', 'Stage 8', 'analytics:dashboard'),
    ]),
    'approver': ('Approver workspace', roles.APPROVER, [
        ('All vacancies (oversight)', 'Stage 4', 'recruitment:vacancy_list'),
        ('Rosters awaiting approval', 'Stage 6', 'matrix:pending'),
        ('Official roster templates and signatories', 'Stage 6', 'matrix:templates'),
        ('Decisions awaiting release', 'Stage 7', 'recruitment:decisions_pending'),
        ('Recruitment analytics and reports', 'Stage 8', 'analytics:dashboard'),
        ('Search applications', 'Stage 8', 'recruitment:application_search'),
        ('Audit trail search and export', 'Stage 9', 'audit:list'),
    ]),
    'department': ('Department Head workspace', roles.DEPARTMENT_HEAD, [
        ('Assigned vacancies', 'Stage 4', 'recruitment:vacancy_list'),
        ('Committee candidates and assessments', 'Stage 7', 'committee:my_candidates'),
        ('Recruitment analytics for assigned vacancies', 'Stage 8', 'analytics:dashboard'),
    ]),
    'psb': ('PSB workspace', roles.PSB_MEMBER, [
        ('Assigned candidates', 'Stage 7', 'committee:my_candidates'),
        ('Interviews, conflicts, and evaluations (open a candidate)', 'Stage 7',
         'committee:my_candidates'),
    ]),
}


def home(request):
    if request.user.is_authenticated:
        return redirect('core:dashboard')
    return render(request, 'core/home.html')


@login_required
def dashboard(request):
    names = roles.role_names(request.user)
    available = [(url, label) for role, url, label in roles.WORKSPACES if role in names]
    if len(available) == 1:
        return redirect(available[0][0])
    return render(request, 'core/dashboard.html', {'available': available})


def _workspace_view(key):
    title, role, items = WORKSPACE_PLAN[key]

    @role_required(role)
    def view(request):
        return render(request, 'workspaces/workspace.html',
                      {'title': title, 'role': role, 'items': items,
                       'description': roles.ROLE_DESCRIPTIONS[role]})

    view.__name__ = f'workspace_{key}'
    return view


workspace_applicant = _workspace_view('applicant')
workspace_hr = _workspace_view('hr')
workspace_approver = _workspace_view('approver')
workspace_department = _workspace_view('department')
workspace_psb = _workspace_view('psb')
