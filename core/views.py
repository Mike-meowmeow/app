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
        ('Released updates and decisions', 'Stage 7', None),
    ]),
    'hr': ('HR workspace', roles.HR, [
        ('Vacancies and requirements', 'Stage 4', 'recruitment:vacancy_list'),
        ('Document verification queue', 'Stage 5', 'recruitment:verification_queue'),
        ('Applications: fact review and corrections', 'Stage 5', 'recruitment:vacancy_list'),
        ('Official roster preparation', 'Stage 6', None),
    ]),
    'approver': ('Approver workspace', roles.APPROVER, [
        ('All vacancies (oversight)', 'Stage 4', 'recruitment:vacancy_list'),
        ('Rosters awaiting approval', 'Stage 6', None),
        ('Decision release oversight', 'Stage 7', None),
        ('Recruitment reports', 'Stage 8', None),
    ]),
    'department': ('Department Head workspace', roles.DEPARTMENT_HEAD, [
        ('Assigned vacancies', 'Stage 4', 'recruitment:vacancy_list'),
        ('Qualification review and assessments', 'Stage 7', None),
    ]),
    'psb': ('PSB workspace', roles.PSB_MEMBER, [
        ('Assigned candidates', 'Stage 7', None),
        ('Interviews and evaluations', 'Stage 7', None),
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
