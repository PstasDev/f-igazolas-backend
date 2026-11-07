import base64
import hashlib
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import IgazolasAdmin, IgazolasTipusAdmin
from .models import (
    Igazolas,
    IgazolasTipus,
    Osztaly,
    Profile,
    SSOIdentity,
    SSOLoginFlow,
    SSOLoginTicket,
)


class IgazolasAdminPrivacyTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='student', password='password', is_staff=True
        )
        self.profile = Profile.objects.create(user=self.student)
        self.teacher = User.objects.create_user(
            username='teacher', password='password', is_staff=True
        )
        self.other_teacher = User.objects.create_user(
            username='other-teacher', password='password', is_staff=True
        )
        self.unrelated_admin = User.objects.create_superuser(
            username='unrelated-admin', email='admin@example.test', password='password'
        )

        self.school_class = Osztaly.objects.create(tagozat='A', kezdes_eve=2024)
        self.school_class.tanulok.add(self.student)
        self.school_class.osztalyfonokok.add(self.teacher)

        reason_type = IgazolasTipus.objects.create(nev='Orvosi igazolás')
        now = timezone.now()
        self.igazolas = Igazolas.objects.create(
            profile=self.profile,
            eleje=now,
            vege=now + timedelta(hours=1),
            tipus=reason_type,
            megjegyzes_diak='Szenzitív indoklás',
        )
        self.model_admin = IgazolasAdmin(Igazolas, admin.site)
        self.request_factory = RequestFactory()

    def get_field_names(self, user, obj=None):
        request = self.request_factory.get('/admin/api/igazolas/')
        request.user = user
        return {
            field
            for _, options in self.model_admin.get_fieldsets(request, obj)
            for field in options['fields']
        }

    def test_sensitive_fields_are_visible_to_student_and_homeroom_teacher(self):
        sensitive_fields = self.model_admin.sensitive_fields

        self.assertTrue(sensitive_fields.issubset(self.get_field_names(self.student, self.igazolas)))
        self.assertTrue(sensitive_fields.issubset(self.get_field_names(self.teacher, self.igazolas)))

    def test_sensitive_fields_are_hidden_from_other_admins_including_superusers(self):
        sensitive_fields = self.model_admin.sensitive_fields

        self.assertTrue(sensitive_fields.isdisjoint(
            self.get_field_names(self.other_teacher, self.igazolas)
        ))
        self.assertTrue(sensitive_fields.isdisjoint(
            self.get_field_names(self.unrelated_admin, self.igazolas)
        ))

    def test_sensitive_fields_are_not_shown_on_add_or_in_list_search(self):
        sensitive_fields = self.model_admin.sensitive_fields
        request = self.request_factory.get('/admin/api/igazolas/')
        request.user = self.unrelated_admin

        self.assertTrue(sensitive_fields.isdisjoint(self.get_field_names(self.unrelated_admin)))
        self.assertNotIn('get_megjegyzes_diak', self.model_admin.get_list_display(request))
        self.assertNotIn('megjegyzes_diak', self.model_admin.get_search_fields(request))


class IgazolasTipusAdminDeletionTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='type-admin',
            email='type-admin@example.test',
            password='password',
        )
        self.student = User.objects.create_user(username='type-student')
        self.profile = Profile.objects.create(user=self.student)
        self.replacement_type, _ = IgazolasTipus.objects.get_or_create(nev='Egyéb')
        self.type_to_delete = IgazolasTipus.objects.create(nev='Régi típus')
        now = timezone.now()
        self.igazolas = Igazolas.objects.create(
            profile=self.profile,
            eleje=now,
            vege=now + timedelta(hours=1),
            tipus=self.type_to_delete,
        )

    def test_delete_confirmation_defaults_reassignment_to_egyeb(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse('admin:api_igazolastipus_delete', args=[self.type_to_delete.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kapcsolt igazolások új típusa')
        self.assertContains(
            response,
            f'<option value="{self.replacement_type.pk}" selected>',
            html=False,
        )

    def test_delete_reassigns_linked_igazolas_before_removing_type(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('admin:api_igazolastipus_delete', args=[self.type_to_delete.pk]),
            {'post': 'yes', 'replacement_type': str(self.replacement_type.pk)},
        )

        self.assertEqual(response.status_code, 302)
        self.igazolas.refresh_from_db()
        self.assertEqual(self.igazolas.tipus_id, self.replacement_type.pk)
        self.assertFalse(IgazolasTipus.objects.filter(pk=self.type_to_delete.pk).exists())

    def test_delete_defaults_to_egyeb_if_replacement_post_is_missing(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('admin:api_igazolastipus_delete', args=[self.type_to_delete.pk]),
            {'post': 'yes'},
        )

        self.assertEqual(response.status_code, 302)
        self.igazolas.refresh_from_db()
        self.assertEqual(self.igazolas.tipus_id, self.replacement_type.pk)


class SchoolWideSectionPolicyTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='section-admin',
            email='section-admin@example.test',
            password='password',
        )
        self.f_class = Osztaly.objects.create(tagozat='F', kezdes_eve=2025)
        self.a_class = Osztaly.objects.create(tagozat='A', kezdes_eve=2025)
        self.media_type = IgazolasTipus.objects.create(
            nev='Médiás Távollét',
            available_tagozat='F',
        )

    def test_permission_matrix_locks_section_type_to_its_tagozat(self):
        from types import SimpleNamespace
        from .views import get_permission_matrix, is_igazolas_tipus_allowed_for_class, update_permission

        self.assertTrue(is_igazolas_tipus_allowed_for_class(self.media_type, self.f_class))
        self.assertFalse(is_igazolas_tipus_allowed_for_class(self.media_type, self.a_class))

        request = SimpleNamespace(auth=self.admin_user)
        status, result = get_permission_matrix(request)
        self.assertEqual(status, 200)
        matrix = result['matrix']

        self.assertTrue(matrix[self.f_class.id][self.media_type.id])
        self.assertFalse(matrix[self.a_class.id][self.media_type.id])

        status, response = update_permission(request, {
            'class_id': self.a_class.id,
            'type_id': self.media_type.id,
            'allowed': True,
        })
        self.assertEqual(status, 400)
        self.assertEqual(response['error'], 'Invalid permission')

    def test_full_ftv_sync_only_processes_f_classes(self):
        from unittest.mock import patch
        from .ftv_sync import sync_full_from_ftv

        with patch('api.ftv_sync.sync_base_from_ftv', return_value={'statistics': {}}), \
             patch('api.ftv_sync.sync_class_absences_from_ftv', return_value={'statistics': {}}) as sync_class, \
             patch('api.ftv_sync.update_cache_metadata'):
            sync_full_from_ftv()

        self.assertEqual(
            [call.args[0].id for call in sync_class.call_args_list],
            [self.f_class.id],
        )

    def test_direct_class_ftv_sync_skips_non_f_classes(self):
        from .ftv_sync import sync_class_absences_from_ftv

        result = sync_class_absences_from_ftv(self.a_class)

        self.assertTrue(result['skipped'])
        self.assertEqual(result['statistics']['errors'], 0)

    def test_frontend_type_import_is_superuser_only_and_idempotent(self):
        from types import SimpleNamespace

        from .schemas import FrontendIgazolasTipusImportRequest
        from .views import import_frontend_igazolas_type

        data = FrontendIgazolasTipusImportRequest(
            nev='Nyelvvizsga',
            leiras='A tanuló előre egyeztetett nyelvvizsgán vett részt',
            beleszamit=False,
            iskolaerdeku=True,
            category='tanulmanyi',
            available_tagozat=None,
        )
        status, result = import_frontend_igazolas_type(
            SimpleNamespace(auth=self.admin_user),
            data,
        )
        self.assertEqual(status, 201)
        imported_type = IgazolasTipus.objects.get(pk=result['id'])
        self.assertEqual(imported_type.category, 'tanulmanyi')

        status, result = import_frontend_igazolas_type(
            SimpleNamespace(auth=self.admin_user),
            data,
        )
        self.assertEqual(status, 200)
        self.assertFalse(result['created'])
        self.assertEqual(IgazolasTipus.objects.filter(nev__iexact='Nyelvvizsga').count(), 1)

        status, _ = import_frontend_igazolas_type(
            SimpleNamespace(auth=User.objects.create_user(username='not-admin')),
            FrontendIgazolasTipusImportRequest(nev='Nem engedélyezett típus'),
        )
        self.assertEqual(status, 403)

    def test_class_archival_does_not_archive_other_sections_records(self):
        from types import SimpleNamespace
        from .views import archive_class

        class_student = User.objects.create_user(username='archive-a-student')
        other_student = User.objects.create_user(username='archive-f-student')
        class_profile = Profile.objects.create(user=class_student)
        other_profile = Profile.objects.create(user=other_student)
        self.a_class.tanulok.add(class_student)
        self.f_class.tanulok.add(other_student)
        reason_type = IgazolasTipus.objects.create(nev='Orvosi igazolás')
        now = timezone.now()
        class_record = Igazolas.objects.create(
            profile=class_profile,
            eleje=now,
            vege=now + timedelta(hours=1),
            tipus=reason_type,
        )
        other_record = Igazolas.objects.create(
            profile=other_profile,
            eleje=now,
            vege=now + timedelta(hours=1),
            tipus=reason_type,
        )

        status, _ = archive_class(SimpleNamespace(auth=self.admin_user), self.a_class.id)

        self.assertEqual(status, 200)
        class_record.refresh_from_db()
        other_record.refresh_from_db()
        self.assertTrue(class_record.archived)
        self.assertFalse(other_record.archived)


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


@override_settings(USE_TZ=True)
class BKKDisruptionHistoryTests(TestCase):
    def setUp(self):
        self.current_user = User.objects.create_user(username='current-student')
        self.other_user_one = User.objects.create_user(username='other-student-one')
        self.other_user_two = User.objects.create_user(username='other-student-two')
        self.current_profile = Profile.objects.create(user=self.current_user)
        self.other_profile_one = Profile.objects.create(user=self.other_user_one)
        self.other_profile_two = Profile.objects.create(user=self.other_user_two)
        self.koezlekedes = IgazolasTipus.objects.create(nev='Közlekedés')
        self.period_start = timezone.make_aware(datetime(2026, 11, 2, 8, 0))
        self.period_end = timezone.make_aware(datetime(2026, 11, 2, 9, 0))

    def create_disruption_igazolas(self, profile, alert_id='bkk-alert-1', active_date='2026-11-02'):
        return Igazolas.objects.create(
            profile=profile,
            eleje=self.period_start,
            vege=self.period_end,
            tipus=self.koezlekedes,
            bkk_verification={
                'type': 'disruption',
                'alert_data': {
                    'id': alert_id,
                    'title': 'M3 pótlóbusz',
                    'description': 'Forgalmi változás az M3 vonalán',
                    'affected_routes': ['M3'],
                    'priority': 0,
                    'category': 'metro',
                    'effect': 'NO_SERVICE',
                    'cause': 'MAINTENANCE',
                    'active_period': {
                        'start': f'{active_date}T07:00:00+00:00',
                        'end': f'{active_date}T09:00:00+00:00',
                    },
                },
            },
        )

    def test_returns_date_matching_disruptions_with_unique_anonymous_schoolwide_count(self):
        from types import SimpleNamespace

        from .views import bkk_disruption_history

        self.create_disruption_igazolas(self.current_profile)
        self.create_disruption_igazolas(self.other_profile_one)
        self.create_disruption_igazolas(self.other_profile_one)
        self.create_disruption_igazolas(self.other_profile_two)
        self.create_disruption_igazolas(self.other_profile_two, alert_id='different-date', active_date='2026-11-03')

        result = bkk_disruption_history(SimpleNamespace(auth=self.current_user), '2026-11-02')

        self.assertEqual(len(result['alerts']), 1)
        self.assertEqual(result['alerts'][0]['alert']['id'], 'bkk-alert-1')
        self.assertEqual(result['alerts'][0]['otherStudentCount'], 2)
        self.assertEqual(set(result['alerts'][0]), {'alert', 'otherStudentCount'})

    def test_ignores_vehicle_verifications(self):
        from types import SimpleNamespace

        from .views import bkk_disruption_history

        Igazolas.objects.create(
            profile=self.other_profile_one,
            eleje=self.period_start,
            vege=self.period_end,
            tipus=self.koezlekedes,
            bkk_verification={'type': 'vehicle_modification'},
        )

        result = bkk_disruption_history(SimpleNamespace(auth=self.current_user), '2026-11-02')

        self.assertEqual(result['alerts'], [])

    def test_rejects_invalid_date(self):
        from types import SimpleNamespace

        from .views import bkk_disruption_history

        status_code, _ = bkk_disruption_history(SimpleNamespace(auth=self.current_user), '02-11-2026')

        self.assertEqual(status_code, 400)
