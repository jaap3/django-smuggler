# Copyright (c) 2009 Guilherme Gondim and contributors
#
# This file is part of Django Smuggler.
#
# Django Smuggler is free software under terms of the GNU Lesser
# General Public License version 3 (LGPLv3) as published by the Free
# Software Foundation. See the file README for copying conditions.
import os.path
import tarfile
import tempfile
from datetime import datetime
from django.contrib.admin.helpers import AdminForm
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import CommandError
from django.core.serializers.base import DeserializationError
from django.db import IntegrityError
from django.http import HttpResponseRedirect
from django.utils.encoding import force_text
from django.utils.six import BytesIO
from django.utils.translation import ugettext_lazy as _, ungettext_lazy
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.views.generic.edit import FormView
from smuggler.forms import ImportForm, DumpStorageForm, LoadStorageForm
from smuggler import settings
from smuggler.utils import (save_uploaded_file_on_disk, serialize_to_response,
                            load_fixtures)
try:
    from django.http import StreamingHttpResponse
except ImportError:  # Django < 1.5
    from django.http import HttpResponse as StreamingHttpResponse


def dump_to_response(request, app_label=[], exclude=[], filename_prefix=None):
    """Utility function that dumps the given app/model to an HttpResponse.
    """
    try:
        filename = '%s.%s' % (datetime.now().isoformat(),
                              settings.SMUGGLER_FORMAT)
        if filename_prefix:
            filename = '%s_%s' % (filename_prefix, filename)
        if not isinstance(app_label, list):
            app_label = [app_label]
        response = serialize_to_response(app_label, exclude)
        response['Content-Disposition'] = 'attachment; filename=%s' % filename
        return response
    except CommandError as e:
        messages.error(
            request,
            _('An exception occurred while dumping data: %s') % force_text(e))
    return HttpResponseRedirect(request.build_absolute_uri().split('dump')[0])


def is_superuser(u):
    if u.is_authenticated():
        if u.is_superuser:
            return True
        raise PermissionDenied
    return False


@user_passes_test(is_superuser)
def dump_data(request):
    """Exports data from whole project.
    """
    # Try to grab app_label data
    app_label = request.GET.get('app_label', [])
    if app_label:
        app_label = app_label.split(',')
    return dump_to_response(request, app_label=app_label,
                            exclude=settings.SMUGGLER_EXCLUDE_LIST)


@user_passes_test(is_superuser)
def dump_app_data(request, app_label):
    """Exports data from a application.
    """
    return dump_to_response(request, app_label, settings.SMUGGLER_EXCLUDE_LIST,
                            app_label)


@user_passes_test(is_superuser)
def dump_model_data(request, app_label, model_label):
    """Exports data from a model.
    """
    return dump_to_response(request, '%s.%s' % (app_label, model_label),
                            [], '-'.join((app_label, model_label)))


class AdminFormMixin(object):
    def get_context_data(self, **kwargs):
        context = super(AdminFormMixin, self).get_context_data(
            adminform=self.get_admin_form(kwargs['form']),
            **kwargs)
        return context

    def get_fieldsets(self, form):
        if hasattr(self, 'fieldsets'):
            return self.fieldsets
        else:
            fields = form.fields.keys()
            return [(None, {'fields': fields})]

    def get_admin_form(self, form):
        return AdminForm(form, self.get_fieldsets(form), {})


