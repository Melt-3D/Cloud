# Sign-in through the Melt3D account server (auth.melt3d.eu, on Auth0).
#
# The auth server's session cookie is for auth.melt3d.eu alone, so it hands
# the session over (its worker/handoff.ts):
#
#   1. /accounts/login/ (or signup/) keeps a random state in the Django session
#      and sends the browser to MELT_AUTH_URL/handoff. Already signed in there,
#      the browser comes straight back; otherwise Auth0 signs them in first.
#   2. /accounts/melt3d/session/ gets a one-minute ticket, checks the state,
#      and trades the ticket at /api/handoff/redeem with
#      MELT_AUTH_HANDOFF_SECRET for the account's access and a token.
#   3. The Obico user with that email is logged in with a normal Django
#      session. Every MELT_AUTH_RECHECK_SECONDS the middleware asks
#      /api/access with the token; signing out anywhere, losing access, or the
#      token running out (a day) logs them out here, and the next sign-in is
#      another trip through step 1, without a password while Auth0's session lasts.
#
# app/urls.py routes the views only when MELT_AUTH_URL is set. Obico's origin
# must be in the auth server's HANDOFF_ORIGINS and TRUSTED_ORIGINS.

import logging
import secrets
import time
from urllib.parse import urlencode

import requests
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from app.models import User
from lib.syndicate import syndicate_from_request
from lib.tunnelv2 import OctoprintTunnelV2Helper

LOGGER = logging.getLogger(__name__)

# Requests that never come from a signed-in browser.
SKIP_PATH_PREFIXES = (
    '/api/v1/octo/',
    '/static/',
    '/media/',
    '/hc/',
    '/ws/',
    '/o/token/',
)

SESSION_CHECKED_AT = 'melt_auth_checked_at'
SESSION_EMAIL = 'melt_auth_email'
SESSION_TOKEN = 'melt_auth_token'
# Sign-ins on their way through the auth server: {state: next URL}. A few, so
# that two tabs signing in at once both come back signed in.
SESSION_PENDING = 'melt_auth_pending'
MAX_PENDING = 5


class MeltAuthUnavailable(Exception):
    """The auth server could not be asked. Not an answer either way."""


def auth_url():
    return (getattr(settings, 'MELT_AUTH_URL', '') or '').rstrip('/')


def handoff_secret():
    return getattr(settings, 'MELT_AUTH_HANDOFF_SECRET', '') or ''


def recheck_seconds():
    return getattr(settings, 'MELT_AUTH_RECHECK_SECONDS', 15 * 60)


def _answer(resp, what):
    """The auth server's JSON answer, None on 401, MeltAuthUnavailable for anything else."""
    if resp.status_code == 401:
        return None
    if resp.status_code != 200:
        raise MeltAuthUnavailable(f'{what} answered {resp.status_code}')
    try:
        answer = resp.json()
    except ValueError:
        raise MeltAuthUnavailable(f'{what} answered no JSON')
    if not isinstance(answer, dict):
        raise MeltAuthUnavailable(f'{what} answered {answer!r}')
    return answer


