from ninja import NinjaAPI
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone
from django_ratelimit.decorators import ratelimit
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from django.conf import settings
from typing import List
import logging
import requests

from .models import (
    Profile, Osztaly, Mulasztas, IgazolasTipus, Igazolas,
    PasswordResetOTP, ForgotPasswordToken
)
from .schemas import (
    LoginRequest, TokenResponse, ErrorResponse,
    ProfileSchema, OsztalySchema, MulasztasSchema,
    IgazolasTipusSchema, IgazolasSchema, IgazolasCreateRequest,
    OsztalySimpleSchema, QuickActionRequest, BulkQuickActionRequest,
    QuickActionResponse, BulkQuickActionResponse, TeacherCommentUpdateRequest,
    TeacherCommentUpdateResponse, DiakjaSignleSchema, DiakjaCreateRequest, 
    DiakjaCreateResponse, ForgotPasswordRequest, ForgotPasswordResponse,
    CheckOTPRequest, CheckOTPResponse, ChangePasswordOTPRequest,
    ChangePasswordOTPResponse
)
from .jwt_utils import generate_jwt_token, decode_jwt_token
from .authentication import JWTAuth
from .email_utils import send_otp_email, send_password_changed_notification
from .ftv_sync import sync_with_ftv, FTVSyncError

logger = logging.getLogger(__name__)

# Initialize Ninja API
api = NinjaAPI(
    title="Igazolás API",
    version="1.0.0",
    description="API for managing student absences and justifications"
)

# Initialize JWT authentication
jwt_auth = JWTAuth()


# BKK GTFS-RT Endpoints

@api.get("/bkk/TripUpdates", auth=None, tags=["BKK"])
def bkk_trip_updates(request):
    """
    BKK Trip Updates proxy endpoint.
    
    Forwards requests to BKK GTFS-RT TripUpdates API and returns the response as-is.
    """
    bkk_token = settings.BKK_TOKEN
    if not bkk_token:
        return HttpResponse("BKK token not configured", status=500, content_type="text/plain")
    
    try:
        response = requests.get(
            f"https://go.bkk.hu/api/query/v1/ws/gtfs-rt/full/TripUpdates.txt?key={bkk_token}",
            timeout=30
        )
        
        return HttpResponse(
            response.content,
            status=response.status_code,
            content_type=response.headers.get('Content-Type', 'text/plain;charset=utf-8')
        )
    except requests.RequestException as e:
        logger.error(f"Error fetching BKK TripUpdates: {str(e)}")
        return HttpResponse("Error fetching BKK data", status=500, content_type="text/plain")


@api.get("/bkk/Alerts", auth=None, tags=["BKK"])
def bkk_alerts(request):
    """
    BKK Alerts proxy endpoint.
    
    Forwards requests to BKK GTFS-RT Alerts API and returns the response as-is.
    """
    bkk_token = settings.BKK_TOKEN
    if not bkk_token:
        return HttpResponse("BKK token not configured", status=500, content_type="text/plain")
    
    try:
        response = requests.get(
            f"https://go.bkk.hu/api/query/v1/ws/gtfs-rt/full/Alerts.txt?key={bkk_token}",
            timeout=30
        )
        
        return HttpResponse(
            response.content,
            status=response.status_code,
            content_type=response.headers.get('Content-Type', 'text/plain;charset=utf-8')
        )
    except requests.RequestException as e:
        logger.error(f"Error fetching BKK Alerts: {str(e)}")
        return HttpResponse("Error fetching BKK data", status=500, content_type="text/plain")


@api.get("/bkk/VehiclePositions", auth=None, tags=["BKK"])
def bkk_vehicle_positions(request):
    """
    BKK Vehicle Positions proxy endpoint.
    
    Forwards requests to BKK GTFS-RT VehiclePositions API and returns the response as-is.
    """
    bkk_token = settings.BKK_TOKEN
    if not bkk_token:
        return HttpResponse("BKK token not configured", status=500, content_type="text/plain")
    
    try:
        response = requests.get(
            f"https://go.bkk.hu/api/query/v1/ws/gtfs-rt/full/VehiclePositions.txt?key={bkk_token}",
            timeout=30
        )
        
        return HttpResponse(
            response.content,
            status=response.status_code,
            content_type=response.headers.get('Content-Type', 'text/plain;charset=utf-8')
        )
    except requests.RequestException as e:
        logger.error(f"Error fetching BKK VehiclePositions: {str(e)}")
        return HttpResponse("Error fetching BKK data", status=500, content_type="text/plain")


# Helper functions
def is_class_teacher(user: User) -> bool:
    """Check if user is a class teacher (osztályfőnök)"""
    return Osztaly.objects.filter(osztalyfonokok=user).exists()


def get_teacher_class(user: User) -> Osztaly:
    """Get the class for which the user is a teacher"""
    return Osztaly.objects.filter(osztalyfonokok=user).first()


