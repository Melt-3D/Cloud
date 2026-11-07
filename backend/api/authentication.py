from urllib.parse import urlparse

from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import WebsocketDenier

from app.models import Printer


class PrinterAuthentication(TokenAuthentication):
    def authenticate_credentials(self, key, request=None):
        try:
            printer = Printer.objects.select_related('user').get(auth_token=key)
        except ObjectDoesNotExist:
            raise AuthenticationFailed({'error': 'Invalid or Inactive Token', 'is_authenticated': False})

        return printer.user, printer


def TokenAuthMiddlewareStack(inner): return AuthMiddlewareStack(inner)


# Websocket routes that sign the browser in with its session cookie. The token,
# share_token and agent routes authenticate with their own token instead.
SESSION_WEBSOCKET_PREFIXES = ('/ws/web/', '/ws/janus/')


def websocket_origin_allowed(scope):
    headers = dict(scope.get('headers', []))
    origin = headers.get(b'origin', b'').decode('latin1')
    if not origin:
        return False  # Browsers always send Origin on websocket handshakes
    if origin in settings.CSRF_TRUSTED_ORIGINS:
        return True
    return urlparse(origin).netloc == headers.get(b'host', b'').decode('latin1')


class SessionWebsocketOriginValidator:
    """
    The websocket counterpart of Django's CSRF origin check: refuses session
    websockets opened by a page from another origin, which the browser would
    otherwise send the session cookie with (e.g. any other *.melt3d.eu site).
    """

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if (
            scope['type'] == 'websocket' and
            scope['path'].startswith(SESSION_WEBSOCKET_PREFIXES) and
            not websocket_origin_allowed(scope)
        ):
            denier = WebsocketDenier()
            return await denier(scope, receive, send)
        return await self.application(scope, receive, send)
