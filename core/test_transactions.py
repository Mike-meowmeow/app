"""Every public service function that locks rows runs inside a transaction.

Django's TestCase wraps each test in a transaction, so a missing
@transaction.atomic is invisible to ordinary tests but fails on the server
with "select_for_update cannot be used outside of a transaction" (found on
28 November 2026: a helper inserted between the decorator and
record_qualification_outcome took the decorator). This test reads the code
instead of running it.
"""

import importlib
import inspect

from django.test import SimpleTestCase

MODULES = [
    'recruitment.application_services',
    'recruitment.decision_services',
    'recruitment.services',
    'documents.services',
    'committee.services',
    'matrix.services',
    'ai_processing.services',
    'ai_processing.screening',
    'ai_processing.matching',
]
LOCKING = ('select_for_update', '_lock(')
# Signal receivers that run inside the sender's transaction by design.
INSIDE_CALLER = {'matrix.services.mark_outdated'}


def _locks_rows(func):
    try:
        source = inspect.getsource(inspect.unwrap(func))
    except (OSError, TypeError):
        return False
    body = source.split(':', 1)[1] if ':' in source else source
    return any(marker in body for marker in LOCKING)


def _is_atomic(func):
    # transaction.atomic used as a decorator wraps with functools.wraps.
    return hasattr(func, '__wrapped__')


def _opens_transaction(func):
    """Locks only inside its own `with transaction.atomic():` block."""
    try:
        return 'transaction.atomic()' in inspect.getsource(inspect.unwrap(func))
    except (OSError, TypeError):
        return False


class LockingFunctionsAreAtomicTests(SimpleTestCase):
    def test_public_locking_functions_are_atomic(self):
        missing = []
        for name in MODULES:
            try:
                module = importlib.import_module(name)
            except ImportError:
                continue
            for attr, func in vars(module).items():
                if attr.startswith('_') or not inspect.isfunction(inspect.unwrap(func)):
                    continue
                if inspect.unwrap(func).__module__ != module.__name__:
                    continue  # imported from elsewhere
                if f'{name}.{attr}' in INSIDE_CALLER:
                    continue
                if _locks_rows(func) and not _is_atomic(func) and not _opens_transaction(func):
                    missing.append(f'{name}.{attr}')
        self.assertEqual(missing, [], 'Lock rows without @transaction.atomic: '
                                      + ', '.join(missing))

    def test_record_qualification_outcome_is_atomic(self):
        from recruitment import application_services
        self.assertTrue(_is_atomic(application_services.record_qualification_outcome))
        self.assertFalse(_is_atomic(application_services.fact_needs_evidence))
