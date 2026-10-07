import base64
import hashlib
import logging
import secrets
from datetime import timedelta
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import jwt
import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from ninja import Schema
from pydantic import Field

from .jwt_utils import decode_jwt_token, generate_jwt_token
from .models import Profile, SSOIdentity, SSOLoginFlow, SSOLoginTicket
from .schemas import ErrorResponse, TokenResponse

logger = logging.getLogger(__name__)

LOGIN_FLOW_LIFETIME = timedelta(minutes=10)
LOGIN_TICKET_LIFETIME = timedelta(minutes=2)


class SSOTicketRequest(Schema):
    ticket: str = Field(min_length=32, max_length=128)


class SSOLoginError(Exception):
    def __init__(self, error_code):
        self.error_code = error_code


def _frontend_redirect(error_code=None, ticket=None):
    parts = urlsplit(settings.SSO_FRONTEND_URL)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.pop('sso_error', None)
    query.pop('sso_ticket', None)
    if error_code:
        query['sso_error'] = error_code
    if ticket:
        query['sso_ticket'] = ticket
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _get_provider_metadata():
    issuer = settings.SSO_ISSUER.rstrip('/')
    response = requests.get(
        f'{issuer}/.well-known/openid-configuration',
        timeout=10,
    )
    response.raise_for_status()
    metadata = response.json()
    if metadata.get('issuer', '').rstrip('/') != issuer:
        raise ValueError('The OIDC discovery issuer does not match the configured issuer.')
    for endpoint in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
        if not metadata.get(endpoint):
            raise ValueError(f'The OIDC discovery document is missing {endpoint}.')
    return metadata


def _create_login_ticket(user):
    raw_ticket = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_ticket.encode('utf-8')).hexdigest()
    with transaction.atomic():
        SSOLoginTicket.objects.filter(expires_at__lte=timezone.now()).delete()
        SSOLoginTicket.objects.create(
            token_hash=token_hash,
            user=user,
            expires_at=timezone.now() + LOGIN_TICKET_LIFETIME,
        )
    return raw_ticket


def _create_login_flow(state, nonce, verifier):
    state_hash = hashlib.sha256(state.encode('utf-8')).hexdigest()
    with transaction.atomic():
        SSOLoginFlow.objects.filter(expires_at__lte=timezone.now()).delete()
        SSOLoginFlow.objects.create(
            state_hash=state_hash,
            nonce=nonce,
            code_verifier=verifier,
            expires_at=timezone.now() + LOGIN_FLOW_LIFETIME,
        )


def _consume_login_flow(state):
    state_hash = hashlib.sha256(state.encode('utf-8')).hexdigest()
    with transaction.atomic():
        try:
            flow = SSOLoginFlow.objects.select_for_update().get(state_hash=state_hash)
        except SSOLoginFlow.DoesNotExist:
            return None

        if flow.expires_at <= timezone.now():
            flow.delete()
            return None

        nonce = flow.nonce
        verifier = flow.code_verifier
        flow.delete()
        return nonce, verifier


def _token_endpoint_auth():
    method = settings.SSO_TOKEN_AUTH_METHOD
    if method == 'none':
        return {'client_id': settings.SSO_CLIENT_ID}, {}

    if not settings.SSO_CLIENT_SECRET:
        raise ValueError('SSO_CLIENT_SECRET is required for the configured token auth method.')

    if method == 'client_secret_post':
        return {
            'client_id': settings.SSO_CLIENT_ID,
            'client_secret': settings.SSO_CLIENT_SECRET,
        }, {}

    credentials = (
        f'{quote_plus(settings.SSO_CLIENT_ID, safe="")}:'
        f'{quote_plus(settings.SSO_CLIENT_SECRET, safe="")}'
    ).encode('utf-8')
    authorization = base64.b64encode(credentials).decode('ascii')
    return {}, {'Authorization': f'Basic {authorization}'}


def _resolve_user(issuer, claims):
    if claims.get('email_verified') is not True:
        raise SSOLoginError('sso_email_not_verified')

    email = claims.get('email')
    subject = claims.get('sub')
    if not isinstance(email, str) or not email or not isinstance(subject, str) or not subject:
        raise SSOLoginError('sso_account_not_linked')

    identity = SSOIdentity.objects.select_related('user').filter(
        issuer=issuer,
        subject=subject,
    ).first()
    if identity:
        if not identity.user.is_active:
            raise SSOLoginError('sso_account_disabled')
        return identity.user

    matching_users = list(
        User.objects.filter(email__iexact=email, is_active=True).order_by('pk')[:2]
    )
    if len(matching_users) != 1:
        raise SSOLoginError('sso_account_not_linked')

    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=matching_users[0].pk)
            if not user.is_active:
                raise SSOLoginError('sso_account_disabled')
            SSOIdentity.objects.create(user=user, issuer=issuer, subject=subject)
            return user
    except IntegrityError:
        identity = SSOIdentity.objects.select_related('user').filter(
            issuer=issuer,
            subject=subject,
        ).first()
        if identity and identity.user.is_active:
            return identity.user
        raise SSOLoginError('sso_account_not_linked')


def _validate_id_token(id_token, metadata, nonce):
    signing_key = jwt.PyJWKClient(metadata['jwks_uri']).get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=['RS256'],
        audience=settings.SSO_CLIENT_ID,
        issuer=settings.SSO_ISSUER.rstrip('/'),
        options={'require': ['exp', 'iat', 'iss', 'aud', 'sub', 'nonce']},
        leeway=30,
    )
    returned_nonce = claims.get('nonce')
    if not isinstance(returned_nonce, str) or not secrets.compare_digest(returned_nonce, nonce):
        raise jwt.InvalidTokenError('OIDC nonce mismatch')
    return claims


