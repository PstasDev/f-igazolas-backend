import base64
import hashlib
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .models import Profile, SSOIdentity, SSOLoginFlow, SSOLoginTicket


@override_settings(
    SSO_CLIENT_ID='test-client',
    SSO_CLIENT_SECRET='',
    SSO_TOKEN_AUTH_METHOD='none',
    SSO_ISSUER='https://sso.example.test/o',
    SSO_REDIRECT_URI='http://testserver/api/auth/sso/callback',
    SSO_FRONTEND_URL='http://localhost:3000/login',
    SSO_SCOPES='openid profile email groups',
)
class SSOLoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='student1',
            email='student1@example.test',
            password='existing-password',
        )
        self.profile = Profile.objects.create(user=self.user, login_count=5)

    def test_oidc_pkce_callback_and_single_use_ticket_preserve_local_account(self):
        metadata = {
            'issuer': 'https://sso.example.test/o',
            'authorization_endpoint': 'https://sso.example.test/o/authorize/',
            'token_endpoint': 'https://sso.example.test/o/token/',
            'jwks_uri': 'https://sso.example.test/o/jwks/',
        }
        discovery_response = Mock()
        discovery_response.json.return_value = metadata
        discovery_response.raise_for_status.return_value = None

        with patch('api.sso.requests.get', return_value=discovery_response):
            start_response = self.client.get('/api/auth/sso/start')

        self.assertEqual(start_response.status_code, 302)
        authorization_params = parse_qs(urlparse(start_response['Location']).query)
        self.assertEqual(authorization_params['client_id'], ['test-client'])
        self.assertEqual(authorization_params['scope'], ['openid profile email groups'])
        self.assertEqual(authorization_params['code_challenge_method'], ['S256'])

        state = authorization_params['state'][0]
        self.assertNotIn('sessionid', start_response.cookies)
        flow = SSOLoginFlow.objects.get(
            state_hash=hashlib.sha256(state.encode('utf-8')).hexdigest()
        )
        verifier = flow.code_verifier
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode('ascii')).digest()
        ).rstrip(b'=').decode('ascii')
        self.assertEqual(authorization_params['state'], [state])
        self.assertEqual(authorization_params['code_challenge'], [expected_challenge])

        token_response = Mock()
        token_response.ok = True
        token_response.status_code = 200
        token_response.json.return_value = {'id_token': 'signed-id-token'}
        token_response.raise_for_status.return_value = None
        claims = {
            'sub': 'sso-subject-123',
            'email': self.user.email,
            'email_verified': True,
        }
        with (
            patch('api.sso.requests.get', return_value=discovery_response),
            patch('api.sso.requests.post', return_value=token_response) as token_exchange,
            patch('api.sso._validate_id_token', return_value=claims),
        ):
            callback_response = self.client.get(
                '/api/auth/sso/callback',
                {
                    'code': 'authorization-code',
                    'state': state,
                    'iss': 'https://sso.example.test/o',
                },
            )

        self.assertEqual(callback_response.status_code, 302)
        callback_params = parse_qs(urlparse(callback_response['Location']).query)
        ticket = callback_params['sso_ticket'][0]
        self.assertNotIn('token', callback_params)
        token_exchange.assert_called_once()
        self.assertEqual(
            token_exchange.call_args.kwargs['data']['code_verifier'],
            verifier,
        )
        self.assertEqual(
            token_exchange.call_args.kwargs['data']['client_id'],
            'test-client',
        )
        self.assertEqual(token_exchange.call_args.kwargs['headers'], {})
        self.assertTrue(
            SSOIdentity.objects.filter(
                user=self.user,
                issuer='https://sso.example.test/o',
                subject='sso-subject-123',
            ).exists()
        )
        self.assertFalse(SSOLoginFlow.objects.exists())

        exchange_response = self.client.post(
            '/api/auth/sso/exchange',
            data={'ticket': ticket},
            content_type='application/json',
        )
        self.assertEqual(exchange_response.status_code, 200)
        self.assertEqual(exchange_response.json()['username'], self.user.username)

        replay_response = self.client.post(
            '/api/auth/sso/exchange',
            data={'ticket': ticket},
            content_type='application/json',
        )
        self.assertEqual(replay_response.status_code, 400)
        self.assertTrue(self.user.check_password('existing-password'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'student1@example.test')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.login_count, 6)
        self.assertEqual(SSOLoginTicket.objects.count(), 0)

    def test_invalid_state_does_not_exchange_authorization_code(self):
        response = self.client.get(
            '/api/auth/sso/callback',
            {'code': 'authorization-code', 'state': 'incorrect-state'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('sso_invalid_state', response['Location'])
        self.assertFalse(SSOIdentity.objects.exists())

    def test_unverified_email_is_not_linked(self):
        from .sso import SSOLoginError, _resolve_user

        with self.assertRaises(SSOLoginError) as error:
            _resolve_user(
                'https://sso.example.test/o',
                {
                    'sub': 'unverified-subject',
                    'email': self.user.email,
                    'email_verified': False,
                },
            )

        self.assertEqual(error.exception.error_code, 'sso_email_not_verified')
        self.assertFalse(SSOIdentity.objects.exists())

    def test_confidential_token_auth_supports_basic_and_post(self):
        from .sso import _token_endpoint_auth

        with override_settings(
            SSO_CLIENT_ID='client:id',
            SSO_CLIENT_SECRET='s/ecret+value',
            SSO_TOKEN_AUTH_METHOD='client_secret_basic',
        ):
            body, headers = _token_endpoint_auth()
        expected_basic = base64.b64encode(
            b'client%3Aid:s%2Fecret%2Bvalue'
        ).decode('ascii')
        self.assertEqual(body, {})
        self.assertEqual(headers, {'Authorization': f'Basic {expected_basic}'})

        with override_settings(
            SSO_CLIENT_ID='test-client',
            SSO_CLIENT_SECRET='test-secret',
            SSO_TOKEN_AUTH_METHOD='client_secret_post',
        ):
            body, headers = _token_endpoint_auth()
        self.assertEqual(
            body,
            {'client_id': 'test-client', 'client_secret': 'test-secret'},
        )
        self.assertEqual(headers, {})
