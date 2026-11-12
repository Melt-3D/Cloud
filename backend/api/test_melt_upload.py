from unittest.mock import patch

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from app.models import GCodeFile, User
from api.tests import setup_syndicate
from app.test_melt_auth import AUTH, access, answer

URL = '/api/v1/melt/g_code_files/'
SLICER = 'https://slicer.example'


def gcode(name='plate.gcode'):
    return SimpleUploadedFile(name, b'G1 X10 Y10 E0.01\n', content_type='text/x-gcode')


@override_settings(SITE_ID=1, MELT_AUTH_URL=AUTH, CORS_ALLOWED_ORIGINS=[SLICER])
@patch('api.viewsets.save_file_obj', return_value=('internal-url', 'external-url'))
@patch('api.viewsets.gcode_metadata.parse', return_value=({'estimated_time': 120}, []))
class MeltGCodeFileUploadTestCase(TestCase):
    """The Slicer's Print button: G-code sent with a Melt3D account's token."""

    def setUp(self):
        self.syndicate = setup_syndicate()
        self.client = Client()

    def upload(self, token='app-token', **extra):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
        return self.client.post(URL, {'filename': 'plate.gcode', 'file': gcode()}, **headers, **extra)

    def test_token_uploads_for_the_account_and_answers_the_print_page(self, parse, save_file_obj):
        with patch('app.melt_auth.requests.get', return_value=answer(200, access())) as get:
            resp = self.upload()

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer app-token')
        file = GCodeFile.objects.get(filename='plate.gcode')
        self.assertEqual(file.user.email, 'maker@example.com')
        self.assertEqual(resp.json()['id'], file.id)
        self.assertEqual(resp.json()['print_url'], f'http://testserver/g_code_files/cloud/{file.id}/?print=1')

    def test_the_account_is_the_cloud_user_with_its_email(self, parse, save_file_obj):
        user = User.objects.create(email='maker@example.com', syndicate=self.syndicate)
        with patch('app.melt_auth.requests.get', return_value=answer(200, access())):
            self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(GCodeFile.objects.get(filename='plate.gcode').user, user)

    def test_an_ended_session_is_refused(self, parse, save_file_obj):
        with patch('app.melt_auth.requests.get', return_value=answer(401)):
            resp = self.upload()
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(GCodeFile.objects.exists())

    def test_an_account_without_access_is_refused(self, parse, save_file_obj):
        with patch('app.melt_auth.requests.get', return_value=answer(200, access(has_access=False))):
            resp = self.upload()
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(User.objects.filter(email='maker@example.com').exists())

    def test_an_unreachable_auth_server_is_a_503(self, parse, save_file_obj):
        with patch('app.melt_auth.requests.get', side_effect=requests.ConnectionError('down')):
            resp = self.upload()
        self.assertEqual(resp.status_code, 503)

    def test_no_token_is_refused(self, parse, save_file_obj):
        with patch('app.melt_auth.requests.get') as get:
            resp = self.upload(token=None)
        self.assertIn(resp.status_code, (401, 403))
        get.assert_not_called()

    def test_a_signed_in_session_is_not_enough(self, parse, save_file_obj):
        # Cookies alone must never upload: the endpoint answers other origins.
        user = User.objects.create(email='session@example.com', syndicate=self.syndicate)
        self.client.force_login(user)
        resp = self.upload(token=None)
        self.assertIn(resp.status_code, (401, 403))

    def test_only_upload_is_routed(self, parse, save_file_obj):
        with patch('app.melt_auth.requests.get', return_value=answer(200, access())):
            resp = self.client.get(URL, HTTP_AUTHORIZATION='Bearer app-token')
        self.assertEqual(resp.status_code, 405)

    def test_the_slicer_origin_may_call_it(self, parse, save_file_obj):
        resp = self.client.options(
            URL,
            HTTP_ORIGIN=SLICER,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization',
        )
        self.assertEqual(resp['Access-Control-Allow-Origin'], SLICER)
        self.assertIn('authorization', resp['Access-Control-Allow-Headers'])

    def test_other_origins_and_other_paths_get_no_cors(self, parse, save_file_obj):
        other = self.client.options(URL, HTTP_ORIGIN='https://evil.example', HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST')
        self.assertNotIn('Access-Control-Allow-Origin', other)
        session_api = self.client.options('/api/v1/g_code_files/', HTTP_ORIGIN=SLICER, HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST')
        self.assertNotIn('Access-Control-Allow-Origin', session_api)
