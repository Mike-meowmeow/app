from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import AuditLog


class AuditLogTests(TestCase):
    def test_events_are_append_only_and_actor_optional(self):
        system_event = AuditLog.objects.create(action='seed', entity_type='core.Department',
                                               entity_id='COED')
        self.assertIsNone(system_event.actor)
        self.assertEqual(system_event.created_at, system_event.updated_at)

        system_event.reason = 'edited'
        with self.assertRaises(PermissionError):
            system_event.save()
        with self.assertRaises(PermissionError):
            system_event.delete()

    def test_admin_list_with_date_drilldown_loads(self):
        # Needs MySQL time zone tables (setup_db.py loads them).
        admin = get_user_model().objects.create_superuser('root', 'root@example.com',
                                                          'pw-Strong-123')
        AuditLog.objects.create(action='seed', entity_type='core.Department', entity_id='X')
        self.client.force_login(admin)
        response = self.client.get('/admin/audit/auditlog/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'seed')

    def test_changes_json_round_trip(self):
        user = get_user_model().objects.create_user('hr1', 'hr@example.com', 'pw-Strong-123')
        event = AuditLog.objects.create(actor=user, action='update', entity_type='accounts.User',
                                        entity_id=str(user.pk),
                                        changes={'first_name': {'old': '', 'new': 'Ana'}})
        event.refresh_from_db()
        self.assertEqual(event.changes['first_name']['new'], 'Ana')
