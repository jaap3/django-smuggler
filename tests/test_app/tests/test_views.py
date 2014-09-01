import json
import os.path
import tarfile
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.urlresolvers import reverse
from django.test import TestCase, TransactionTestCase, Client
from django.test.utils import override_settings
from django.utils.six import assertRegex, BytesIO
from django.utils.six.moves import reload_module
from freezegun import freeze_time
from smuggler import settings
from smuggler.forms import ImportForm, DumpStorageForm, LoadStorageForm
from tests.test_app.models import Page


p = lambda *args: os.path.abspath(os.path.join(os.path.dirname(__file__),
                                               *args))


class SuperUserTestCase(object):
    def setUp(self):
        super(SuperUserTestCase, self).setUp()
        superuser = User(username='superuser')
        superuser.set_password('test')
        superuser.is_staff = True
        superuser.is_superuser = True
        superuser.save()
        self.c = Client()
        self.c.login(username='superuser', password='test')


class TestDumpViewsGenerateDownloadsWithSaneFilenames(SuperUserTestCase,
                                                      TestCase):
    @freeze_time('2012-01-14')
    def test_dump_data(self):
        url = reverse('dump-data')
        response = self.c.get(url)
        self.assertEqual(response['Content-Disposition'],
                         'attachment; filename=2012-01-14T00:00:00.json')

    @freeze_time('2012-01-14')
    def test_dump_app_data(self):
        url = reverse('dump-app-data', kwargs={'app_label': 'sites'})
        response = self.c.get(url)
        self.assertEqual(response['Content-Disposition'],
                         'attachment; filename=sites_2012-01-14T00:00:00.json')

    @freeze_time('2012-01-14')
    def test_dump_model_data(self):
        url = reverse('dump-model-data', kwargs={
            'app_label': 'sites',
            'model_label': 'site'
        })
        response = self.c.get(url)
        self.assertEqual(response['Content-Disposition'],
                         'attachment;'
                         ' filename=sites-site_2012-01-14T00:00:00.json')


class TestDumpData(SuperUserTestCase, TestCase):
    def test_dump_data_parameters(self):
        url = reverse('dump-data')
        response = self.c.get(url, {
            'app_label': 'auth.user,sites'
        })
        content = json.loads(response.content.decode('utf-8'))
        self.assertTrue([i for i in content if i['model'] == 'auth.user'])
        self.assertTrue([i for i in content if i['model'] == 'sites.site'])


class TestDumpHandlesErrorsGracefully(SuperUserTestCase, TestCase):
    def test_erroneous_dump_has_error_messages(self):
        url = reverse('dump-app-data', kwargs={'app_label': 'flatpages'})
        response = self.c.get(url, follow=True)
        response_messages = list(response.context['messages'])
        self.assertEqual(1, len(response_messages))
        self.assertEqual(messages.ERROR, response_messages[0].level)
        self.assertEqual(
            'An exception occurred while dumping data: '
            'Unknown application: flatpages',
            response_messages[0].message)

    def test_erroneous_dump_redirects(self):
        url = reverse('dump-app-data', kwargs={'app_label': 'flatpages'})
        response = self.c.get(url)
        self.assertRedirects(response, '/admin/flatpages/',
                             target_status_code=404)


class TestLoadDataGet(SuperUserTestCase, TestCase):
    def setUp(self):
        super(TestLoadDataGet, self).setUp()
        self.url = reverse('load-data')

    def test_renders_correct_template(self):
        response = self.c.get(self.url)
        self.assertTemplateUsed(response, 'smuggler/load_data_form.html')

    def test_has_form_in_context(self):
        response = self.c.get(self.url)
        self.assertIsInstance(response.context['form'],
                              ImportForm)

    def test_title(self):
        response = self.c.get(self.url)
        self.assertContains(response, '<title>Load Data')

    def test_enctype(self):
        response = self.c.get(self.url)
        self.assertContains(response, ' enctype="multipart/form-data"')

    def test_button_verb(self):
        response = self.c.get(self.url)
        self.assertContains(response, '<input type="submit" value="Load"')