def redeem(ticket, state):
    """
    The auth server's answer for a ticket from /handoff: /api/access's, plus
    `token`. None when the ticket is no good (expired, or not for this state).
    """
    url = f'{auth_url()}/api/handoff/redeem'
    try:
        resp = requests.post(
            url,
            json={'ticket': ticket, 'state': state},
            headers={'Authorization': f'Bearer {handoff_secret()}', 'Accept': 'application/json'},
            timeout=5,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        raise MeltAuthUnavailable(str(e))
    if resp.status_code == 403:
        raise MeltAuthUnavailable(f'{url} refused MELT_AUTH_HANDOFF_SECRET')
    return _answer(resp, url)


def melt_access(token):
    """
    The auth server's /api/access answer for this token, or None when its
    session has ended. Raises MeltAuthUnavailable when it can't be asked.
    """
    url = f'{auth_url()}/api/access'
    try:
        resp = requests.get(
            url,
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
            timeout=5,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        raise MeltAuthUnavailable(str(e))
    return _answer(resp, url)


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


# --- The middleware: re-checks sessions made here ---------------------------


def _should_skip(request):
    return (
        not auth_url() or
        request.path.startswith(SKIP_PATH_PREFIXES) or
        OctoprintTunnelV2Helper.is_tunnel_request(request)
    )


def _recheck(request):
    # Only sessions created here. An unreachable auth server signs nobody out.
    checked_at = request.session.get(SESSION_CHECKED_AT)
    if checked_at is None or time.time() - checked_at < recheck_seconds():
        return

    token = request.session.get(SESSION_TOKEN)
    try:
        access = melt_access(token) if token else None
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


def melt_auth(get_response):

    def middleware(request):
        if not _should_skip(request) and request.user.is_authenticated:
            _recheck(request)
        return get_response(request)

    return middleware


# --- The views ----------------------------------------------------------------


def absolute_url(request, url):
    url = request.build_absolute_uri(url)
    # Behind a TLS-terminating proxy Django sees http; the auth server only returns to https.
    if settings.SITE_USES_HTTPS and url.startswith('http://'):
        url = 'https://' + url[len('http://'):]
    return url


def _safe_next(request, default='/'):
    next_url = request.GET.get('next') or default
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = default
    return next_url


def _portal(path, **params):
    query = f'?{urlencode(params)}' if params else ''
    return f'{auth_url()}{path}{query}'


def _start_handoff(request, mode, default_next):
    """Off to the auth server for its session, signing in (or up) there first if need be."""
    next_url = _safe_next(request, default_next)
    if request.user.is_authenticated:
        return redirect(next_url)

    state = secrets.token_urlsafe(32)
    pending = dict(list(request.session.get(SESSION_PENDING, {}).items())[-(MAX_PENDING - 1):])
    pending[state] = next_url
    request.session[SESSION_PENDING] = pending

    # The state travels in return_to too, to pick this sign-in out of the pending ones.
    return_to = absolute_url(request, reverse('melt_auth_session') + '?' + urlencode({'state': state}))
    return redirect(_portal('/handoff', return_to=return_to, state=state, mode=mode))


def _try_again(message, status=400):
    return HttpResponse(f'{message} <a href="/accounts/login/">Sign in again</a>.', status=status)


def handoff_return(request):
    """GET /accounts/melt3d/session/?state=…&ticket=…: the auth server's answer to /handoff."""
    state = request.GET.get('state', '')
    pending = request.session.get(SESSION_PENDING, {})
    next_url = pending.pop(state, None) if state else None
    request.session[SESSION_PENDING] = pending
    ticket = request.GET.get('ticket')
    if next_url is None or not ticket:
        # Not this browser's sign-in (an old tab, a forwarded link, cookies off).
        return _try_again('This sign-in has expired or was started elsewhere.')

    try:
        access = redeem(ticket, state)
    except MeltAuthUnavailable as e:
        LOGGER.warning('Melt3D auth: could not redeem a ticket: %s', e)
        return HttpResponse('Melt3D accounts are unavailable for a moment. Try again shortly.', status=503)
    if access is None:
        return _try_again('This sign-in has expired.')

    if not has_access(access):
        if access.get('signedIn') is True:
            return redirect(_portal('/no-access', redirect_url=absolute_url(request, next_url)))
        return _try_again('This sign-in did not go through.')

    user = user_for(access['user'], syndicate_from_request(request))
    if not user.is_active:
        return render(request, 'account/account_inactive.html', status=403)

    auth_login(request, user, backend='app.accounts.SyndicateSpecificBackend')
    request.session[SESSION_EMAIL] = access['user']['email'].strip().lower()
    request.session[SESSION_TOKEN] = access['token']
    request.session[SESSION_CHECKED_AT] = int(time.time())
    return redirect(next_url)


def login_redirect(request, default_next='/'):
    return _start_handoff(request, 'login', default_next)


def signup_redirect(request, default_next='/'):
    return _start_handoff(request, 'signup', default_next)


def mobile_login_redirect(request):
    return login_redirect(request, default_next='/mobile/auth/fetch/')


def mobile_signup_redirect(request):
    return signup_redirect(request, default_next='/mobile/auth/fetch/')


def logout_redirect(request):
    # Ends the Melt3D session too: otherwise the next sign-in here would come
    # straight back signed in, and other apps would stay signed in.
    auth_logout(request)
    return redirect(_portal('/logout'))


def password_redirect(request, *args, **kwargs):
    # Auth0's Universal Login resets passwords.
    return redirect(_portal('/login'))


def account_redirect(request, *args, **kwargs):
    return redirect(_portal('/'))
