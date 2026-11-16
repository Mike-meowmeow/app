"""Register the official roster form (an .xlsx file) for a recruitment type.

Examples:
  manage.py register_roster_template "MATRIX_TEACHING APPLICANTS FOR PSB.xlsx" --approve-as Mike
  manage.py register_roster_template --sample --approve-as Mike   (development only)

The file is validated against the PART-TIME layout, copied to private
storage, and recorded with its SHA-256. The original file is not modified.
"""

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from audit.context import audit_reason
from core.models import RecruitmentType
from matrix import layout, services


class Command(BaseCommand):
    help = 'Register (and optionally approve) an official roster template workbook.'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', help='path to the .xlsx template')
        parser.add_argument('--sample', action='store_true',
                            help='register a synthetic development template instead of a file')
        parser.add_argument('--recruitment-type', default='PTI')
        parser.add_argument('--name', default='')
        parser.add_argument('--approve-as', metavar='USERNAME',
                            help='approve immediately as this user (HR Head / Approver or '
                                 'System Administrator)')
        for key, label in (('prepared-name', 'Prepared by name'),
                           ('prepared-position', 'Prepared by position'),
                           ('noted-name', 'Noted by name'),
                           ('noted-position', 'Noted by position')):
            parser.add_argument(f'--{key}', default='', help=f'default {label}')

    def handle(self, *args, path=None, sample=False, recruitment_type='PTI', name='',
               approve_as=None, **options):
        if bool(path) == bool(sample):
            raise CommandError('Give the template path, or --sample for a development template.')
        try:
            rtype = RecruitmentType.objects.get(code=recruitment_type)
        except RecruitmentType.DoesNotExist:
            raise CommandError(f'Unknown recruitment type "{recruitment_type}". '
                               'Run manage.py seed_reference_data first.')
        approver = None
        if approve_as:
            try:
                approver = get_user_model().objects.get(username=approve_as)
            except get_user_model().DoesNotExist:
                raise CommandError(f'No user named "{approve_as}".')

        signatories = {'schema_version': 1,
                       'prepared_by': {'name': options['prepared_name'],
                                       'position': options['prepared_position']},
                       'noted_by': {'name': options['noted_name'],
                                    'position': options['noted_position']}}
        with tempfile.TemporaryDirectory() as tmp, audit_reason('Roster template registration'):
            if sample:
                source = layout.build_sample_template(Path(tmp) / 'sample.xlsx')
                name = name or 'PART-TIME roster (synthetic development template)'
            else:
                source = Path(path)
                if not source.is_file():
                    raise CommandError(f'File not found: {source}')
                name = name or 'PART-TIME roster (MATRIX_TEACHING APPLICANTS FOR PSB)'
            try:
                template = services.register_template(source, rtype, name=name,
                                                      signatories=signatories, actor=approver)
                self.stdout.write(f'Registered {template.name} v{template.version} '
                                  f'(sha256 {template.sha256[:12]}...).')
                if approver:
                    services.approve_template(approver, template)
                    self.stdout.write(self.style.SUCCESS('Approved; earlier versions retired.'))
                else:
                    self.stdout.write('Status: Draft. Approve it at /rosters/templates/ '
                                      '(HR Head / Approver or System Administrator).')
            except PermissionDenied as exc:
                raise CommandError(str(exc) or 'That user cannot approve templates.')
            except ValidationError as exc:
                raise CommandError('; '.join(exc.messages))
        if not all(signatories['prepared_by'].values()) or not all(signatories['noted_by'].values()):
            self.stdout.write('Default signatories are incomplete; set them at /rosters/templates/.')
