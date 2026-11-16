from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Department, RecruitmentType


class ReferenceDataTests(TestCase):
    def test_department_code_unique(self):
        Department.objects.create(name='College of Education', code='COED')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Department.objects.create(name='Duplicate', code='COED')

    def test_recruitment_type_code_unique(self):
        RecruitmentType.objects.create(code='PTI', name='Part-Time Instructor')
        with self.assertRaises(IntegrityError), transaction.atomic():
            RecruitmentType.objects.create(code='PTI', name='Duplicate')

    def test_erd_table_names(self):
        self.assertEqual(Department._meta.db_table, 'departments')
        self.assertEqual(RecruitmentType._meta.db_table, 'recruitment_types')
