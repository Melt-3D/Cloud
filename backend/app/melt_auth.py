# Sign-in through the Melt3D account server (auth.melt3d.eu, on Auth0).
#
# The auth server's session cookie is set for all of melt3d.eu, so any
# *.melt3d.eu site can set it too. The login page forwards it to /api/access and
# shows whose account it is ("Continue as …?"); only that page's CSRF-protected
# POST logs the Obico user in, with a normal Django session. The middleware
# re-checks sessions every MELT_AUTH_RECHECK_SECONDS and signs out when the
# account changed or lost access, but never signs anyone in. While the auth
# server is unreachable, a session lasts MELT_AUTH_GRACE_SECONDS past its last
# successful check. Obico's origin must be in the auth server's
# TRUSTED_ORIGINS. app/urls.py routes the views only when MELT_AUTH_URL is set.

import hashlib
import logging
import re
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

# A cookie value as RFC 6265 allows it (printable ASCII except space, ", comma, ; and \).
# Anything else isn't a Melt3D session and is never forwarded to the auth server.
COOKIE_VALUE_RE = re.compile(r'[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]{1,4096}')

SESSION_CHECKED_AT = 'melt_auth_checked_at'
SESSION_EMAIL = 'melt_auth_email'

# Why a browser with a Melt3D cookie isn't signed in (request.melt_auth_state).
# The login views show it instead of looping through Auth0.
SIGNED_OUT = 'signed_out'
WAITLIST = 'waitlist'
INACTIVE = 'inactive'
PENDING = 'pending'  # inactive and never signed in: waiting for an admin's approval
UNAVAILABLE = 'unavailable'


class MeltAuthUnavailable(Exception):
    """The auth server could not be asked. Not an answer either way."""


def auth_url():
    return (getattr(settings, 'MELT_AUTH_URL', '') or '').rstrip('/')


def session_cookie_name():
    return getattr(settings, 'MELT_AUTH_SESSION_COOKIE', '') or DEFAULT_SESSION_COOKIE


def recheck_seconds():
    return getattr(settings, 'MELT_AUTH_RECHECK_SECONDS', 15 * 60)


def grace_seconds():
    return getattr(settings, 'MELT_AUTH_GRACE_SECONDS', 60 * 60)


def melt_cookie(request):
    """The browser's Melt3D session cookie, or None when it is missing or malformed."""
    value = request.COOKIES.get(session_cookie_name())
    if value and COOKIE_VALUE_RE.fullmatch(value):
        return value
    return None


def _denied_cache_key(cookie_value):
    return 'melt_auth:denied:' + hashlib.sha256(cookie_value.encode()).hexdigest()


