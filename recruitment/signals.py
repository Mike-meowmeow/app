"""Signals other modules listen to without recruitment importing them.

application_changed: sent inside the transaction whenever an application's
revision increases (a roster-visible change). The matrix module uses it to
mark affected roster versions as requiring regeneration.
"""

from django.dispatch import Signal

application_changed = Signal()  # kwargs: application
