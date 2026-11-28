"""Role definitions. Roles are Django Groups; a user may hold several.

Recruitment authority comes from explicit role membership. `is_superuser` /
system administration never implies a recruitment role (FR-002): use
`has_role()`, not `user.has_perm()`, for recruitment decisions, because
Django grants superusers every permission.
"""

from django.core.exceptions import ValidationError

APPLICANT = 'Applicant'
HR = 'HR Personnel'
APPROVER = 'HR Head / Approver'
DEPARTMENT_HEAD = 'Department Head'
PSB_MEMBER = 'PSB Member'
SYSTEM_ADMIN = 'System Administrator'

ALL_ROLES = (APPLICANT, HR, APPROVER, DEPARTMENT_HEAD, PSB_MEMBER, SYSTEM_ADMIN)
STAFF_ROLES = (HR, APPROVER, DEPARTMENT_HEAD, PSB_MEMBER, SYSTEM_ADMIN)

# Session key: the staff workspace this user opened most recently.
ACTIVE_WORKSPACE_SESSION_KEY = 'active_workspace_role'

ROLE_DESCRIPTIONS = {
    APPLICANT: 'Manage own profile, submit applications and documents, view released updates.',
    HR: 'Manage vacancies, verify documents, record authorized decisions, prepare rosters.',
    APPROVER: 'Review and approve roster release; oversee recruitment.',
    DEPARTMENT_HEAD: 'Review qualifications and give assessments for assigned vacancies.',
    PSB_MEMBER: 'Review assigned candidates, record assessments, join recommendations.',
    SYSTEM_ADMIN: 'Manage accounts, role assignments, departments, and settings. '
                  'Grants no recruitment decision authority.',
}

# Default permissions seeded per role (only for models that exist so far).
# Seeding only adds missing permissions, so administrators can extend groups.
ROLE_PERMISSIONS = {
    APPLICANT: [],
    HR: ['core.view_department', 'core.view_recruitmenttype', 'documents.view_documenttype'],
    APPROVER: ['core.view_department', 'core.view_recruitmenttype', 'audit.view_auditlog'],
    DEPARTMENT_HEAD: ['core.view_department'],
    PSB_MEMBER: [],
    SYSTEM_ADMIN: [
        'accounts.view_user', 'accounts.change_user', 'accounts.assign_roles',
        'core.view_department', 'core.add_department', 'core.change_department',
        'core.view_recruitmenttype', 'core.add_recruitmenttype',
        'core.change_recruitmenttype', 'audit.view_auditlog',
        'documents.view_documenttype', 'documents.add_documenttype',
        'documents.change_documenttype',
        # Position catalog in the Django admin: deactivate, reactivate, and
        # delete never-used positions (recruitment/admin.py).
        'recruitment.view_position',
    ],
}

# Roles whose holders may use the Django admin (for reference data). Kept in
# sync with is_staff whenever roles change.
ADMIN_SITE_ROLES = (SYSTEM_ADMIN,)

# (role, URL name, label) for role workspaces, in navigation order.
WORKSPACES = (
    (APPLICANT, 'core:workspace_applicant', 'Applicant'),
    (HR, 'core:workspace_hr', 'HR'),
    (APPROVER, 'core:workspace_approver', 'Approver'),
    (DEPARTMENT_HEAD, 'core:workspace_department', 'Department Head'),
    (PSB_MEMBER, 'core:workspace_psb', 'PSB'),
    (SYSTEM_ADMIN, 'accounts:user_list', 'System Admin'),
)


def role_names(user):
    """Names of the user's roles, cached on the user object for the request."""
    if not getattr(user, 'is_authenticated', False) or not user.is_active:
        return frozenset()
    cached = getattr(user, '_role_names_cache', None)
    if cached is None:
        cached = frozenset(user.groups.values_list('name', flat=True))
        user._role_names_cache = cached
    return cached


def has_role(user, *roles):
    """True if the user holds any of the given roles."""
    return bool(role_names(user) & set(roles))


def clear_role_cache(user):
    user.__dict__.pop('_role_names_cache', None)


# --------------------------------------------------------------------------
# Applicant and staff accounts are separate
# --------------------------------------------------------------------------

class RoleConflict(ValidationError):
    """A change would mix applicant and staff access on one account."""


def is_staff_account(user):
    return bool(getattr(user, 'is_superuser', False)) or has_role(user, *STAFF_ROLES)


def is_applicant_account(user):
    return has_role(user, APPLICANT) and not is_staff_account(user)


def validate_role_change(current, wanted, *, is_superuser=False):
    """Applicant and staff access never share an account, and an existing
    account is not converted from one kind to the other: a person who is both
    uses one account of each kind."""
    current, wanted = set(current), set(wanted)
    staff = set(STAFF_ROLES)
    if APPLICANT in wanted and (wanted & staff or is_superuser):
        raise RoleConflict('An account cannot be both an applicant and staff. '
                           'Use separate accounts.')
    if (APPLICANT in current and wanted & staff) or (current & staff and APPLICANT in wanted):
        raise RoleConflict('Applicant and staff accounts cannot be converted into each '
                           'other. Create a separate account.')