# Authentication Endpoints

@api.post("/login", response={200: TokenResponse, 401: ErrorResponse}, auth=None, tags=["Authentication"])
def login(request, data: LoginRequest):
    """
    Login endpoint that returns JWT token.
    
    Returns JWT token containing user_id, username, iat, and exp.
    """
    user = authenticate(username=data.username, password=data.password)
    
    if user is None:
        return 401, {
            'error': 'Unauthorized',
            'detail': 'Invalid username or password'
        }
    
    if not user.is_active:
        return 401, {
            'error': 'Unauthorized',
            'detail': 'User account is disabled'
        }
    
    # Generate JWT token
    token = generate_jwt_token(user)
    
    # Decode to get iat and exp
    payload = decode_jwt_token(token)
    
    return 200, {
        'token': token,
        'user_id': user.id,
        'username': user.username,
        'iat': payload['iat'],
        'exp': payload['exp']
    }


# Profile Endpoints

@api.get("/profiles", response={200: List[ProfileSchema], 401: ErrorResponse}, auth=jwt_auth, tags=["Profile"])
def list_profiles(request):
    """Get all profiles (requires authentication)"""
    profiles = Profile.objects.all()
    result = []
    
    for profile in profiles:
        osztaly = profile.osztalyom()
        profile_data = {
            'id': profile.id,
            'user': {
                'id': profile.user.id,
                'username': profile.user.username,
                'first_name': profile.user.first_name,
                'last_name': profile.user.last_name,
                'email': profile.user.email
            },
            'osztalyom': {
                'id': osztaly.id,
                'tagozat': osztaly.tagozat,
                'kezdes_eve': osztaly.kezdes_eve,
                'nev': str(osztaly)
            } if osztaly else None
        }
        result.append(profile_data)
    
    return 200, result