class TestLoadDataPost(SuperUserTestCase, TransactionTestCase):
    def setUp(self):
        super(TestLoadDataPost, self).setUp()
        self.url = reverse('load-data')

    def test_load_fixture(self):
        self.assertEqual(0, Page.objects.count())
        f = open(p('..', 'smuggler_fixtures', 'page_dump.json'), mode='rb')
        self.c.post(self.url, {
            'uploads': f
        }, follow=True)
        self.assertEqual(1, Page.objects.count())

    def test_load_fixture_message(self):
        f = open(p('..', 'smuggler_fixtures', 'page_dump.json'), mode='rb')
        response = self.c.post(self.url, {
            'uploads': f
        }, follow=True)
        response_messages = list(response.context['messages'])
        self.assertEqual(1, len(response_messages))
        self.assertEqual(messages.INFO, response_messages[0].level)
        self.assertEqual(response_messages[0].message,
                         'Successfully imported 1 file. Loaded 1 object.')

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=0)
    def test_load_fixture_with_chunks(self):
        self.assertEqual(0, Page.objects.count())
        f = open(p('..', 'smuggler_fixtures', 'big_file.json'), mode='rb')
        self.c.post(self.url, {
            'uploads': f
        }, follow=True)
        self.assertEqual(1, Page.objects.count())

    def test_handle_garbage_upload(self):
        f = open(p('..', 'smuggler_fixtures', 'garbage', 'garbage.json'),
                 mode='rb')
        response = self.c.post(self.url, {
            'uploads': f
        }, follow=True)
        response_messages = list(response.context['messages'])
        self.assertEqual(1, len(response_messages))
        self.assertEqual(messages.ERROR, response_messages[0].level)
        assertRegex(self, response_messages[0].message,
                    ' No JSON object could be decoded')

    def test_handle_integrity_error(self):
        f = open(p('..', 'smuggler_fixtures', 'garbage',
                   'invalid_page_dump.json'), mode='rb')
        response = self.c.post(self.url, {
            'uploads': f
        }, follow=True)
        response_messages = list(response.context['messages'])
        self.assertEqual(1, len(response_messages))
        self.assertEqual(messages.ERROR, response_messages[0].level)
        assertRegex(self, response_messages[0].message,
                    r'(?i)An exception occurred while loading data:.*unique.*')

    @override_settings(SMUGGLER_FIXTURE_DIR=p('..', 'smuggler_fixtures'))
    def test_load_from_disk(self):
        reload_module(settings)
        self.assertEqual(0, Page.objects.count())
        self.c.post(self.url, {
            'picked_files': p('..', 'smuggler_fixtures', 'page_dump.json')
        }, follow=True)
        self.assertEqual(1, Page.objects.count())

    @override_settings(SMUGGLER_FIXTURE_DIR=p('..', 'smuggler_fixtures'))
    def test_load_from_disk_and_upload(self):
        reload_module(settings)
        f = open(p('..', 'smuggler_fixtures', 'page_dump.json'), mode='rb')
        response = self.c.post(self.url, {
            'uploads': f,
            'picked_files': p('..', 'smuggler_fixtures', 'page_dump.json')
        }, follow=True)
        response_messages = list(response.context['messages'])
        self.assertEqual(1, len(response_messages))
        self.assertEqual(messages.INFO, response_messages[0].level)
        self.assertEqual(response_messages[0].message,
                         'Successfully imported 2 files. Loaded 2 objects.')

    @override_settings(SMUGGLER_FIXTURE_DIR=p('..', 'smuggler_fixtures'))
    def test_load_and_save(self):
        reload_module(settings)
        f = SimpleUploadedFile('uploaded.json',
                               b'[{"pk": 1, "model": "test_app.page",'
                               b' "fields": {"title": "test",'
                               b' "path": "", "body": "test body"}}]')
        self.c.post(self.url, {
            'store': True,
            'uploads': f
        }, follow=True)
        self.assertTrue(os.path.exists(
            p('..', 'smuggler_fixtures', 'uploaded.json')))
        os.unlink(p('..', 'smuggler_fixtures', 'uploaded.json'))

    def tearDown(self):
        reload_module(settings)


