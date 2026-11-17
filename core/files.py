"""Private file responses (documents, roster exports, reports).

Callers check permissions and audit first. With PRIVATE_FILE_DELIVERY='nginx'
the response carries only an X-Accel-Redirect to an internal location (Nginx
`internal;` block mapped to PRIVATE_STORAGE_ROOT), so files are never
reachable by URL without passing Django's checks. The default 'django'
streams the file from the application (development and small deployments).
"""

from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.utils.http import content_disposition_header


def private_file_response(path, *, filename, as_attachment=True, content_type=None):
    path = Path(path)
    if not path.is_file():
        raise Http404
    if settings.PRIVATE_FILE_DELIVERY == 'nginx':
        root = Path(settings.PRIVATE_STORAGE_ROOT).resolve()
        resolved = path.resolve()
        if root not in resolved.parents:
            raise PermissionDenied('File is outside private storage.')
        response = HttpResponse(content_type=content_type or 'application/octet-stream')
        response['X-Accel-Redirect'] = (settings.PRIVATE_FILE_INTERNAL_PREFIX.rstrip('/') + '/'
                                        + quote(resolved.relative_to(root).as_posix()))
        response['Content-Disposition'] = content_disposition_header(as_attachment, filename)
    else:
        response = FileResponse(open(path, 'rb'), as_attachment=as_attachment,
                                filename=filename, content_type=content_type)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response
