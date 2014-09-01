from django.core.urlresolvers import reverse
from django.test import TestCase


class TestSmugglerUrls(TestCase):
    def test_can_reverse_dump_data(self):
        self.assertEqual(reverse('dump-data'), '/admin/smuggler/dump-data/')

    def test_can_reverse_dump_app_data(self):
        url = reverse('dump-app-data', kwargs={'app_label': 'sites'})
        self.assertEqual(url, '/admin/sites/dump/')

    def test_can_reverse_dump_model_data(self):
        url = reverse('dump-model-data', kwargs={
            'app_label': 'sites',
            'model_label': 'site'
        })
        self.assertEqual(url, '/admin/sites/site/dump/')

    def test_can_reverse_load_data(self):
        self.assertEqual(reverse('load-data'), '/admin/smuggler/load-data/')

    def test_can_reverse_dump_storage(self):
        self.assertEqual(reverse('dump-storage'), '/admin/smuggler/dump-storage/')
