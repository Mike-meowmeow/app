"""The qualification standard form shows only the fields a criterion uses
(static/recruitment/standard_form.js) and ignores values left in the others."""

import json
import re

from django.test import SimpleTestCase, TestCase

from .forms import STANDARD_FIELDS, QualificationStandardForm
from .models import CriterionType


class StandardFormTests(TestCase):  # the name field reads the credential list
    def test_criterion_box_carries_the_field_rules_and_script(self):
        html = str(QualificationStandardForm()['criterion_type'])
        rules = json.loads(re.search(r'data-standard-fields="([^"]+)"', html).group(1)
                           .replace('&quot;', '"'))
        self.assertEqual(rules['education'], ['degree_level', 'field'])
        self.assertEqual(rules['license'], ['name'])
        self.assertEqual(rules['other'], [])
        labels = json.loads(re.search(r'data-field-labels="([^"]+)"', html).group(1)
                            .replace('&quot;', '"'))
        self.assertEqual(labels['amount']['training'], 'Minimum hours of training')
        self.assertEqual(labels['name']['eligibility'], 'Required eligibility')
        self.assertIn('recruitment/standard_form.js?v=', html)

    def test_values_in_unused_fields_are_ignored(self):
        form = QualificationStandardForm({
            'criterion_type': CriterionType.LICENSE, 'name': 'Professional Teacher',
            'degree_level': 'master', 'amount': '3', 'field': 'Mathematics',
            'description': 'A professional teacher license.'})
        self.assertTrue(form.is_valid(), form.errors)
        data = form.cleaned_data
        self.assertEqual((data['degree_level'], data['amount'], data['field']), ('', None, ''))
        self.assertEqual(data['name'], 'Professional Teacher')
        form = QualificationStandardForm({
            'criterion_type': CriterionType.EDUCATION, 'degree_level': 'master',
            'field': 'Mathematics', 'name': 'Professional Teacher',
            'description': "Master's degree in Mathematics."})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['name'], '')
        self.assertEqual(form.cleaned_data['field'], 'Mathematics')

    def test_the_needed_field_is_still_required(self):
        form = QualificationStandardForm({'criterion_type': CriterionType.TRAINING,
                                          'name': 'ignored', 'description': 'Training.'})
        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)


class FieldRulesTests(SimpleTestCase):
    def test_every_criterion_has_a_rule(self):
        self.assertEqual(set(STANDARD_FIELDS), set(CriterionType.values))
