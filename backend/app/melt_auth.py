# Sign-in through the Melt3D account server (auth.melt3d.eu, Better Auth).
#
# Obico runs on a melt3d.eu subdomain, so the browser sends it the Better Auth
# session cookie, which is set for all of melt3d.eu. The middleware trades that
# cookie, server to server, for a short-lived EdDSA JWT at /api/auth/token,
# verifies it against /api/auth/jwks, and logs the matching Obico user in with
# an ordinary Django session. From then on Obico works as before: web UI,
# REST API, websockets, tunnels and its own OAuth clients all see that session.
#
# Sessions created here are re-checked every MELT_AUTH_RECHECK_SECONDS, so
# signing out of Melt3D, or leaving MELT_ACCESS_ORG, also ends the Obico
# session. Sessions created any other way (Django admin's password login,
# hijack) are left alone.
#
# The views send Obico's own account pages (login, signup, logout, password,
# email) to the portal instead. app/urls.py should route them only when
# MELT_AUTH_URL is set; without it Obico keeps its stock allauth sign-in.

import hashlib
import logging
import time
from urllib.parse import urlencode

import jwt
import requests
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.core.cache import cache
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from app.models import User
from lib.syndicate import syndicate_from_request
from lib.tunnelv2 import OctoprintTunnelV2Helper

LOGGER = logging.getLogger(__name__)

# Better Auth's session cookie on an https base URL. Plain-http deployments
# (local dev) call it 'better-auth.session_token'.
DEFAULT_SESSION_COOKIE = '__Secure-better-auth.session_token'

# Requests that never come from a signed-in browser: printer agents, static
# files, health checks, websockets, OAuth token exchanges by the slicer plugins.
SKIP_PATH_PREFIXES = (
    '/api/v1/octo/',
    '/static/',
    '/media/',
    '/hc/',
    '/ws/',
    '/o/token/',
)

# How long a cookie that got no access is remembered, so a waitlisted user
# browsing Obico doesn't cost a round trip to the auth server per request.
DENIED_CACHE_SECONDS = 60

SESSION_CHECKED_AT = 'melt_auth_checked_at'
SESSION_SUBJECT = 'melt_auth_sub'


class MeltAuthUnavailable(Exception):
    """The auth server could not be asked. Not an answer either way."""


def auth_url():
    return (getattr(settings, 'MELT_AUTH_URL', '') or '').rstrip('/')


def session_cookie_name():
    return getattr(settings, 'MELT_AUTH_SESSION_COOKIE', '') or DEFAULT_SESSION_COOKIE


def recheck_seconds():
    return getattr(settings, 'MELT_AUTH_RECHECK_SECONDS', 15 * 60)


_jwks_clients = {}


def _jwks_client(issuer):
    # PyJWKClient caches the key set and fetches it again for an unknown kid,
    # so a key rotation on the auth server needs nothing here.
    if issuer not in _jwks_clients:
        _jwks_clients[issuer] = jwt.PyJWKClient(f'{issuer}/api/auth/jwks')
    return _jwks_clients[issuer]


def _denied_cache_key(cookie_value):
    return 'melt_auth:denied:' + hashlib.sha256(cookie_value.encode()).hexdigest()


def melt_claims(request):
    """
    The verified JWT claims of the Melt3D account this browser is signed in
    to, or None when it is signed in to none. Raises MeltAuthUnavailable when
    the auth server can't be reached.
    """
    issuer = auth_url()
    cookie_name = session_cookie_name()
    cookie_value = request.COOKIES.get(cookie_name)
    if not issuer or not cookie_value:
        return None

    try:
        # Only the Better Auth cookie is sent, never Obico's own session.
        # Passed as a raw header: the value is already URL-encoded.
        resp = requests.get(
            f'{issuer}/api/auth/token',
            headers={'Cookie': f'{cookie_name}={cookie_value}', 'Accept': 'application/json'},
            timeout=5,
        )
    except requests.RequestException as e:
        raise MeltAuthUnavailable(str(e))

    if resp.status_code in (401, 403):
        return None
    if resp.status_code != 200:
        raise MeltAuthUnavailable(f'{issuer}/api/auth/token answered {resp.status_code}')

    try:
        token = resp.json()['token']
        signing_key = _jwks_client(issuer).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=['EdDSA'],
            issuer=issuer,
            audience=issuer,
            leeway=30,
            options={'require': ['exp', 'iss', 'aud', 'sub']},
        )
    except jwt.PyJWKClientConnectionError as e:
        raise MeltAuthUnavailable(str(e))
    except (ValueError, KeyError, jwt.PyJWKClientError, jwt.InvalidTokenError) as e:
        LOGGER.warning('Melt3D auth: rejected token: %s', e)
        return None


