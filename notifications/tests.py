"""Stage 8 notification tests (FR-014), including the guide's gate: one event
creates one recipient notification, a failed delivery can be retried safely,
and private notes do not leak into messages."""

from datetime import timedelta
from smtplib import SMTPException
from unittest import mock

from django.core import mail
from django.db import transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.test_stage3 import make_user
from committee.tests import CommitteeTestCase
from documents.models import VerificationStatus
from documents import services as documents
from recruitment import application_services as applications
from recruitment import decision_services as decisions
from recruitment.models import DecisionOutcome, DecisionType

from . import services
from .models import Category, DeliveryStatus, Notification, NotificationDelivery

SECRET = 'INTERNAL-NOTE-7731'


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   NOTIFICATION_EMAIL_ENABLED=True, NOTIFICATION_MAX_ATTEMPTS=3)
class NotificationTests(CommitteeTestCase):
    """CommitteeTestCase gives a submitted application under review, a committee
    with two PSB members, HR (hr1), and an approver."""

    def fresh_application(self, username='newapp'):
        user = make_user(username, roles.APPLICANT, last_name='Reyes')
        self.complete_profile(user)
        application = applications.start_application(user, self.vacancy)[0]
        documents.upload(user, application, self.pds, self.pdf_file())
        return user, application

    def pdf_file(self):
        from recruitment.test_applications import pdf
        return pdf('pds.pdf')

    def test_one_event_one_notification_per_recipient(self):
        user, application = self.fresh_application()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            applications.submit(user, application)
        with self.captureOnCommitCallbacks(execute=True):
            applications.submit(user, application)  # repeated request
        key = f'application.submitted:{application.pk}'
        self.assertEqual(Notification.objects.filter(event_key=key, user=user).count(), 1)
        self.assertEqual(Notification.objects.filter(event_key=key, user=self.hr).count(), 1)
        self.assertEqual(Notification.objects.filter(event_key=key).count(), 2)  # applicant+HR
        self.assertEqual(len(mail.outbox), 2)
        delivery = NotificationDelivery.objects.get(notification__user=user,
                                                    notification__event_key=key)
        self.assertEqual((delivery.status, delivery.attempt_number), (DeliveryStatus.SENT, 1))
        # notify() itself is idempotent per recipient and event.
        again = services.notify(user, category=Category.SYSTEM, event_key=key, title='x',
                                message='y')
        self.assertEqual(Notification.objects.filter(event_key=key, user=user).count(), 1)
        self.assertEqual(again.title, 'Application submitted')

    def test_rolled_back_action_sends_nothing(self):
        user, application = self.fresh_application()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    applications.submit(user, application)
                    raise RuntimeError('database failure after the business change')
        self.assertFalse(Notification.objects.filter(application=application).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_failed_delivery_retries_safely(self):
        user, application = self.fresh_application()
        mail.outbox.clear()
        with mock.patch('notifications.services.send_mail',
                        side_effect=SMTPException('SMTP server unavailable')):
            with self.captureOnCommitCallbacks(execute=True):
                applications.submit(user, application)
        notification = Notification.objects.get(user=user,
                                                event_key=f'application.submitted:{application.pk}')
        first = notification.deliveries.get()
        self.assertEqual(first.status, DeliveryStatus.FAILED)
        self.assertIn('SMTP server unavailable', first.error_message)
        self.assertEqual(len(mail.outbox), 0)
        application.refresh_from_db()
        self.assertEqual(application.status, 'submitted')  # the action itself succeeded

        sent, failed = services.retry_pending(notification_ids=[notification.pk])
        self.assertEqual((sent, failed), (1, 0))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(list(notification.deliveries.values_list('attempt_number', 'status')),
                         [(1, DeliveryStatus.FAILED), (2, DeliveryStatus.SENT)])
        self.assertEqual(services.retry_pending(notification_ids=[notification.pk]), (0, 0))
        services.deliver(notification.pk)  # even a direct second call does not resend
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Notification.objects.filter(user=user, application=application).count(),
                         1)

    def test_retries_stop_at_the_limit(self):
        user, application = self.fresh_application()
        with mock.patch('notifications.services.send_mail', side_effect=SMTPException('down')):
            with self.captureOnCommitCallbacks(execute=True):
                applications.submit(user, application)
            services.retry_pending()
            services.retry_pending()
            services.retry_pending()
        notification = Notification.objects.get(user=user,
                                                event_key=f'application.submitted:{application.pk}')
        self.assertEqual(notification.deliveries.count(), 3)
        self.assertNotIn(notification, services.pending_email())

    def test_private_notes_do_not_leak(self):
        version = self.application.documents.first().current_version()
        with self.captureOnCommitCallbacks(execute=True):
            documents.verify(self.hr, version, status=VerificationStatus.INCOMPLETE,
                             notes=f'{SECRET} blurry scan')
            applications.request_correction(
                self.hr, self.application, message=f'Please re-upload. {SECRET}',
                deadline_at=timezone.now() + timedelta(days=3))
        self.application.refresh_from_db()
        texts = [f'{n.title} {n.message}' for n in Notification.objects.all()]
        texts += [f'{m.subject} {m.body}' for m in mail.outbox]
        self.assertTrue(any('Corrections requested' in t for t in texts))
        self.assertFalse([t for t in texts if SECRET in t])

    def test_release_notifies_only_after_release_without_internal_reason(self):
        with self.captureOnCommitCallbacks(execute=True):
            decision = decisions.record_decision(
                self.hr, self.application, decision_type=DecisionType.SHORTLISTING,
                outcome=DecisionOutcome.SHORTLISTED, reason=f'{SECRET} strong demo')
        self.assertFalse(Notification.objects.filter(user=self.applicant,
                                                     event_type=Category.RECOMMENDATION).exists())
        with self.captureOnCommitCallbacks(execute=True):
            decisions.release_decision(self.hr, decision, note='Congratulations')
        notification = Notification.objects.get(user=self.applicant,
                                                event_key=f'decision.released:{decision.pk}')
        self.assertNotIn(SECRET, notification.message)
        self.assertFalse([m for m in mail.outbox if SECRET in m.body])

    def test_interview_changes_notify_applicant_and_panel(self):
        self.shortlist()
        interview = self.schedule()
        recipients = set(Notification.objects.filter(interview=interview)
                         .values_list('user__username', flat=True))
        self.assertEqual(recipients, {self.applicant.username, 'psb1', 'psb2'})
        new_start = interview.starts_at + timedelta(days=1)
        from committee import services as committee
        committee.reschedule_interview(self.hr, interview, reason='Room conflict',
                                       starts_at=new_start, ends_at=new_start + timedelta(hours=1),
                                       mode='in_person', location='Room 2')
        titles = list(Notification.objects.filter(user=self.applicant, interview=interview)
                      .values_list('title', flat=True))
        self.assertIn('Interview rescheduled', titles)
        self.assertFalse(Notification.objects.filter(message__icontains='Room conflict').exists())

    def test_committee_and_conflict_tasks(self):
        self.assertTrue(Notification.objects.filter(user=self.psb1,
                                                    title='Committee assignment').exists())
        self.shortlist()
        from committee import services as committee
        committee.declare_conflict(self.psb2, self.member2, self.application,
                                   declaration=f'{SECRET} cousin')
        note = Notification.objects.get(user=self.approver,
                                        title='Conflict of interest to resolve')
        self.assertNotIn(SECRET, note.message)

    def test_inbox_screens(self):
        n = Notification.objects.filter(user=self.applicant).first()
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('notifications:list'))
        self.assertContains(response, n.title)
        self.assertEqual(response.context['unread_notifications'],
                         Notification.objects.filter(user=self.applicant).count())
        response = self.client.get(reverse('notifications:open', args=[n.pk]))
        self.assertRedirects(response, reverse('recruitment:my_application',
                                               args=[self.application.pk]),
                             fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)
        self.client.force_login(self.hr)  # someone else's notification
        self.assertEqual(self.client.get(reverse('notifications:open', args=[n.pk])).status_code,
                         404)
        self.assertEqual(self.client.get(reverse('notifications:deliveries')).status_code, 403)
        admin = make_user('sysadmin', roles.SYSTEM_ADMIN)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse('notifications:deliveries')).status_code, 200)
        self.assertEqual(self.client.post(reverse('notifications:retry')).status_code, 302)
