# Sign-in through the Melt3D account server (auth.melt3d.eu, on Auth0).
#
# The auth server's session cookie is set for all of melt3d.eu. The middleware
# forwards it to /api/access and logs the matching Obico user in with a normal
# Django session, re-checked every MELT_AUTH_RECHECK_SECONDS. Obico's origin
# must be in the auth server's TRUSTED_ORIGINS. app/urls.py routes the views
# only when MELT_AUTH_URL is set.

import hashlib
import logging
import time
from urllib.parse import urlencode

import requests
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from app.models import User
from lib.syndicate import syndicate_from_request
from lib.tunnelv2 import OctoprintTunnelV2Helper

LOGGER = logging.getLogger(__name__)

# 'melt3d_session' when the auth server runs on plain http (local dev).
DEFAULT_SESSION_COOKIE = '__Secure-melt3d_session'

# Requests that never come from a signed-in browser.
SKIP_PATH_PREFIXES = (
    '/api/v1/octo/',
    '/static/',
    '/media/',
    '/hc/',
    '/ws/',
    '/o/token/',
)

DENIED_CACHE_SECONDS = 60

SESSION_CHECKED_AT = 'melt_auth_checked_at'
SESSION_EMAIL = 'melt_auth_email'

# Why a browser with a Melt3D cookie isn't signed in (request.melt_auth_state).
# The login views show it instead of looping through Auth0.
SIGNED_OUT = 'signed_out'
WAITLIST = 'waitlist'
INACTIVE = 'inactive'
UNAVAILABLE = 'unavailable'


class MeltAuthUnavailable(Exception):
    """The auth server could not be asked. Not an answer either way."""


def auth_url():
    return (getattr(settings, 'MELT_AUTH_URL', '') or '').rstrip('/')


def session_cookie_name():
    return getattr(settings, 'MELT_AUTH_SESSION_COOKIE', '') or DEFAULT_SESSION_COOKIE


def recheck_seconds():
    return getattr(settings, 'MELT_AUTH_RECHECK_SECONDS', 15 * 60)


def _denied_cache_key(cookie_value):
    return 'melt_auth:denied:' + hashlib.sha256(cookie_value.encode()).hexdigest()