def has_access(claims):
    """A confirmed address, and membership of MELT_ACCESS_ORG when it is set."""
    if not claims or claims.get('emailVerified') is not True or not claims.get('email'):
        return False
    access_org = getattr(settings, 'MELT_ACCESS_ORG', '')
    return not access_org or access_org in (claims.get('orgs') or [])


def user_for(claims, syndicate):
    """
    The Obico user for this Melt3D account, created on first sign-in. Matched
    by email within the syndicate, as SocialAccountAdapter.pre_social_login
    matches social logins, so existing Obico accounts carry over.
    """
    email = claims['email'].strip().lower()
    user = (
        User.objects.filter(emailaddress__email__iexact=email, syndicate=syndicate).first() or
        User.objects.filter(email__iexact=email, syndicate=syndicate).first()
    )
    if not user:
        # No password: the account signs in only through Melt3D (or a reset
        # by an admin).
        user = User.objects.create_user(
            email=email,
            password=None,
            syndicate=syndicate,
            username=f'{email}_{syndicate.id}',
            first_name=(claims.get('name') or '')[:150],
        )

    # Better Auth only issues tokens for confirmed addresses (has_access checks).
    address, _ = EmailAddress.objects.get_or_create(
        user=user,
        email=email,
        defaults={'primary': not EmailAddress.objects.filter(user=user, primary=True).exists(), 'verified': True},
    )
    if not address.verified:
        address.verified = True
        address.save(update_fields=['verified'])
    return user


def _should_skip(request):
    return (
        not auth_url() or
        request.path.startswith(SKIP_PATH_PREFIXES) or
        OctoprintTunnelV2Helper.is_tunnel_request(request)
    )


def _try_login(request):
    cookie_value = request.COOKIES.get(session_cookie_name())
    if not cookie_value or cache.get(_denied_cache_key(cookie_value)):
        return

    try:
        claims = melt_claims(request)
    except MeltAuthUnavailable as e:
        LOGGER.warning('Melt3D auth: auth server unavailable: %s', e)
        return

    if not has_access(claims):
        cache.set(_denied_cache_key(cookie_value), True, DENIED_CACHE_SECONDS)
        return

    user = user_for(claims, syndicate_from_request(request))
    if not user.is_active:
        cache.set(_denied_cache_key(cookie_value), True, DENIED_CACHE_SECONDS)
        return

    auth_login(request, user, backend='app.accounts.SyndicateSpecificBackend')
    request.session[SESSION_SUBJECT] = claims['sub']
    request.session[SESSION_CHECKED_AT] = int(time.time())


def _recheck(request):
    """
    Ends a session this module created once its Melt3D sign-in is gone: signed
    out, removed from the access organization, or another account signed in.
    An unreachable auth server is not a reason to sign anyone out.
    """
    checked_at = request.session.get(SESSION_CHECKED_AT)
    if checked_at is None or time.time() - checked_at < recheck_seconds():
        return

    try:
        claims = melt_claims(request)
    except MeltAuthUnavailable as e:
        LOGGER.warning('Melt3D auth: auth server unavailable, keeping session: %s', e)
        return

    if has_access(claims) and claims['sub'] == request.session.get(SESSION_SUBJECT):
        request.session[SESSION_CHECKED_AT] = int(time.time())
        return

    auth_logout(request)
    # Another account that may use Obico signs in at once.
    _try_login(request)


def melt_auth(get_response):

    def middleware(request):
        if not _should_skip(request):
            if request.user.is_authenticated:
                _recheck(request)
            else:
                _try_login(request)

        return get_response(request)

    return middleware


def _absolute_next(request, default='/'):
    """Obico's ?next=, kept on this site, as the absolute URL the portal returns to."""
    next_url = request.GET.get('next') or default
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = default
    return request.build_absolute_uri(next_url)


def _portal(path, **params):
    query = f'?{urlencode(params)}' if params else ''
    return f'{auth_url()}{path}{query}'


def login_redirect(request, default_next='/'):
    if request.user.is_authenticated:
        return redirect(_absolute_next(request, default_next))
    return redirect(_portal('/sign-in', redirect_url=_absolute_next(request, default_next)))


def signup_redirect(request, default_next='/'):
    if request.user.is_authenticated:
        return redirect(_absolute_next(request, default_next))
    return redirect(_portal('/sign-up', redirect_url=_absolute_next(request, default_next)))


def mobile_login_redirect(request):
    return login_redirect(request, default_next='/mobile/auth/fetch/')


def mobile_signup_redirect(request):
    return signup_redirect(request, default_next='/mobile/auth/fetch/')


def logout_redirect(request):
    auth_logout(request)
    return redirect(_portal('/sign-out', redirect_url=_absolute_next(request)))


def password_redirect(request, *args, **kwargs):
    return redirect(_portal('/forgot-password'))


def account_redirect(request, *args, **kwargs):
    return redirect(_portal('/'))
