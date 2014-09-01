import os.path
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import DefaultStorage, Storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import BooleanField, FilePathField
from django.test import TestCase
from django.test.utils import override_settings
from django.utils.datastructures import MultiValueDict
from django.utils.six.moves import reload_module
from smuggler import settings
from smuggler.forms import ImportForm, DumpStorageForm


p = lambda *args: os.path.abspath(os.path.join(os.path.dirname(__file__),
                                               *args))


class TestForm(TestCase):
    def test_requires_file(self):
        form = ImportForm({}, {})
        self.assertFalse(form.is_valid())
        self.assertEqual({'uploads': ["This field is required."]},
                         form.errors)

    def test_invalid_file_extension(self):
        f = SimpleUploadedFile('invalid.txt', b'invalid')
        form = ImportForm({}, {
            'uploads': f
        })
        self.assertFalse(form.is_valid())
        self.assertEqual({'uploads': ["Invalid file extension: .txt."]},
                         form.errors)

    def test_valid_file_extension(self):
        f = SimpleUploadedFile('valid.json', b'[]')
        form = ImportForm({}, {
            'uploads': f
        })
        self.assertTrue(form.is_valid())

    def test_valid_uppercase_file_extension(self):
        f = SimpleUploadedFile('valid.JSON', b'[]')
        form = ImportForm({}, {
            'uploads': f
        })
        self.assertTrue(form.is_valid())

    def test_mix_valid_and_invalid(self):
        form = ImportForm({}, MultiValueDict({
            'uploads': [
                SimpleUploadedFile('valid.json', b'[]'),
                SimpleUploadedFile('invalid.txt', b'invalid')
            ]
        }))
        self.assertFalse(form.is_valid())
        self.assertEqual({'uploads': ["Invalid file extension: .txt."]},
                         form.errors)

    @override_settings(SMUGGLER_FIXTURE_DIR=p('..', 'smuggler_fixtures'))
    def test_store_checkbox(self):
        reload_module(settings)
        form = ImportForm()
        self.assertIsInstance(form['store'].field, BooleanField)

    @override_settings(SMUGGLER_FIXTURE_DIR=p('..', 'smuggler_fixtures'))
    def test_picked_files(self):
        reload_module(settings)
        form = ImportForm()
        self.assertIsInstance(form['picked_files'].field, FilePathField)

    @override_settings(SMUGGLER_FIXTURE_DIR=p('..', 'smuggler_fixtures'))
    def test_requires_at_least_one_field(self):
        reload_module(settings)
        form = ImportForm({}, {})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            '__all__': [
                'At least one fixture file needs to be uploaded or selected.'
            ]})

    def tearDown(self):
        reload_module(settings)


class TestDumpStorageForm(TestCase):
    def setUp(self):
        self.form = DumpStorageForm()

    def test_requires_file(self):
        form = DumpStorageForm({})
        self.assertFalse(form.is_valid())
        self.assertEqual({'files': ["This field is required."]},
                         form.errors)

    def test_storage_attribute(self):
        self.assertIsInstance(self.form.storage, DefaultStorage)

    def test_validates_storage_methods(self):
        BadStorageForm = type('BadStorageForm', (DumpStorageForm,), {})
        BadStorageForm.storage_class = Storage
        with self.assertRaises(ImproperlyConfigured) as assertion:
            BadStorageForm().storage
            self.assertEqual(str(assertion), 'Storage class must implement'
                                             ' `listdir` and `path`.')

    def test_get_choice_keys(self):
        self.assertEqual(
            set(dict(self.form.fields['files'].choices).keys()),
            set(['files', 'uploads', 'uploads/sub', 'uploaded_file.txt']))

    def test_get_choice_values(self):
        self.assertEqual(
            set(dict(self.form.fields['files'].choices).values()),
            set([
                u'/files/ (2 files, 10\xa0bytes)',
                u'/uploads/ (1 file, 6\xa0bytes)',
                u'/uploads/sub/ (1 file, 9\xa0bytes)',
                u'/uploaded_file.txt (3\xa0bytes)']))
