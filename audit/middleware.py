from .context import bind_request


class AuditContextMiddleware:
    """Makes the current request available to the audit service, so events
    recorded by services and signals carry the actor and IP address."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with bind_request(request):
            return self.get_response(request)