def register_sso_endpoints(api):
    @api.get('/auth/sso/start', auth=None, tags=['Authentication'])
    def sso_login_start(request):
        if (
            not settings.SSO_CLIENT_ID
            or (
                settings.SSO_TOKEN_AUTH_METHOD in ('client_secret_basic', 'client_secret_post')
                and not settings.SSO_CLIENT_SECRET
            )
        ):
            return HttpResponseRedirect(_frontend_redirect('sso_not_configured'))

        try:
            metadata = _get_provider_metadata()
        except (requests.RequestException, ValueError) as error:
            logger.error('Unable to load OIDC provider metadata: %s', error)
            return HttpResponseRedirect(_frontend_redirect('sso_unavailable'))

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode('ascii')).digest()
        ).rstrip(b'=').decode('ascii')

        _create_login_flow(state, nonce, verifier)

        authorization_params = {
            'response_type': 'code',
            'client_id': settings.SSO_CLIENT_ID,
            'redirect_uri': settings.SSO_REDIRECT_URI,
            'scope': settings.SSO_SCOPES,
            'state': state,
            'nonce': nonce,
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
        }
        parts = urlsplit(metadata['authorization_endpoint'])
        query = parse_qsl(parts.query, keep_blank_values=True)
        query.extend(authorization_params.items())
        authorization_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
        return HttpResponseRedirect(authorization_url)

    @api.get('/auth/sso/callback', auth=None, tags=['Authentication'])
    def sso_login_callback(request, code: str = None, state: str = None, error: str = None):
        if not state:
            return HttpResponseRedirect(_frontend_redirect('sso_invalid_state'))

        login_flow = _consume_login_flow(state)
        if not login_flow:
            return HttpResponseRedirect(_frontend_redirect('sso_invalid_state'))
        nonce, verifier = login_flow

        if error:
            return HttpResponseRedirect(_frontend_redirect('sso_cancelled'))
        if not code or not nonce or not verifier:
            return HttpResponseRedirect(_frontend_redirect('sso_failed'))

        try:
            metadata = _get_provider_metadata()
            token_request_data = {
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': settings.SSO_REDIRECT_URI,
                'code_verifier': verifier,
            }
            auth_data, auth_headers = _token_endpoint_auth()
            token_request_data.update(auth_data)

            token_response = requests.post(
                metadata['token_endpoint'],
                data=token_request_data,
                headers=auth_headers,
                timeout=10,
            )
            if not token_response.ok:
                try:
                    response_data = token_response.json()
                except ValueError:
                    response_data = {}
                provider_error = (
                    response_data.get('error', 'unknown_error')
                    if isinstance(response_data, dict)
                    else 'unknown_error'
                )
                logger.warning(
                    'OIDC token exchange was rejected (HTTP %s, error=%s)',
                    token_response.status_code,
                    provider_error,
                )
                return HttpResponseRedirect(_frontend_redirect('sso_token_rejected'))

            token_data = token_response.json()
            id_token = token_data.get('id_token')
            if not isinstance(id_token, str):
                raise ValueError('The OIDC token response did not contain an ID token.')
            claims = _validate_id_token(id_token, metadata, nonce)
            user = _resolve_user(settings.SSO_ISSUER.rstrip('/'), claims)
            ticket = _create_login_ticket(user)
            return HttpResponseRedirect(_frontend_redirect(ticket=ticket))
        except SSOLoginError as error:
            return HttpResponseRedirect(_frontend_redirect(error.error_code))
        except (
            requests.RequestException,
            jwt.PyJWTError,
            ValueError,
            KeyError,
        ) as error:
            logger.warning('OIDC login callback failed: %s', error)
            return HttpResponseRedirect(_frontend_redirect('sso_failed'))

    @api.post(
        '/auth/sso/exchange',
        response={200: TokenResponse, 400: ErrorResponse, 401: ErrorResponse},
        auth=None,
        tags=['Authentication'],
    )
    def sso_login_exchange(request, data: SSOTicketRequest):
        token_hash = hashlib.sha256(data.ticket.encode('utf-8')).hexdigest()
        try:
            with transaction.atomic():
                login_ticket = SSOLoginTicket.objects.select_for_update().get(
                    token_hash=token_hash
                )
                if login_ticket.expires_at <= timezone.now():
                    login_ticket.delete()
                    return 401, {
                        'error': 'Unauthorized',
                        'detail': 'A bejelentkezési hivatkozás lejárt. Próbáld újra.',
                    }

                user = User.objects.get(pk=login_ticket.user_id)
                login_ticket.delete()
                if not user.is_active:
                    return 401, {
                        'error': 'Unauthorized',
                        'detail': 'A felhasználói fiók le van tiltva.',
                    }

                user.last_login = timezone.now()
                user.save(update_fields=['last_login'])
                profile, _ = Profile.objects.get_or_create(user=user)
                profile.login_count += 1
                profile.save(update_fields=['login_count'])
                token = generate_jwt_token(user)
                payload = decode_jwt_token(token)
        except SSOLoginTicket.DoesNotExist:
            return 400, {
                'error': 'Invalid ticket',
                'detail': 'A bejelentkezési hivatkozás érvénytelen vagy már felhasznált.',
            }
        except User.DoesNotExist:
            return 401, {
                'error': 'Unauthorized',
                'detail': 'A felhasználói fiók nem található.',
            }

        return 200, {
            'token': token,
            'user_id': user.id,
            'username': user.username,
            'iat': payload['iat'],
            'exp': payload['exp'],
        }
