"""System Administrators deactivate, reactivate, and delete catalog positions
from the Django admin. Deleting needs a reason and works only for positions
no vacancy was created from; a used position can only be deactivated."""

from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts import roles
from accounts.test_stage3 import make_user
from audit.models import AuditLog

from . import position_services as catalog
from .models import CriterionType, Position, PositionQualificationStandard
from .test_positions import CatalogTestCase

CHANGELIST = 'admin:recruitment_position_changelist'


class PositionAdminTests(CatalogTestCase):
    def setUp(self):
        super().setUp()
        self.sysadmin = make_user('sysadmin', roles.SYSTEM_ADMIN, is_staff=True)
        self.unused = catalog.create_position(self.hr, title='Part-Time Instructor - Music',
                                              description='Teach music.')
        catalog.add_standard_default(self.hr, self.unused,
                                     criterion_type=CriterionType.EDUCATION,
                                     degree_level='bachelor', description="Bachelor's degree")
        self.from_catalog()  # self.math is now used by a vacancy
        self.client.force_login(self.sysadmin)

    def act(self, action, *positions, **extra):
        return self.client.post(reverse(CHANGELIST), {
            'action': action, helpers.ACTION_CHECKBOX_NAME: [p.pk for p in positions],
            **extra}, follow=True)

    def test_system_admin_sees_positions_with_usage(self):
        page = self.client.get(reverse(CHANGELIST))
        self.assertContains(page, 'Part-Time Instructor - Music')
        self.assertContains(page, 'Vacancies created from it')
        self.assertContains(page, 'Delete selected positions (only never-used ones)')

    def test_deactivate_and_reactivate(self):
        self.act('deactivate_positions', self.math, self.unused)
        self.math.refresh_from_db()
        self.unused.refresh_from_db()
        self.assertFalse(self.math.is_active or self.unused.is_active)
        log = AuditLog.objects.filter(action='position.deactivated').latest('created_at')
        self.assertEqual(log.actor, self.sysadmin)
        self.act('reactivate_positions', self.math)
        self.math.refresh_from_db()
        self.assertTrue(self.math.is_active)

    def test_delete_asks_for_a_reason_first(self):
        page = self.act('delete_positions', self.unused)
        self.assertContains(page, 'Reason for deleting')
        page = self.act('delete_positions', self.unused, confirm='yes', reason='  ')
        self.assertContains(page, 'Give a reason for deleting.')
        self.assertTrue(Position.objects.filter(pk=self.unused.pk).exists())

    def test_delete_removes_an_unused_position_and_keeps_a_copy_in_the_audit(self):
        pk = self.unused.pk
        page = self.act('delete_positions', self.unused, confirm='yes',
                        reason='Created by mistake')
        self.assertContains(page, '1 position(s) deleted: Part-Time Instructor - Music.')
        self.assertFalse(Position.objects.filter(pk=pk).exists())
        self.assertFalse(PositionQualificationStandard.objects.filter(position_id=pk).exists())
        log = AuditLog.objects.get(action='position.deleted')
        self.assertEqual((log.reason, log.actor), ('Created by mistake', self.sysadmin))
        deleted = log.changes['deleted']
        self.assertEqual(deleted['title'], 'Part-Time Instructor - Music')
        self.assertEqual(deleted['standards'][0]['description'], "Bachelor's degree")

    def test_used_positions_are_kept(self):
        page = self.act('delete_positions', self.math)
        self.assertContains(page, 'None of the selected positions can be deleted.')
        self.assertNotContains(page, 'Yes, delete')
        page = self.act('delete_positions', self.math, self.unused, confirm='yes',
                        reason='Clean up')
        self.assertContains(page, 'Not deleted because vacancies were created from them')
        self.assertTrue(Position.objects.filter(pk=self.math.pk).exists())
        self.assertFalse(Position.objects.filter(pk=self.unused.pk).exists())

    def test_service_rules(self):
        with self.assertRaises(PermissionDenied):  # HR keeps deactivate, not delete
            catalog.delete_position(self.hr, self.unused, reason='No')
        with self.assertRaises(ValidationError):
            catalog.delete_position(self.sysadmin, self.math, reason='Used')
        with self.assertRaises(ValidationError):
            catalog.delete_position(self.sysadmin, self.unused, reason='')
        with self.assertRaises(PermissionDenied):
            catalog.set_position_active(self.approver, self.unused, False)
        catalog.set_position_active(self.hr, self.unused, False)  # HR catalog pages unchanged

    def test_other_staff_cannot_use_the_admin_actions(self):
        self.client.force_login(make_user('hrstaff', roles.HR, is_staff=True))
        self.act('delete_positions', self.unused, confirm='yes', reason='Try')
        self.assertTrue(Position.objects.filter(pk=self.unused.pk).exists())