class TestDumpStorageGet(SuperUserTestCase, TestCase):
    def setUp(self):
        super(TestDumpStorageGet, self).setUp()
        self.url = reverse('dump-storage')

    def test_renders_correct_template(self):
        response = self.c.get(self.url)
        self.assertTemplateUsed(response, 'smuggler/dump_storage_form.html')

    def test_has_form_in_context(self):
        response = self.c.get(self.url)
        self.assertIsInstance(response.context['form'],
                              DumpStorageForm)

    def test_title(self):
        response = self.c.get(self.url)
        self.assertContains(response, '<title>Dump Storage')

    def test_enctype(self):
        response = self.c.get(self.url)
        self.assertNotContains(response, ' enctype="multipart/form-data"')

    def test_button_verb(self):
        response = self.c.get(self.url)
        self.assertContains(response, '<input type="submit" value="Dump"')


class TestDumpStoragePostBasic(SuperUserTestCase, TestCase):
    def setUp(self):
        super(TestDumpStoragePostBasic, self).setUp()
        url = reverse('dump-storage')
        with freeze_time('2012-01-14'):
            self.response = self.c.post(url, {
                'files': [
                    'files', 'uploads', 'uploads/sub', 'uploaded_file.txt'
                ]})

    def test_contenttype(self):
        self.assertEqual(self.response.content_type,
                         'application/x-compressed')

    def test_filename(self):
        self.assertEqual(self.response['Content-Disposition'],
                         'attachment; filename=2012-01-14T00:00:00.tgz')

    def test_archive(self):
        f = BytesIO()
        for data in self.response.streaming_content:
            f.write(data)
        f.seek(0)
        archive = tarfile.open(fileobj=f,
                               mode='r:gz')
        self.assertEqual(set(archive.getnames()), set([
            'files/uploaded_file_2.txt',
            'files/uploaded_file.txt',
            'files',
            'uploads/uploaded_file.txt',
            'uploads',
            'uploads/sub/uploaded_file.txt',
            'uploads/sub',
            'uploaded_file.txt'
        ]))


class TestDumpStoragePost(SuperUserTestCase, TestCase):
    def test_does_not_recurse_into_subdir(self):
        url = reverse('dump-storage')
        response = self.c.post(url, {
            'files': [
                'uploads'
            ]})
        f = BytesIO()
        for data in response.streaming_content:
            f.write(data)
        f.seek(0)
        archive = tarfile.open(fileobj=f,
                               mode='r:gz')
        self.assertEqual(set(archive.getnames()), set([
            'uploads/uploaded_file.txt',
            'uploads',
        ]))


class TestLoadStorageGet(SuperUserTestCase, TestCase):
    def setUp(self):
        super(TestLoadStorageGet, self).setUp()
        self.url = reverse('load-storage')

    def test_renders_correct_template(self):
        response = self.c.get(self.url)
        self.assertTemplateUsed(response, 'smuggler/load_storage_form.html')

    def test_has_form_in_context(self):
        response = self.c.get(self.url)
        self.assertIsInstance(response.context['form'],
                              LoadStorageForm)

    def test_title(self):
        response = self.c.get(self.url)
        self.assertContains(response, '<title>Load Storage')

    def test_enctype(self):
        response = self.c.get(self.url)
        self.assertContains(response, ' enctype="multipart/form-data"')

    def test_button_verb(self):
        response = self.c.get(self.url)
        self.assertContains(response, '<input type="submit" value="Load"')