def melt_access(request):
    """
    The auth server's /api/access answer for this browser, or None when it is
    signed out. Raises MeltAuthUnavailable when the auth server can't be asked.
    """
    base = auth_url()
    cookie_name = session_cookie_name()
    cookie_value = request.COOKIES.get(cookie_name)
    if not base or not cookie_value:
        return None

    try:
        # Only the Melt3D cookie, never Obico's own session.
        resp = requests.get(
            f'{base}/api/access',
            headers={'Cookie': f'{cookie_name}={cookie_value}', 'Accept': 'application/json'},
            timeout=5,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        raise MeltAuthUnavailable(str(e))

    if resp.status_code == 401:
        return None
    if resp.status_code != 200:
        raise MeltAuthUnavailable(f'{base}/api/access answered {resp.status_code}')

    try:
        access = resp.json()
    except ValueError:
        raise MeltAuthUnavailable(f'{base}/api/access answered no JSON')
    if not isinstance(access, dict):
        raise MeltAuthUnavailable(f'{base}/api/access answered {access!r}')
    return access


def has_access(access):
    # The auth server decides who is on the access list, and signs in only
    # confirmed addresses.
    return bool(
        access and
        access.get('signedIn') is True and
        access.get('hasAccess') is True and
        (access.get('user') or {}).get('email')
    )


def user_for(melt_user, syndicate):
    """The Obico user for this Melt3D account, matched by email, created on first sign-in."""
    email = melt_user['email'].strip().lower()
    user = (
        User.objects.filter(emailaddress__email__iexact=email, syndicate=syndicate).first() or
        User.objects.filter(email__iexact=email, syndicate=syndicate).first()
    )
    if not user:
        user = User.objects.create_user(
            email=email,
            password=None,
            syndicate=syndicate,
            username=f'{email}_{syndicate.id}',
            first_name=(melt_user.get('name') or '')[:150],
        )

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
    if not cookie_value:
        return
    denied = cache.get(_denied_cache_key(cookie_value))
    if denied:
        request.melt_auth_state = denied
        return

    try:
        access = melt_access(request)
    except MeltAuthUnavailable as e:
        LOGGER.warning('Melt3D auth: auth server unavailable: %s', e)
        request.melt_auth_state = UNAVAILABLE
        return

    if not has_access(access):
        denied = WAITLIST if access and access.get('signedIn') is True else SIGNED_OUT
        cache.set(_denied_cache_key(cookie_value), denied, DENIED_CACHE_SECONDS)
        request.melt_auth_state = denied
        return

    user = user_for(access['user'], syndicate_from_request(request))
    if not user.is_active:
        cache.set(_denied_cache_key(cookie_value), INACTIVE, DENIED_CACHE_SECONDS)
        request.melt_auth_state = INACTIVE
        return

    auth_login(request, user, backend='app.accounts.SyndicateSpecificBackend')
    request.session[SESSION_EMAIL] = access['user']['email'].strip().lower()
    request.session[SESSION_CHECKED_AT] = int(time.time())


def _recheck(request):
    # Only sessions created here. An unreachable auth server signs nobody out.
    checked_at = request.session.get(SESSION_CHECKED_AT)
    if checked_at is None or time.time() - checked_at < recheck_seconds():
        return

    try:
        access = melt_access(request)
    except MeltAuthUnavailable as e:
        LOGGER.warning('Melt3D auth: auth server unavailable, keeping session: %s', e)
        return

    if (
        has_access(access) and
        access['user']['email'].strip().lower() == request.session.get(SESSION_EMAIL)
    ):
        request.session[SESSION_CHECKED_AT] = int(time.time())
        return

    auth_logout(request)
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
    next_url = request.GET.get('next') or default
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = default
    url = request.build_absolute_uri(next_url)
    # Behind a TLS-terminating proxy Django sees http; the portal only returns to https.
    if settings.SITE_USES_HTTPS and url.startswith('http://'):
        url = 'https://' + url[len('http://'):]
    return url


def _portal(path, **params):
    query = f'?{urlencode(params)}' if params else ''
    return f'{auth_url()}{path}{query}'


def _not_signed_in(request, default_next):
    """The reason page for a Melt3D sign-in that didn't sign in here, or None."""
    state = getattr(request, 'melt_auth_state', None)
    if state == WAITLIST:
        return redirect(_portal('/no-access', redirect_url=_absolute_next(request, default_next)))
    if state == INACTIVE:
        return render(request, 'account/account_inactive.html', status=403)
    if state == UNAVAILABLE:
        return HttpResponse('Melt3D accounts are unavailable for a moment. Try again shortly.', status=503)
    return None


def login_redirect(request, default_next='/'):
    if request.user.is_authenticated:
        return redirect(_absolute_next(request, default_next))
    return _not_signed_in(request, default_next) or \
        redirect(_portal('/login', redirect_url=_absolute_next(request, default_next)))


def signup_redirect(request, default_next='/'):
    if request.user.is_authenticated:
        return redirect(_absolute_next(request, default_next))
    return _not_signed_in(request, default_next) or \
        redirect(_portal('/signup', redirect_url=_absolute_next(request, default_next)))


def mobile_login_redirect(request):
    return login_redirect(request, default_next='/mobile/auth/fetch/')


def mobile_signup_redirect(request):
    return signup_redirect(request, default_next='/mobile/auth/fetch/')


def logout_redirect(request):
    # Must end the Melt3D session too, or the middleware signs straight back in.
    auth_logout(request)
    return redirect(_portal('/logout'))


def password_redirect(request, *args, **kwargs):
    # Auth0's Universal Login resets passwords.
    return redirect(_portal('/login'))


def account_redirect(request, *args, **kwargs):
    return redirect(_portal('/'))