@api.get("/profiles/me", response={200: ProfileSchema, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Profile"])
def get_my_profile(request):
    """Get current user's profile (requires authentication)"""
    try:
        profile = Profile.objects.get(user=request.auth)
        osztaly = profile.osztalyom()
        
        return 200, {
            'id': profile.id,
            'user': {
                'id': profile.user.id,
                'username': profile.user.username,
                'first_name': profile.user.first_name,
                'last_name': profile.user.last_name,
                'email': profile.user.email
            },
            'osztalyom': {
                'id': osztaly.id,
                'tagozat': osztaly.tagozat,
                'kezdes_eve': osztaly.kezdes_eve,
                'nev': str(osztaly)
            } if osztaly else None
        }
    except Profile.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': 'Profile not found for current user'
        }


@api.get("/profiles/{profile_id}", response={200: ProfileSchema, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Profile"])
def get_profile(request, profile_id: int):
    """Get profile by ID (requires authentication)"""
    profile = get_object_or_404(Profile, id=profile_id)
    osztaly = profile.osztalyom()
    
    return 200, {
        'id': profile.id,
        'user': {
            'id': profile.user.id,
            'username': profile.user.username,
            'first_name': profile.user.first_name,
            'last_name': profile.user.last_name,
            'email': profile.user.email
        },
        'osztalyom': {
            'id': osztaly.id,
            'tagozat': osztaly.tagozat,
            'kezdes_eve': osztaly.kezdes_eve,
            'nev': str(osztaly)
        } if osztaly else None
    }


# Osztaly Endpoints

@api.get("/osztaly", response={200: List[OsztalySchema], 401: ErrorResponse}, auth=jwt_auth, tags=["Osztaly"])
def list_osztaly(request):
    """Get all classes (requires authentication)"""
    osztalyok = Osztaly.objects.all()
    result = []
    
    for osztaly in osztalyok:
        osztaly_data = {
            'id': osztaly.id,
            'tagozat': osztaly.tagozat,
            'kezdes_eve': osztaly.kezdes_eve,
            'nev': str(osztaly),
            'tanulok': [
                {
                    'id': tanulo.id,
                    'username': tanulo.username,
                    'first_name': tanulo.first_name,
                    'last_name': tanulo.last_name,
                    'email': tanulo.email
                } for tanulo in osztaly.tanulok.all()
            ],
            'osztalyfonokok': [
                {
                    'id': of.id,
                    'username': of.username,
                    'first_name': of.first_name,
                    'last_name': of.last_name,
                    'email': of.email
                } for of in osztaly.osztalyfonokok.all()
            ]
        }
        result.append(osztaly_data)
    
    return 200, result


@api.get("/osztaly/{osztaly_id}", response={200: OsztalySchema, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Osztaly"])
def get_osztaly(request, osztaly_id: int):
    """Get class by ID (requires authentication)"""
    osztaly = get_object_or_404(Osztaly, id=osztaly_id)
    
    return 200, {
        'id': osztaly.id,
        'tagozat': osztaly.tagozat,
        'kezdes_eve': osztaly.kezdes_eve,
        'nev': str(osztaly),
        'tanulok': [
            {
                'id': tanulo.id,
                'username': tanulo.username,
                'first_name': tanulo.first_name,
                'last_name': tanulo.last_name,
                'email': tanulo.email
            } for tanulo in osztaly.tanulok.all()
        ],
        'osztalyfonokok': [
            {
                'id': of.id,
                'username': of.username,
                'first_name': of.first_name,
                'last_name': of.last_name,
                'email': of.email
            } for of in osztaly.osztalyfonokok.all()
        ]
    }


# Mulasztas Endpoints

@api.get("/mulasztas", response={200: List[MulasztasSchema], 401: ErrorResponse}, auth=jwt_auth, tags=["Mulasztas"])
def list_mulasztas(request):
    """Get all absences (requires authentication)"""
    mulasztasok = Mulasztas.objects.all()
    return 200, list(mulasztasok)


@api.get("/mulasztas/{mulasztas_id}", response={200: MulasztasSchema, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Mulasztas"])
def get_mulasztas(request, mulasztas_id: int):
    """Get absence by ID (requires authentication)"""
    mulasztas = get_object_or_404(Mulasztas, id=mulasztas_id)
    return 200, mulasztas


# IgazolasTipus Endpoints

@api.get("/igazolas-tipus", response={200: List[IgazolasTipusSchema], 401: ErrorResponse}, auth=jwt_auth, tags=["IgazolasTipus"])
def list_igazolas_tipus(request):
    """Get all justification types (requires authentication)"""
    tipusok = IgazolasTipus.objects.all()
    return 200, list(tipusok)


@api.get("/igazolas-tipus/{tipus_id}", response={200: IgazolasTipusSchema, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["IgazolasTipus"])
def get_igazolas_tipus(request, tipus_id: int):
    """Get justification type by ID (requires authentication)"""
    tipus = get_object_or_404(IgazolasTipus, id=tipus_id)
    return 200, tipus


# Igazolas Endpoints

@api.get("/igazolas", response={200: List[IgazolasSchema], 401: ErrorResponse}, auth=jwt_auth, tags=["Igazolas"])
def list_igazolas(request):
    """
    Get all justifications (requires authentication).
    
    This endpoint automatically syncs with FTV before returning data.
    Only accessible by osztályfőnök (class teachers).
    """
    # Sync with FTV before fetching data
    try:
        logger.info(f"User {request.auth.username} requested /igazolas - triggering FTV sync")
        sync_stats = sync_with_ftv()
        logger.info(f"FTV sync completed: {sync_stats}")
    except FTVSyncError as e:
        logger.error(f"FTV sync failed but continuing with existing data: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error during FTV sync: {str(e)}")
    
    # Fetch igazolások for the teacher's class
    igazolasok = Profile.objects.filter(user=request.auth).first().osztalyom_igazolasai().select_related('profile', 'tipus').prefetch_related('mulasztasok')
    result = []
    
    for igazolas in igazolasok:
        osztaly = igazolas.profile.osztalyom()
        igazolas_data = {
            'id': igazolas.id,
            'profile': {
                'id': igazolas.profile.id,
                'user': {
                    'id': igazolas.profile.user.id,
                    'username': igazolas.profile.user.username,
                    'first_name': igazolas.profile.user.first_name,
                    'last_name': igazolas.profile.user.last_name,
                    'email': igazolas.profile.user.email
                },
                'osztalyom': {
                    'id': osztaly.id,
                    'tagozat': osztaly.tagozat,
                    'kezdes_eve': osztaly.kezdes_eve,
                    'nev': str(osztaly)
                } if osztaly else None
            },
            'mulasztasok': list(igazolas.mulasztasok.all()),
            'eleje': igazolas.eleje,
            'vege': igazolas.vege,
            'tipus': igazolas.tipus,
            'rogzites_datuma': igazolas.rogzites_datuma,
            'megjegyzes_diak': igazolas.megjegyzes_diak,
            'diak': igazolas.diak,
            'ftv': igazolas.ftv,
            'korrigalt': igazolas.korrigalt,
            'ftv_hianyzas_id': igazolas.ftv_hianyzas_id,
            'diak_extra_ido_elotte': igazolas.diak_extra_ido_elotte,
            'diak_extra_ido_utana': igazolas.diak_extra_ido_utana,
            'imgDriveURL': igazolas.imgDriveURL,
            'bkk_verification': igazolas.bkk_verification,
            'allapot': igazolas.allapot,
            'megjegyzes_tanar': igazolas.megjegyzes_tanar,
            'kretaban_rogzitettem': igazolas.kretaban_rogzitettem
        }
        result.append(igazolas_data)
    
    return 200, result


@api.get("/igazolas/my", response={200: List[IgazolasSchema], 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Igazolas"])
def get_my_igazolas(request):
    """
    Get current user's justifications (requires authentication).
    
    This endpoint automatically syncs with FTV before returning data.
    Students can see their own records here.
    """
    # Sync with FTV before fetching data
    try:
        logger.info(f"User {request.auth.username} requested /igazolas/my - triggering FTV sync")
        sync_stats = sync_with_ftv()
        logger.info(f"FTV sync completed: {sync_stats}")
    except FTVSyncError as e:
        logger.error(f"FTV sync failed but continuing with existing data: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error during FTV sync: {str(e)}")
    
    try:
        profile = Profile.objects.get(user=request.auth)
        igazolasok = Igazolas.objects.filter(profile=profile).select_related('tipus').prefetch_related('mulasztasok')
        result = []
        
        for igazolas in igazolasok:
            osztaly = igazolas.profile.osztalyom()
            igazolas_data = {
                'id': igazolas.id,
                'profile': {
                    'id': igazolas.profile.id,
                    'user': {
                        'id': igazolas.profile.user.id,
                        'username': igazolas.profile.user.username,
                        'first_name': igazolas.profile.user.first_name,
                        'last_name': igazolas.profile.user.last_name,
                        'email': igazolas.profile.user.email
                    },
                    'osztalyom': {
                        'id': osztaly.id,
                        'tagozat': osztaly.tagozat,
                        'kezdes_eve': osztaly.kezdes_eve,
                        'nev': str(osztaly)
                    } if osztaly else None
                },
                'mulasztasok': list(igazolas.mulasztasok.all()),
                'eleje': igazolas.eleje,
                'vege': igazolas.vege,
                'tipus': igazolas.tipus,
                'rogzites_datuma': igazolas.rogzites_datuma,
                'megjegyzes_diak': igazolas.megjegyzes_diak,
                'diak': igazolas.diak,
                'ftv': igazolas.ftv,
                'korrigalt': igazolas.korrigalt,
                'ftv_hianyzas_id': igazolas.ftv_hianyzas_id,
                'diak_extra_ido_elotte': igazolas.diak_extra_ido_elotte,
                'diak_extra_ido_utana': igazolas.diak_extra_ido_utana,
                'imgDriveURL': igazolas.imgDriveURL,
                'bkk_verification': igazolas.bkk_verification,
                'allapot': igazolas.allapot,
                'megjegyzes_tanar': igazolas.megjegyzes_tanar,
                'kretaban_rogzitettem': igazolas.kretaban_rogzitettem
            }
            result.append(igazolas_data)
        
        return 200, result
    except Profile.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': 'Profile not found for current user'
        }


@api.get("/igazolas/{igazolas_id}", response={200: IgazolasSchema, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Igazolas"])
def get_igazolas(request, igazolas_id: int):
    """Get justification by ID (requires authentication)"""
    igazolas = get_object_or_404(Igazolas.objects.select_related('profile', 'tipus').prefetch_related('mulasztasok'), id=igazolas_id)
    osztaly = igazolas.profile.osztalyom()
    
    return 200, {
        'id': igazolas.id,
        'profile': {
            'id': igazolas.profile.id,
            'user': {
                'id': igazolas.profile.user.id,
                'username': igazolas.profile.user.username,
                'first_name': igazolas.profile.user.first_name,
                'last_name': igazolas.profile.user.last_name,
                'email': igazolas.profile.user.email
            },
            'osztalyom': {
                'id': osztaly.id,
                'tagozat': osztaly.tagozat,
                'kezdes_eve': osztaly.kezdes_eve,
                'nev': str(osztaly)
            } if osztaly else None
        },
        'mulasztasok': list(igazolas.mulasztasok.all()),
        'eleje': igazolas.eleje,
        'vege': igazolas.vege,
        'tipus': igazolas.tipus,
        'rogzites_datuma': igazolas.rogzites_datuma,
        'megjegyzes_diak': igazolas.megjegyzes_diak,
        'diak': igazolas.diak,
        'ftv': igazolas.ftv,
        'korrigalt': igazolas.korrigalt,
        'ftv_hianyzas_id': igazolas.ftv_hianyzas_id,
        'diak_extra_ido_elotte': igazolas.diak_extra_ido_elotte,
        'diak_extra_ido_utana': igazolas.diak_extra_ido_utana,
        'imgDriveURL': igazolas.imgDriveURL,
        'bkk_verification': igazolas.bkk_verification,
        'allapot': igazolas.allapot,
        'megjegyzes_tanar': igazolas.megjegyzes_tanar,
        'kretaban_rogzitettem': igazolas.kretaban_rogzitettem
    }


@api.post("/igazolas", response={201: IgazolasSchema, 400: ErrorResponse, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Igazolas"])
def create_igazolas(request, data: IgazolasCreateRequest):
    """
    Create new justification (Új Igazolás form submission).
    
    Requires authentication. Creates a new justification for the authenticated user.
    """
    # Validate that eleje is before vege
    if data.eleje >= data.vege:
        return 400, {
            'error': 'Validation error',
            'detail': 'End time must be after start time'
        }
    
    # Get or create profile for the user
    profile, created = Profile.objects.get_or_create(user=request.auth)
    
    # Verify tipus exists
    try:
        tipus = IgazolasTipus.objects.get(id=data.tipus)
    except IgazolasTipus.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'IgazolasTipus with id {data.tipus} does not exist'
        }
    
    # Create the igazolas
    # Note: ftv is always False for user-created igazolások
    # It will only be True when automatically synced from FTV system
    igazolas = Igazolas.objects.create(
        profile=profile,
        eleje=data.eleje,
        vege=data.vege,
        tipus=tipus,
        megjegyzes_diak=data.megjegyzes_diak,
        diak=data.diak if data.diak is not None else True,
        ftv=False,  # Always False for user submissions
        korrigalt=False,
        diak_extra_ido_elotte=None,
        diak_extra_ido_utana=None,
        imgDriveURL=data.imgDriveURL,
        bkk_verification=data.bkk_verification
    )
    
    osztaly = igazolas.profile.osztalyom()
    
    return 201, {
        'id': igazolas.id,
        'profile': {
            'id': igazolas.profile.id,
            'user': {
                'id': igazolas.profile.user.id,
                'username': igazolas.profile.user.username,
                'first_name': igazolas.profile.user.first_name,
                'last_name': igazolas.profile.user.last_name,
                'email': igazolas.profile.user.email
            },
            'osztalyom': {
                'id': osztaly.id,
                'tagozat': osztaly.tagozat,
                'kezdes_eve': osztaly.kezdes_eve,
                'nev': str(osztaly)
            } if osztaly else None
        },
        'mulasztasok': [],
        'eleje': igazolas.eleje,
        'vege': igazolas.vege,
        'tipus': igazolas.tipus,
        'rogzites_datuma': igazolas.rogzites_datuma,
        'megjegyzes_diak': igazolas.megjegyzes_diak,
        'diak': igazolas.diak,
        'ftv': igazolas.ftv,
        'korrigalt': igazolas.korrigalt,
        'ftv_hianyzas_id': None,  # New igazolas doesn't have FTV ID
        'diak_extra_ido_elotte': igazolas.diak_extra_ido_elotte,
        'diak_extra_ido_utana': igazolas.diak_extra_ido_utana,
        'imgDriveURL': igazolas.imgDriveURL,
        'bkk_verification': igazolas.bkk_verification,
        'allapot': igazolas.allapot,
        'megjegyzes_tanar': igazolas.megjegyzes_tanar,
        'kretaban_rogzitettem': igazolas.kretaban_rogzitettem
    }


# Quick Action Endpoints

@api.post("/igazolas/{igazolas_id}/quick-action", response={200: QuickActionResponse, 400: ErrorResponse, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Igazolas"])
def quick_action_igazolas(request, igazolas_id: int, data: QuickActionRequest):
    """
    Quick action to change igazolas status (Elfogadva/Elutasítva/Függőben).
    
    Requires authentication. Only teachers (osztályfőnök) can perform quick actions.
    """
    # Validate action
    valid_actions = ['Elfogadva', 'Elutasítva', 'Függőben']
    if data.action not in valid_actions:
        return 400, {
            'error': 'Invalid action',
            'detail': f'Action must be one of: {", ".join(valid_actions)}'
        }
    
    # Get the igazolas
    try:
        igazolas = Igazolas.objects.get(id=igazolas_id)
    except Igazolas.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Igazolas with id {igazolas_id} does not exist'
        }
    
    # Check if user is osztályfőnök of the student's class
    student_class = igazolas.profile.osztalyom()
    if not student_class or request.auth not in student_class.osztalyfonokok.all():
        return 401, {
            'error': 'Unauthorized',
            'detail': 'Only class teachers can perform quick actions'
        }
    
    # Update the status
    igazolas.allapot = data.action
    igazolas.save()
    
    return 200, {
        'id': igazolas.id,
        'allapot': igazolas.allapot,
        'message': f'Igazolas status updated to {data.action}'
    }


@api.post("/igazolas/quick-action/bulk", response={200: BulkQuickActionResponse, 400: ErrorResponse, 401: ErrorResponse}, auth=jwt_auth, tags=["Igazolas"])
def bulk_quick_action_igazolas(request, data: BulkQuickActionRequest):
    """
    Bulk quick action to change multiple igazolas statuses (Elfogadva/Elutasítva/Függőben).
    
    Requires authentication. Only teachers (osztályfőnök) can perform bulk quick actions.
    """
    # Validate action
    valid_actions = ['Elfogadva', 'Elutasítva', 'Függőben']
    if data.action not in valid_actions:
        return 400, {
            'error': 'Invalid action',
            'detail': f'Action must be one of: {", ".join(valid_actions)}'
        }
    
    if not data.ids:
        return 400, {
            'error': 'Invalid request',
            'detail': 'No IDs provided'
        }
    
    # Get all igazolasok that exist
    igazolasok = Igazolas.objects.filter(id__in=data.ids).select_related('profile')
    found_ids = set(igazolas.id for igazolas in igazolasok)
    failed_ids = [id for id in data.ids if id not in found_ids]
    
    # Check permissions and update
    updated_count = 0
    for igazolas in igazolasok:
        student_class = igazolas.profile.osztalyom()
        if student_class and request.auth in student_class.osztalyfonokok.all():
            igazolas.allapot = data.action
            igazolas.save()
            updated_count += 1
        else:
            failed_ids.append(igazolas.id)
    
    return 200, {
        'updated_count': updated_count,
        'failed_ids': failed_ids,
        'message': f'Updated {updated_count} igazolas(ok) to {data.action}'
    }


# Teacher Comment Edit Endpoint

@api.put("/igazolas/{igazolas_id}/teacher-comment", response={200: TeacherCommentUpdateResponse, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Igazolas"])
def update_teacher_comment(request, igazolas_id: int, data: TeacherCommentUpdateRequest):
    """
    Update osztályfőnök megjegyzése (teacher comment) for an igazolas.
    
    Requires authentication. Only teachers (osztályfőnök) can edit teacher comments.
    """
    # Get the igazolas
    try:
        igazolas = Igazolas.objects.get(id=igazolas_id)
    except Igazolas.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Igazolas with id {igazolas_id} does not exist'
        }
    
    # Check if user is osztályfőnök of the student's class
    student_class = igazolas.profile.osztalyom()
    if not student_class or request.auth not in student_class.osztalyfonokok.all():
        return 401, {
            'error': 'Unauthorized',
            'detail': 'Only class teachers can edit teacher comments'
        }
    
    # Update the teacher comment
    igazolas.megjegyzes_tanar = data.megjegyzes_tanar
    igazolas.save()
    
    return 200, {
        'id': igazolas.id,
        'megjegyzes_tanar': igazolas.megjegyzes_tanar,
        'message': 'Teacher comment updated successfully'
    }


# Password Reset Endpoints

@api.post("/forgot-password", response={200: ForgotPasswordResponse, 400: ErrorResponse, 404: ErrorResponse}, auth=None, tags=["Password Reset"])
@csrf_exempt
@ratelimit(key='ip', rate='3/h', method='POST', block=True)
def forgot_password(request, data: ForgotPasswordRequest):
    """
    Request password reset by sending OTP to user's email.
    
    Rate limited to 3 requests per hour per IP.
    """
    try:
        # Find user by username
        user = User.objects.get(username=data.username, is_active=True)
        
        # Check if user has email
        if not user.email:
            return 400, {
                'error': 'Bad request',
                'detail': 'No email address found for this user'
            }
        
        # Create OTP for user
        otp_instance = PasswordResetOTP.create_for_user(user)
        otp_code = otp_instance.generate_otp()
        
        # Send OTP email
        logger.debug(f"[VIEWS DEBUG] Calling send_otp_email for user {user.username}")
        email_sent = send_otp_email(user, otp_code)
        
        if email_sent:
            logger.info(f"✓ [VIEWS] Password reset OTP sent to user {user.username}")
            return 200, {
                'message': 'OTP kód elküldve az email címére. Ellenőrizze a postafiókját.',
                'email_sent': True
            }
        else:
            logger.error(f"✗ [VIEWS] Failed to send OTP email to user {user.username}")
            return 400, {
                'error': 'Email sending failed',
                'detail': 'Nem sikerült elküldeni az email-t. Kérjük próbálja újra később.'
            }
            
    except User.DoesNotExist:
        # Don't reveal if username exists or not for security
        return 200, {
            'message': 'Ha a felhasználónév létezik, OTP kód lett küldve az email címére.',
            'email_sent': True
        }
    except Exception as e:
        logger.error(f"Error in forgot_password: {str(e)}")
        return 400, {
            'error': 'Server error',
            'detail': 'Hiba történt a kérés feldolgozása során.'
        }


@api.post("/check-otp", response={200: CheckOTPResponse, 400: ErrorResponse, 429: ErrorResponse}, auth=None, tags=["Password Reset"])
@csrf_exempt
@ratelimit(key='ip', rate='10/h', method='POST', block=True)
def check_otp(request, data: CheckOTPRequest):
    """
    Verify OTP code and return temporary reset token.
    
    Rate limited to 10 requests per hour per IP.
    """
    try:
        # Find user
        user = User.objects.get(username=data.username, is_active=True)
        
        # Find active OTP
        otp_instance = PasswordResetOTP.objects.filter(
            user=user,
            is_used=False
        ).order_by('-created_at').first()
        
        if not otp_instance:
            return 400, {
                'error': 'Invalid request',
                'detail': 'Nincs aktív OTP kérés. Kérjük kérjen új OTP kódot.'
            }
        
        # Check if OTP is expired
        if otp_instance.is_expired():
            return 400, {
                'error': 'OTP expired',
                'detail': 'Az OTP kód lejárt. Kérjük kérjen új OTP kódot.'
            }
        
        # Check attempts limit
        if not otp_instance.can_attempt():
            otp_instance.is_used = True
            otp_instance.save()
            return 400, {
                'error': 'Too many attempts',
                'detail': 'Túl sok sikertelen próbálkozás. Kérjük kérjen új OTP kódot.'
            }
        
        # Increment attempts
        otp_instance.attempts += 1
        otp_instance.save()
        
        # Verify OTP
        if otp_instance.verify_otp(data.otp_code):
            # Mark OTP as used
            otp_instance.is_used = True
            otp_instance.save()
            
            # Create temporary reset token
            reset_token_instance = ForgotPasswordToken.create_for_user(user)
            
            logger.info(f"OTP verified successfully for user {user.username}")
            return 200, {
                'message': 'OTP kód sikeresen ellenőrizve. Használja a tokent a jelszó megváltoztatásához.',
                'reset_token': reset_token_instance.token,
                'expires_in_minutes': 10
            }
        else:
            return 400, {
                'error': 'Invalid OTP',
                'detail': f'Érvénytelen OTP kód. {5 - otp_instance.attempts} próbálkozás maradt.'
            }
            
    except User.DoesNotExist:
        return 400, {
            'error': 'User not found',
            'detail': 'Felhasználó nem található.'
        }
    except Exception as e:
        logger.error(f"Error in check_otp: {str(e)}")
        return 400, {
            'error': 'Server error',
            'detail': 'Hiba történt a kérés feldolgozása során.'
        }


@api.post("/change-password-otp", response={200: ChangePasswordOTPResponse, 400: ErrorResponse, 401: ErrorResponse}, auth=None, tags=["Password Reset"])
@csrf_exempt
@ratelimit(key='ip', rate='5/h', method='POST', block=True)
def change_password_otp(request, data: ChangePasswordOTPRequest):
    """
    Change password using temporary reset token.
    
    Rate limited to 5 requests per hour per IP.
    """
    try:
        # Find user
        user = User.objects.get(username=data.username, is_active=True)
        
        # Find active reset token
        token_instance = ForgotPasswordToken.objects.filter(
            user=user,
            token=data.reset_token,
            is_used=False
        ).first()
        
        if not token_instance:
            return 401, {
                'error': 'Invalid token',
                'detail': 'Érvénytelen vagy nem létező reset token.'
            }
        
        # Check if token is expired
        if token_instance.is_expired():
            return 400, {
                'error': 'Token expired',
                'detail': 'A reset token lejárt. Kérjük kezdje újra a jelszó visszaállítási folyamatot.'
            }
        
        # Validate new password (basic validation)
        if len(data.new_password) < 6:
            return 400, {
                'error': 'Weak password',
                'detail': 'A jelszónak legalább 6 karakter hosszúnak kell lennie.'
            }
        
        # Change password
        user.set_password(data.new_password)
        user.save()
        
        # Mark token as used
        token_instance.is_used = True
        token_instance.save()
        
        # Invalidate all other tokens for this user
        ForgotPasswordToken.objects.filter(user=user, is_used=False).update(is_used=True)
        
        # Send confirmation email
        logger.debug(f"[VIEWS DEBUG] Calling send_password_changed_notification for user {user.username}")
        notification_sent = send_password_changed_notification(user)
        
        if notification_sent:
            logger.debug(f"[VIEWS DEBUG] Password change notification sent successfully to {user.username}")
        else:
            logger.warning(f"[VIEWS WARNING] Password changed but notification email failed for {user.username}")
        
        logger.info(f"Password changed successfully for user {user.username}")
        return 200, {
            'message': 'Jelszó sikeresen megváltoztatva. Most már bejelentkezhet az új jelszavával.',
            'success': True
        }
        
    except User.DoesNotExist:
        return 400, {
            'error': 'User not found',
            'detail': 'Felhasználó nem található.'
        }
    except Exception as e:
        logger.error(f"Error in change_password_otp: {str(e)}")
        return 400, {
            'error': 'Server error',
            'detail': 'Hiba történt a jelszó megváltoztatása során.'
        }


# Diakjaim Endpoints (Ofő only)

@api.get("/diakjaim", response={200: List[DiakjaSignleSchema], 401: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Diakjaim"])
def get_diakjaim(request):
    """
    Get students from the teacher's class with their igazolások records.
    
    Requires authentication. Only class teachers (ofő) can access this endpoint.
    """
    # Check if user is a class teacher
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers (ofő) can access this endpoint'
        }
    
    # Get the teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'No class found for this teacher'
        }
    
    # Get all students in the class
    students = teacher_class.tanulok.all()
    result = []
    
    for student in students:
        # Get student's profile and igazolások
        try:
            profile = Profile.objects.get(user=student)
            igazolasok = Igazolas.objects.filter(profile=profile).select_related('tipus').order_by('-rogzites_datuma')
        except Profile.DoesNotExist:
            # Create profile if it doesn't exist
            profile = Profile.objects.create(user=student)
            igazolasok = Igazolas.objects.none()
        
        # Build igazolások list
        igazolasok_data = []
        for igazolas in igazolasok:
            igazolasok_data.append({
                'id': igazolas.id,
                'eleje': igazolas.eleje,
                'vege': igazolas.vege,
                'tipus': {
                    'id': igazolas.tipus.id,
                    'nev': igazolas.tipus.nev,
                    'leiras': igazolas.tipus.leiras,
                    'beleszamit': igazolas.tipus.beleszamit,
                    'iskolaerdeku': igazolas.tipus.iskolaerdeku
                },
                'allapot': igazolas.allapot,
                'rogzites_datuma': igazolas.rogzites_datuma,
                'megjegyzes_diak': igazolas.megjegyzes_diak,
                'bkk_verification': igazolas.bkk_verification
            })
        
        student_data = {
            'id': student.id,
            'username': student.username,
            'first_name': student.first_name,
            'last_name': student.last_name,
            'email': student.email,
            'igazolasok': igazolasok_data
        }
        result.append(student_data)
    
    return 200, result


@api.post("/diakjaim", response={201: DiakjaCreateResponse, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Diakjaim"])
def create_diakjaim(request, data: List[DiakjaCreateRequest]):
    """
    Create multiple students and assign them to the teacher's class.
    
    Requires authentication. Only class teachers (ofő) can access this endpoint.
    Expects a list of users with last_name, first_name, and email.
    """
    # Check if user is a class teacher
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers (ofő) can access this endpoint'
        }
    
    # Get the teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'No class found for this teacher'
        }
    
    if not data:
        return 400, {
            'error': 'Bad request',
            'detail': 'No student data provided'
        }
    
    created_count = 0
    failed_users = []
    
    # Process each student in a separate transaction to avoid long-running locks
    for student_data in data:
        try:
            # Generate username from email (part before @)
            username = student_data.email.split('@')[0]
            
            # Check if user already exists (outside transaction for faster checks)
            if User.objects.filter(username=username).exists():
                failed_users.append(f"{student_data.first_name} {student_data.last_name} - username '{username}' already exists")
                continue
            
            if User.objects.filter(email=student_data.email).exists():
                failed_users.append(f"{student_data.first_name} {student_data.last_name} - email already exists")
                continue
            
            # Create user, profile, and add to class in a single transaction
            with transaction.atomic():
                # Create user
                user = User.objects.create_user(
                    username=username,
                    email=student_data.email,
                    first_name=student_data.first_name,
                    last_name=student_data.last_name,
                    password=f"{student_data.last_name.lower()}{student_data.first_name.lower()}123"  # Default password
                )
                
                # Create profile
                Profile.objects.create(user=user)
                
                # Add to class
                teacher_class.tanulok.add(user)
            
            created_count += 1
            
        except Exception as e:
            failed_users.append(f"{student_data.first_name} {student_data.last_name} - {str(e)}")
    
    return 201, {
        'created_count': created_count,
        'failed_users': failed_users,
        'message': f'Successfully created {created_count} students. {len(failed_users)} failed.'
    }


# FTV Sync Endpoints

@api.post("/sync/ftv", response={200: dict, 500: ErrorResponse}, auth=jwt_auth, tags=["FTV Sync"])
def manual_ftv_sync(request):
    """
    Manually trigger FTV sync (requires authentication).
    
    This endpoint can be called by admins to force a sync with FTV.
    Normally, sync happens automatically when /igazolas or /igazolas/my is called.
    """
    try:
        logger.info(f"Manual FTV sync triggered by user {request.auth.username}")
        sync_stats = sync_with_ftv()
        
        return 200, {
            'success': True,
            'message': 'FTV sync completed successfully',
            'statistics': sync_stats
        }
    except FTVSyncError as e:
        logger.error(f"Manual FTV sync failed: {str(e)}")
        return 500, {
            'error': 'Sync failed',
            'detail': str(e)
        }
    except Exception as e:
        logger.error(f"Unexpected error during manual FTV sync: {str(e)}")
        return 500, {
            'error': 'Server error',
            'detail': 'An unexpected error occurred during sync'
        }
