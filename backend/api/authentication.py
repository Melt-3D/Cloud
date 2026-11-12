import logging

from rest_framework.authentication import BaseAuthentication, SessionAuthentication, BasicAuthentication, get_authorization_header
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import APIException, AuthenticationFailed
from channels.auth import AuthMiddlewareStack

from app import melt_auth
from app.models import Printer
from lib.syndicate import syndicate_from_request

LOGGER = logging.getLogger(__name__)


class PrinterAuthentication(TokenAuthentication):
    def authenticate_credentials(self, key, request=None):
        try:
            printer = Printer.objects.select_related('user').get(auth_token=key)
        except ObjectDoesNotExist:
            raise AuthenticationFailed({'error': 'Invalid or Inactive Token', 'is_authenticated': False})

        return printer.user, printer


def TokenAuthMiddlewareStack(inner): return AuthMiddlewareStack(inner)


class CsrfExemptSessionAuthentication(SessionAuthentication):

    def enforce_csrf(self, request):
        return  # To not perform the csrf check previously happening


class MeltTokenAuthentication(BaseAuthentication):
    """
    A Melt3D account's access token (`Authorization: Bearer …`), as the Melt3D
    Slicer sends it with G-code to print. The auth server (MELT_AUTH_URL) says
    whose it is, and the Obico user with that email is the one signed in, made
    on first use as a browser sign-in makes it (app/melt_auth.py). No session
    and no CSRF: the token is never sent by a browser on its own.
    """

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header or header[0].lower() != b'bearer' or not melt_auth.auth_url():
            return None
        if len(header) != 2:
            raise AuthenticationFailed('Invalid Authorization header')

        try:
            access = melt_auth.melt_access(header[1].decode('ascii'))
        except UnicodeDecodeError:
            raise AuthenticationFailed('Invalid Authorization header')
        except melt_auth.MeltAuthUnavailable as e:
            LOGGER.warning('Melt3D auth: auth server unavailable for a token: %s', e)
            raise MeltAuthUnavailableError()

        if not melt_auth.has_access(access):
            raise AuthenticationFailed('This Melt3D account has no access to Melt Cloud')
        return melt_auth.user_for(access['user'], syndicate_from_request(request)), None

    def authenticate_header(self, request):
        return 'Bearer'


class MeltAuthUnavailableError(APIException):
    status_code = 503
    default_detail = 'Melt3D accounts could not be reached. Try again in a moment.'
    default_code = 'melt_auth_unavailable'
