from django.contrib.auth.decorators import login_required
from django.http import Http404

from core.files import private_file_response

from . import services, storage
from .models import DocumentVersion

INLINE_TYPES = {'application/pdf', 'image/jpeg', 'image/png'}


@login_required
def download(request, pk):
    """Serve a private file only to permitted users (owner or reviewers of the
    vacancy). Others get 404 so file existence is not revealed."""
    try:
        version = DocumentVersion.objects.select_related(
            'document__application__vacancy', 'document__application__applicant').get(pk=pk)
    except DocumentVersion.DoesNotExist:
        raise Http404
    if not services.can_download(request.user, version) or not version.is_usable:
        raise Http404
    path = storage.path_for(version.storage_key)
    if not path.exists():
        raise Http404
    services.record_download(request.user, version)
    inline = request.GET.get('inline') == '1' and version.mime_type in INLINE_TYPES
    return private_file_response(path, as_attachment=not inline, filename=version.file_name,
                                 content_type=version.mime_type)