def melt_access(request):
    """
    The auth server's /api/access answer for this browser, or None when it is
    signed out. Raises MeltAuthUnavailable when the auth server can't be asked.
    """
    base = auth_url()
    cookie_name = session_cookie_name()
    cookie_value = melt_cookie(request)
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
        # New accounts wait for an admin to activate them in the Django admin
        user = User.objects.create_user(
            email=email,
            password=None,
            syndicate=syndicate,
            username=f'{email}_{syndicate.id}',
            first_name=(melt_user.get('name') or '')[:150],
            is_active=False,
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


def _signed_in_melt_user(request):
    """
    The Melt3D account this browser is signed in with and its Obico user, as
    (access, user), or None with request.melt_auth_state saying why not.
    Signs nobody in: that takes a click on the "Continue as" page.
    """
    cookie_value = melt_cookie(request)
    if not cookie_value:
        return None
    denied = cache.get(_denied_cache_key(cookie_value))
    if denied:
        request.melt_auth_state = denied
        return None

    try:
        access = melt_access(request)
    except MeltAuthUnavailable as e:
        LOGGER.warning('Melt3D auth: auth server unavailable: %s', e)
        request.melt_auth_state = UNAVAILABLE
        return None

    if not has_access(access):
        denied = WAITLIST if access and access.get('signedIn') is True else SIGNED_OUT
        cache.set(_denied_cache_key(cookie_value), denied, DENIED_CACHE_SECONDS)
        request.melt_auth_state = denied
        return None

    user = user_for(access['user'], syndicate_from_request(request))
    request.melt_auth_email = access['user']['email'].strip().lower()
    if not user.is_active:
        state = PENDING if user.last_login is None else INACTIVE
        cache.set(_denied_cache_key(cookie_value), state, DENIED_CACHE_SECONDS)
        request.melt_auth_state = state
        return None
    return access, user


def _login(request, access, user):
    auth_login(request, user, backend='app.accounts.SyndicateSpecificBackend')
    request.session[SESSION_EMAIL] = access['user']['email'].strip().lower()
    request.session[SESSION_CHECKED_AT] = int(time.time())


def _recheck(request):
    # Only sessions created here. An unreachable auth server signs nobody out
    # until the grace period after the last successful check runs out.
    checked_at = request.session.get(SESSION_CHECKED_AT)
    if checked_at is None or time.time() - checked_at < recheck_seconds():
        return

    try:
        access = melt_access(request)
    except MeltAuthUnavailable as e:
        if time.time() - checked_at < grace_seconds():
            LOGGER.warning('Melt3D auth: auth server unavailable, keeping session: %s', e)
            return
        LOGGER.warning('Melt3D auth: auth server unavailable past the grace period, signing out: %s', e)
        auth_logout(request)
        request.melt_auth_state = UNAVAILABLE
        return

    if (
        has_access(access) and
        access['user']['email'].strip().lower() == request.session.get(SESSION_EMAIL)
    ):
        request.session[SESSION_CHECKED_AT] = int(time.time())
        return

    # Signed out of Melt3D, off the access list, or the cookie is now someone else's.
    # Never switch accounts silently: the next page asks "Continue as …?" again.
    auth_logout(request)


def melt_auth(get_response):

    def middleware(request):
        # Signing in takes a click on the login page ("Continue as …?"). A Melt3D
        # cookie alone, which any *.melt3d.eu site can set, signs nobody in.
        if not _should_skip(request) and request.user.is_authenticated:
            _recheck(request)

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
    if state in (PENDING, INACTIVE):
        return render(request, 'account/account_inactive.html', {
            'pending': state == PENDING,
            'melt_email': getattr(request, 'melt_auth_email', None),
            'switch_account_url': _portal('/logout'),
        }, status=403)
    if state == UNAVAILABLE:
        return HttpResponse('Melt3D accounts are unavailable for a moment. Try again shortly.', status=503)
    return None


def _continue_as(request, default_next, portal_path):
    if request.user.is_authenticated:
        return redirect(_absolute_next(request, default_next))
    found = _signed_in_melt_user(request)
    if not found:
        return _not_signed_in(request, default_next) or \
            redirect(_portal(portal_path, redirect_url=_absolute_next(request, default_next)))

    access, user = found
    # The POST carries Django's CSRF token and Origin check, so another site can't click it
    if request.method == 'POST':
        _login(request, access, user)
        return redirect(_absolute_next(request, default_next))
    return render(request, 'account/melt_continue_as.html', {
        'melt_user': access['user'],
        'switch_account_url': _portal('/logout'),
    })


def login_redirect(request, default_next='/'):
    return _continue_as(request, default_next, '/login')


def signup_redirect(request, default_next='/'):
    return _continue_as(request, default_next, '/signup')


def mobile_login_redirect(request):
    return login_redirect(request, default_next='/mobile/auth/fetch/')


def mobile_signup_redirect(request):
    return signup_redirect(request, default_next='/mobile/auth/fetch/')


def logout_redirect(request):
    # End the Melt3D session too, so the next sign-in starts fresh.
    auth_logout(request)
    return redirect(_portal('/logout'))


def password_redirect(request, *args, **kwargs):
    # Auth0's Universal Login resets passwords.
    return redirect(_portal('/login'))


def account_redirect(request, *args, **kwargs):
    return redirect(_portal('/'))
