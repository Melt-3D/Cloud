from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import requests
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from django.urls import include, path

from app import melt_auth
from app.models import User
from app.models.syndicate_models import Syndicate

AUTH = 'https://auth.example'
SECRET = 'handoff-secret'

# The Melt3D views ahead of the rest, whatever MELT_AUTH_URL was at startup
# (app/urls.py reads it once).
urlpatterns = [
    path('accounts/login/', melt_auth.login_redirect, name='account_login'),
    path('accounts/signup/', melt_auth.signup_redirect, name='account_signup'),
    path('accounts/logout/', melt_auth.logout_redirect, name='account_logout'),
    path('accounts/melt3d/session/', melt_auth.handoff_return, name='melt_auth_session'),
    path('', include('config.urls')),
]


def answer(status, body=None):
    resp = MagicMock(status_code=status)
    resp.json.return_value = body
    return resp


def access(email='maker@example.com', has_access=True, token='app-token'):
    return {
        'signedIn': True,
        'user': {'name': 'Maker', 'email': email},
        'hasAccess': has_access,
        'canManage': False,
        'token': token,
        'expiresIn': 86400,
    }


@override_settings(ROOT_URLCONF=__name__, MELT_AUTH_URL=AUTH, MELT_AUTH_HANDOFF_SECRET=SECRET, MELT_AUTH_RECHECK_SECONDS=900)
class MeltAuthHandoffTestCase(TestCase):

    def setUp(self):
        self.syndicate, _ = Syndicate.objects.get_or_create(id=1, defaults={'name': 'base'})
        site, _ = Site.objects.get_or_create(domain='testserver', defaults={'name': 'testserver'})
        self.syndicate.sites.add(site)
        site_id = override_settings(SITE_ID=site.id)
        site_id.enable()
        self.addCleanup(site_id.disable)

    def start(self, path='/accounts/login/?next=/printers/'):
        """The redirect to /handoff, and its query."""
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 302)
        location = urlparse(resp['Location'])
        self.assertEqual(f'{location.scheme}://{location.netloc}{location.path}', f'{AUTH}/handoff')
        return {k: v[0] for k, v in parse_qs(location.query).items()}

    def come_back(self, query, ticket='ticket', redeemed=None):
        """The browser back from /handoff with a ticket, redeemed as `redeemed`."""
        return_to = urlparse(query['return_to'])
        with patch('app.melt_auth.requests.post', return_value=redeemed or answer(200, access())) as post:
            resp = self.client.get(f'{return_to.path}?{return_to.query}&ticket={ticket}')
        return resp, post

    def sign_in(self, **kwargs):
        resp, _ = self.come_back(self.start(), **kwargs)
        self.assertEqual(resp.status_code, 302)
        return resp

    def signed_in_user_id(self):
        return self.client.session.get('_auth_user_id')

    # --- Starting a sign-in ----------------------------------------------------

    def test_login_goes_to_handoff_with_a_state(self):
        query = self.start()
        self.assertEqual(query['mode'], 'login')
        self.assertRegex(query['state'], r'^[A-Za-z0-9_-]{22,128}$')
        return_to = urlparse(query['return_to'])
        self.assertEqual(return_to.path, '/accounts/melt3d/session/')
        self.assertEqual(parse_qs(return_to.query)['state'], [query['state']])
        self.assertEqual(self.client.session[melt_auth.SESSION_PENDING], {query['state']: '/printers/'})

    def test_signup_asks_for_signup(self):
        self.assertEqual(self.start('/accounts/signup/')['mode'], 'signup')

    def test_next_on_another_site_becomes_home(self):
        query = self.start('/accounts/login/?next=https://evil.example/')
        self.assertEqual(self.client.session[melt_auth.SESSION_PENDING][query['state']], '/')

    @override_settings(SITE_USES_HTTPS=True)
    def test_return_to_is_https_behind_a_proxy(self):
        self.assertTrue(self.start()['return_to'].startswith('https://testserver/'))

    def test_keeps_only_a_few_pending_sign_ins(self):
        for _ in range(melt_auth.MAX_PENDING + 3):
            last = self.start()
        pending = self.client.session[melt_auth.SESSION_PENDING]
        self.assertEqual(len(pending), melt_auth.MAX_PENDING)
        self.assertIn(last['state'], pending)

    # --- Coming back -----------------------------------------------------------

    def test_a_ticket_signs_in_and_returns_to_next(self):
        query = self.start()
        resp, post = self.come_back(query, ticket='the-ticket')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/printers/')

        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], f'{AUTH}/api/handoff/redeem')
        self.assertEqual(post.call_args.kwargs['json'], {'ticket': 'the-ticket', 'state': query['state']})
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], f'Bearer {SECRET}')

        user = User.objects.get(email='maker@example.com', syndicate=self.syndicate)
        self.assertEqual(self.signed_in_user_id(), str(user.pk))
        self.assertEqual(self.client.session[melt_auth.SESSION_TOKEN], 'app-token')
        self.assertEqual(self.client.session[melt_auth.SESSION_PENDING], {})

    def test_signs_in_the_existing_user_with_that_email(self):
        user = User.objects.create_user(email='Maker@Example.com', password=None, syndicate=self.syndicate, username='maker')
        self.sign_in()
        self.assertEqual(self.signed_in_user_id(), str(user.pk))

    def test_two_tabs_both_sign_in(self):
        first, second = self.start(), self.start('/accounts/login/?next=/stats/')
        resp, _ = self.come_back(first)
        self.assertEqual(resp['Location'], '/printers/')
        # Signing in keeps the other tab's pending sign-in.
        resp, post = self.come_back(second)
        self.assertEqual(resp['Location'], '/stats/')
        post.assert_called_once()

    def test_a_state_this_browser_did_not_start_is_refused(self):
        query = self.start()
        query['return_to'] = query['return_to'].replace(query['state'], 'x' * 43)
        resp, post = self.come_back(query)
        self.assertEqual(resp.status_code, 400)
        post.assert_not_called()
        self.assertIsNone(self.signed_in_user_id())

    def test_a_state_is_used_once(self):
        query = self.start()
        self.come_back(query)
        self.client.logout()
        resp, post = self.come_back(query)
        self.assertEqual(resp.status_code, 400)
        post.assert_not_called()

    def test_an_expired_ticket_asks_to_sign_in_again(self):
        resp, _ = self.come_back(self.start(), redeemed=answer(401, {'signedIn': False}))
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(self.signed_in_user_id())

    def test_a_refused_secret_is_unavailable(self):
        resp, _ = self.come_back(self.start(), redeemed=answer(403, {'message': 'Forbidden.'}))
        self.assertEqual(resp.status_code, 503)

    def test_an_unreachable_auth_server_is_unavailable(self):
        query = self.start()
        return_to = urlparse(query['return_to'])
        with patch('app.melt_auth.requests.post', side_effect=requests.ConnectionError('down')):
            resp = self.client.get(f'{return_to.path}?{return_to.query}&ticket=t')
        self.assertEqual(resp.status_code, 503)

    def test_the_waitlist_goes_to_no_access(self):
        resp, _ = self.come_back(self.start(), redeemed=answer(200, access(has_access=False)))
        self.assertEqual(resp.status_code, 302)
        location = urlparse(resp['Location'])
        self.assertEqual(f'{location.scheme}://{location.netloc}{location.path}', f'{AUTH}/no-access')
        self.assertEqual(parse_qs(location.query)['redirect_url'], ['http://testserver/printers/'])
        self.assertIsNone(self.signed_in_user_id())

    def test_an_inactive_user_is_refused(self):
        User.objects.create_user(email='maker@example.com', password=None, syndicate=self.syndicate,
                                 username='maker', is_active=False)
        resp, _ = self.come_back(self.start())
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(self.signed_in_user_id())

    def test_signed_in_already_skips_the_trip(self):
        self.sign_in()
        resp = self.client.get('/accounts/login/?next=/stats/')
        self.assertEqual(resp['Location'], '/stats/')

    # --- Rechecks --------------------------------------------------------------

    def age_session(self):
        session = self.client.session
        session[melt_auth.SESSION_CHECKED_AT] -= 901
        session.save()

    def recheck(self, **mock):
        with patch('app.melt_auth.requests.get', **mock) as get:
            self.client.get('/accounts/login/')
        return get

    def test_no_recheck_before_its_time(self):
        self.sign_in()
        get = self.recheck(return_value=answer(401))
        get.assert_not_called()
        self.assertIsNotNone(self.signed_in_user_id())

    def test_recheck_asks_with_the_token(self):
        self.sign_in()
        self.age_session()
        get = self.recheck(return_value=answer(200, access()))
        self.assertEqual(get.call_args.args[0], f'{AUTH}/api/access')
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer app-token')
        self.assertIsNotNone(self.signed_in_user_id())

    def test_recheck_signs_out_an_ended_session(self):
        self.sign_in()
        self.age_session()
        self.recheck(return_value=answer(401, {'signedIn': False}))
        self.assertIsNone(self.signed_in_user_id())

    def test_recheck_signs_out_lost_access(self):
        self.sign_in()
        self.age_session()
        self.recheck(return_value=answer(200, access(has_access=False)))
        self.assertIsNone(self.signed_in_user_id())

    def test_recheck_signs_out_another_account(self):
        self.sign_in()
        self.age_session()
        self.recheck(return_value=answer(200, access(email='someone@example.com')))
        self.assertIsNone(self.signed_in_user_id())

    def test_recheck_keeps_the_session_when_the_auth_server_is_down(self):
        self.sign_in()
        self.age_session()
        self.recheck(side_effect=requests.ConnectionError('down'))
        self.assertIsNotNone(self.signed_in_user_id())

    # --- Signing out -----------------------------------------------------------

    def test_logout_ends_the_melt3d_session_too(self):
        self.sign_in()
        resp = self.client.get('/accounts/logout/')
        self.assertEqual(resp['Location'], f'{AUTH}/logout')
        self.assertIsNone(self.signed_in_user_id())