class LoadDataView(AdminFormMixin, FormView):
    form_class = ImportForm
    template_name = 'smuggler/load_data_form.html'
    success_url = '.'

    def form_valid(self, form):
        uploads = form.cleaned_data.get('uploads', [])
        store = form.cleaned_data.get('store', False)
        picked_files = form.cleaned_data.get('picked_files', [])
        fixtures = []
        tmp_fixtures = []
        for upload in uploads:
            file_name = upload.name
            if store:  # Store the file in SMUGGLER_FIXTURE_DIR
                destination_path = os.path.join(
                    settings.SMUGGLER_FIXTURE_DIR, file_name)
                save_uploaded_file_on_disk(upload, destination_path)
            else:  # Store the file in a tmp file
                prefix, suffix = os.path.splitext(file_name)
                destination_path = tempfile.mkstemp(
                    suffix=suffix, prefix=prefix + '_')[1]
                save_uploaded_file_on_disk(upload, destination_path)
                tmp_fixtures.append(destination_path)
            fixtures.append(destination_path)
        for file_name in picked_files:
            fixtures.append(file_name)
        try:
            obj_count = load_fixtures(fixtures)
            user_msg = ' '.join([
                ungettext_lazy(
                    'Successfully imported %(count)d file.',
                    'Successfully imported %(count)d files.',
                    len(fixtures)
                ) % {'count': len(fixtures)},
                ungettext_lazy(
                    'Loaded %(count)d object.',
                    'Loaded %(count)d objects.',
                    obj_count
                ) % {'count': obj_count}])
            messages.info(self.request, user_msg)
        except (IntegrityError, ObjectDoesNotExist,
                DeserializationError, CommandError) as e:
            messages.error(
                self.request,
                _('An exception occurred while loading data: %s') % str(e))
        finally:
            # Remove our tmp files
            for tmp_file in tmp_fixtures:
                os.unlink(tmp_file)
        return super(LoadDataView, self).form_valid(form)

    def get_fieldsets(self, form):
        fields = form.fields.keys()
        if 'picked_files' in fields:
            return [
                (_('Upload'), {'fields': ['uploads', 'store']}),
                (_('From fixture directory'), {'fields': ['picked_files']})
            ]
        return [(None, {'fields': fields})]

    def get_context_data(self, **kwargs):
        return super(LoadDataView, self).get_context_data(
            title=_('Load Data'), form_name='load_data',
            action_verb=_('Load'), has_file_field=True, **kwargs)


class DumpStorageView(AdminFormMixin, FormView):
    archive_format = 'tgz'
    form_class = DumpStorageForm
    template_name = 'smuggler/dump_storage_form.html'
    success_url = '.'

    def archive_generator(self, base_dir, file_list):
        out = BytesIO()
        archive = tarfile.open(fileobj=out, mode='w|gz')
        for path in file_list:
            archive.add(path, os.path.relpath(path, base_dir),
                        recursive=False)
            yield out.getvalue()
            out.truncate(0)
        archive.close()
        yield out.getvalue()

    def get_file_list(self, selected, storage):
        file_list = []
        for path in selected:
            abspath = storage.path(path)
            if os.path.isdir(abspath):
                file_list += [storage.path(os.path.join(abspath, fn))
                              for fn in storage.listdir(path)[1]]
            file_list.append(abspath)
        return file_list

    def form_valid(self, form):
        file_list = self.get_file_list(form.cleaned_data['files'],
                                       form.storage)
        filename = '%s.%s' % (datetime.now().isoformat(),
                              self.archive_format)
        response = StreamingHttpResponse(
            self.archive_generator(form.base_dir, file_list))
        response.content_type = 'application/x-compressed'
        response['Content-Disposition'] = 'attachment; filename=%s' % filename
        return response

    def get_context_data(self, **kwargs):
        return super(DumpStorageView, self).get_context_data(
            title=_('Dump Storage'), form_name='dump_storage',
            action_verb=_('Dump'),  **kwargs)


class LoadStorageView(AdminFormMixin, FormView):
    form_class = LoadStorageForm
    template_name = 'smuggler/load_storage_form.html'
    success_url = '.'

    def form_valid(self, form):
        extracted = 0
        skipped = 0
        try:
            archive = tarfile.open(fileobj=form.cleaned_data['upload'],
                                   mode='r:gz')
            for fn in archive.getnames():
                content = archive.extractfile(fn)
                if content is None:
                    # content is None for directories.
                    continue
                if not form.storage.exists(fn):
                    if not hasattr(content, 'chunks'):
                        # Hack in chunks for Django < 1.6
                        content.chunks = lambda: iter(content.readlines())
                    form.storage.save(fn, content)
                    extracted += 1
                else:
                    skipped += 1
        except tarfile.TarError as e:
            messages.error(
                self.request,
                _('An exception occurred while extracting: %s') % str(e))
        else:
            user_msg = ' '.join([
                ungettext_lazy('Extracted %(count)d file.',
                               'Extracted %(count)d files.',
                               extracted) % {'count': extracted},
                ungettext_lazy('Skipped %(count)d file.',
                               'Skipped %(count)d files.',
                               skipped) % {'count': skipped}])
            messages.info(self.request, user_msg)
        return super(LoadStorageView, self).form_valid(form)

    def get_context_data(self, **kwargs):
        return super(LoadStorageView, self).get_context_data(
            title=_('Load Storage'), form_name='load_storage',
            action_verb=_('Load'), has_file_field=True, **kwargs)


load_data = user_passes_test(is_superuser)(LoadDataView.as_view())
dump_storage = user_passes_test(is_superuser)(DumpStorageView.as_view())
load_storage = user_passes_test(is_superuser)(LoadStorageView.as_view())
