from ninja import NinjaAPI, Body
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone
from django_ratelimit.decorators import ratelimit
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from django.conf import settings
from typing import List, Optional
from datetime import datetime, timedelta
from pathlib import Path
import logging
import requests

from .models import (
    Profile, Osztaly, Mulasztas, IgazolasTipus, Igazolas,
    PasswordResetOTP, ForgotPasswordToken, SystemMessage,
    TanitasiSzunet, Override, PermissionChangeLog
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
    ChangePasswordOTPResponse, ToggleIgazolasTipusRequest, ToggleIgazolasTipusResponse,
    SystemMessageSchema, TanitasiSzunetSchema, OverrideSchema, TanevRendjeSchema,
    TanitasiSzunetCreateRequest, TanitasiSzunetUpdateRequest,
    OverrideCreateRequest, OverrideUpdateRequest, SuperuserCheckResponse,
    # Admin Phase 1 schemas
    GeneratePasswordResponse, ResetPasswordRequest, ResetPasswordResponse,
    TeacherAssignmentRequest, AssignTeacherResponse, RemoveTeacherResponse,
    MoveOsztalyfonokRequest, MoveOsztalyfonokResponse, GetTeachersResponse,
    PromoteDemoteResponse, UserPermissionsResponse, LoginStatsResponse,
    # Admin Phase 2 schemas
    ActivityHeatmapResponse, ClassesOverviewResponse, TeacherWorkloadResponse,
    TeacherActivityResponse, ApprovalRatesResponse,
    # Mulasztas upload schemas (EXPERIMENTAL)
    MulasztasUploadSchema, MulasztasAnalysisResult, UploadMulasztasResponse,
    # Group Absences & Period Config schemas
    EligibleStudentsResponse, GroupIgazolasCreateResponse, GroupMembersResponse,
    PeriodConfigResponse, PeriodUsageAnalysisResponse,
    # Academic Year & Bulk Operations schemas
    ArchiveResponse, BulkCreateStudentsResponse, CreateClassResponse,
    # New feature schemas
    APIMetricsResponse, APIMetricsRefreshResponse,
    AttendanceCreateRequest, AttendanceUpdateRequest, AttendanceResponse, StudentAttendanceResponse,
    PermissionMatrixResponse, UpdatePermissionRequest, UpdatePermissionResponse,
    BulkUpdatePermissionsRequest, BulkUpdatePermissionsResponse,
    AssignClassesRequest, TeacherClassesResponse
)
from .jwt_utils import generate_jwt_token, decode_jwt_token
from .authentication import JWTAuth
from .email_utils import (
    send_otp_email, send_password_changed_notification,
    send_password_generated_email, send_permission_change_email
)
from .admin_utils import (
    generate_strong_password, validate_password_strength, is_superuser,
    log_permission_change, get_permission_history, invalidate_user_sessions,
    get_user_full_name, is_teacher, can_remove_teacher_from_class
)
from .ftv_sync import (
    sync_user_absences_from_ftv, 
    sync_class_absences_from_ftv,
    sync_base_from_ftv,
    FTVSyncError, 
    get_cache_metadata
)

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
    
    # Update last_login timestamp
    user.last_login = timezone.now()
    user.save(update_fields=['last_login'])
    
    # Increment login_count in Profile
    profile, created = Profile.objects.get_or_create(user=user)
    profile.login_count += 1
    profile.save(update_fields=['login_count'])
    
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
        
        # Check if user exists in FTV system by email
        ftv_registered = False
        if request.auth.email:
            try:
                from .ftv_sync import fetch_ftv_profile_by_email
                ftv_profile = fetch_ftv_profile_by_email(request.auth.email)
                ftv_registered = ftv_profile is not None
            except Exception as e:
                logger.warning(f"Failed to check FTV registration for {request.auth.username}: {str(e)}")
                # Don't fail the request, just assume not registered
                ftv_registered = False
        
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
            } if osztaly else None,
            'ftv_registered': ftv_registered
    }
    except Profile.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': 'Profile not found for current user'
        }
    
# GET and POST profile/frontendConfig field
@api.get("/profiles/me/frontend-config", response={200: dict, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Profile"])
def get_my_frontend_config(request):
    """Get current user's frontend config (requires authentication)"""
    try:
        profile = Profile.objects.get(user=request.auth)
        return 200, profile.frontendConfig
    except Profile.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': 'Profile not found for current user'
        }

@api.post("/profiles/me/frontend-config", response={200: dict, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Profile"])
def update_my_frontend_config(request, data: dict = Body(...)):
    """Update current user's frontend config (requires authentication)"""
    try:
        profile = Profile.objects.get(user=request.auth)
        profile.frontendConfig = data
        profile.save(update_fields=['frontendConfig'])
        return 200, profile.frontendConfig
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
    osztalyok = Osztaly.objects.all().prefetch_related('nem_fogadott_igazolas_tipusok')
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
            ],
            'nem_fogadott_igazolas_tipusok': [
                {
                    'id': tipus.id,
                    'nev': tipus.nev,
                    'leiras': tipus.leiras,
                    'beleszamit': tipus.beleszamit,
                    'iskolaerdeku': tipus.iskolaerdeku,
                    'nem_fogado_osztalyok': None  # Avoid circular reference
                } for tipus in osztaly.nem_fogadott_igazolas_tipusok.all()
            ]
        }
        result.append(osztaly_data)
    
    return 200, result


@api.get("/osztaly/{osztaly_id}", response={200: OsztalySchema, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Osztaly"])
def get_osztaly(request, osztaly_id: int):
    """Get class by ID (requires authentication)"""
    osztaly = get_object_or_404(Osztaly.objects.prefetch_related('nem_fogadott_igazolas_tipusok'), id=osztaly_id)
    
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
        ],
        'nem_fogadott_igazolas_tipusok': [
            {
                'id': tipus.id,
                'nev': tipus.nev,
                'leiras': tipus.leiras,
                'beleszamit': tipus.beleszamit,
                'iskolaerdeku': tipus.iskolaerdeku,
                'nem_fogado_osztalyok': None  # Avoid circular reference
            } for tipus in osztaly.nem_fogadott_igazolas_tipusok.all()
        ]
    }


# Mulasztas Endpoints

@api.get("/mulasztas", response={200: List[MulasztasSchema], 401: ErrorResponse}, auth=jwt_auth, tags=["Mulasztas"])
def list_mulasztas(request):
    """Get all absences (requires authentication)"""
    mulasztasok = Mulasztas.objects.all()
    return 200, list(mulasztasok)


@api.post("/mulasztas/upload-ekreta", response={200: UploadMulasztasResponse, 400: ErrorResponse, 401: ErrorResponse}, auth=jwt_auth, tags=["Mulasztas - EXPERIMENTAL"])
def upload_ekreta_xlsx(request):
    """
    Upload eKréta XLSX export and create/update Mulasztas records for the student.
    
    EXPERIMENTAL FEATURE - Students can upload their eKréta attendance export.
    The system will:
    1. Parse the XLSX file
    2. Create/update Mulasztas records for the student
    3. Analyze which mulasztások are covered by existing igazolások
    4. Return analysis results
    
    Expected XLSX columns (based on eKréta export format):
    - Mulasztás dátuma (column 0)
    - Óraszám (column 1)
    - Tárgy (column 2)
    - Téma (column 3)
    - Mulasztás típusa (column 4)
    - Igazolt (column 5) - "Igen" or "Nem"
    - Tanórai célú mulasztás (column 6) - "Igen" or "Nem"
    - Igazolás típusa (column 7)
    - Rögzítés dátuma (column 8)
    - Ok (column 9) - optional
    - Státusz (column 10) - optional
    
    Requires authentication. Only students can upload their own records.
    These records are ONLY visible to the student who uploaded them (NOT to teachers).
    """
    if 'file' not in request.FILES:
        return 400, {
            'error': 'No file uploaded',
            'detail': 'Az XLSX fájl hiányzik. Kérjük töltse fel az eKréta export fájlt.'
        }
    
    xlsx_file = request.FILES['file']
    
    # Validate file extension
    if not xlsx_file.name.endswith(('.xlsx', '.xls')):
        return 400, {
            'error': 'Invalid file type',
            'detail': 'Csak .xlsx vagy .xls fájlokat fogadunk el.'
        }
    
    try:
        import openpyxl
        from datetime import datetime as dt
        
        # Load workbook
        workbook = openpyxl.load_workbook(xlsx_file)
        sheet = workbook.active
        
        # Parse all rows (skip header)
        rows = []
        for row in sheet.iter_rows(min_row=2):  # Skip header row
            rows.append([cell.value for cell in row])
        
        created_count = 0
        updated_count = 0
        error_count = 0
        errors = []
        
        # Process each row
        for idx, row in enumerate(rows, start=2):  # Start at 2 because row 1 is header
            try:
                # Skip empty rows
                if not row[0]:  # If date is empty, skip
                    continue
                
                # Parse date (handle Hungarian eKréta format: "2025. 11. 17.")
                if isinstance(row[0], dt):
                    mulasztas_datuma = row[0].date()
                elif isinstance(row[0], str):
                    # Remove extra spaces and try various formats
                    date_str = row[0].strip().replace('. ', '.').rstrip('.')
                    try:
                        # Try YYYY.MM.DD format (Hungarian eKréta: "2025. 11. 17.")
                        mulasztas_datuma = dt.strptime(date_str, '%Y.%m.%d').date()
                    except ValueError:
                        try:
                            # Try YYYY-MM-DD format
                            mulasztas_datuma = dt.strptime(row[0], '%Y-%m-%d').date()
                        except ValueError:
                            try:
                                # Try DD.MM.YYYY format
                                mulasztas_datuma = dt.strptime(date_str, '%d.%m.%Y').date()
                            except ValueError:
                                errors.append(f"Row {idx}: Invalid date format '{row[0]}'")
                                error_count += 1
                                continue
                else:
                    mulasztas_datuma = row[0]
                
                # Parse óraszám (lesson number)
                try:
                    oraszam = int(row[1]) if row[1] is not None else 0
                except (ValueError, TypeError):
                    errors.append(f"Row {idx}: Invalid óraszám '{row[1]}'")
                    error_count += 1
                    continue
                
                # Parse boolean fields
                igazolt = row[5] in ['Igen', 'igen', 'IGEN', True, 1] if row[5] else False
                tanorai_celu = row[6] in ['Igen', 'igen', 'IGEN', True, 1] if row[6] else False
                
                # Parse rögzítés dátuma (as date, not datetime for Mulasztas model)
                # Handle Hungarian eKréta format: "2025. 11. 17."
                if isinstance(row[8], dt):
                    rogzites_datuma = row[8].date() if hasattr(row[8], 'date') else row[8]
                elif isinstance(row[8], str) and row[8]:
                    date_str = row[8].strip().replace('. ', '.').rstrip('.')
                    try:
                        # Try YYYY.MM.DD format (Hungarian eKréta: "2025. 11. 17.")
                        rogzites_datuma = dt.strptime(date_str, '%Y.%m.%d').date()
                    except ValueError:
                        try:
                            # Try YYYY-MM-DD format
                            rogzites_datuma = dt.strptime(row[8], '%Y-%m-%d').date()
                        except ValueError:
                            try:
                                # Try DD.MM.YYYY format
                                rogzites_datuma = dt.strptime(date_str, '%d.%m.%Y').date()
                            except ValueError:
                                # Default to today if parsing fails
                                rogzites_datuma = timezone.now().date()
                else:
                    rogzites_datuma = timezone.now().date()
                
                # Check if record exists (for this student only)
                existing = Mulasztas.objects.filter(
                    uploaded_by_student=request.auth,
                    datum=mulasztas_datuma,
                    ora=oraszam
                ).first()
                
                # Prepare data
                mulasztas_data = {
                    'uploaded_by_student': request.auth,
                    'datum': mulasztas_datuma,
                    'ora': oraszam,
                    'tantargy': str(row[2])[:100] if row[2] else '',
                    'tema': str(row[3])[:200] if row[3] else '',
                    'tipus': str(row[4])[:50] if row[4] else '',
                    'igazolt': igazolt,
                    'tanorai_celu_mulasztas': tanorai_celu,
                    'igazolas_tipusa': str(row[7])[:100] if row[7] else None,
                    'rogzites_datuma': rogzites_datuma,
                    'mulasztas_ok': str(row[9])[:300] if len(row) > 9 and row[9] else None,
                    'mulasztas_statusz': str(row[10])[:200] if len(row) > 10 and row[10] else None,
                    'uploaded_at': timezone.now(),
                }
                
                if existing:
                    # Update existing record
                    for key, value in mulasztas_data.items():
                        setattr(existing, key, value)
                    existing.save()
                    updated_count += 1
                else:
                    # Create new record
                    Mulasztas.objects.create(**mulasztas_data)
                    created_count += 1
                    
            except Exception as e:
                errors.append(f"Row {idx}: {str(e)}")
                error_count += 1
                logger.error(f"Error processing row {idx}: {str(e)}")
        
        # Perform analysis: compare student's Mulasztas with Igazolas
        analysis = analyze_mulasztas_coverage(request.auth)
        
        logger.info(f"User {request.auth.username} uploaded eKréta XLSX: {created_count} created, {updated_count} updated, {error_count} errors")
        
        return 200, {
            'success': True,
            'message': f'Feldolgozva {created_count + updated_count} rekord. Létrehozva: {created_count}, Frissítve: {updated_count}, Hibák: {error_count}',
            'total_processed': created_count + updated_count,
            'created_count': created_count,
            'updated_count': updated_count,
            'error_count': error_count,
            'errors': errors[:10],  # Return max 10 errors to avoid huge response
            'analysis': analysis
        }
        
    except ImportError:
        return 400, {
            'error': 'Missing dependency',
            'detail': 'Az openpyxl könyvtár nincs telepítve. Kérjük vegye fel a kapcsolatot az adminisztrátorral.'
        }
    except Exception as e:
        logger.error(f"Error processing XLSX upload: {str(e)}")
        import traceback
        traceback.print_exc()
        return 400, {
            'error': 'Processing error',
            'detail': f'Hiba történt a fájl feldolgozása során: {str(e)}'
        }


@api.get("/mulasztas/my", response={200: MulasztasAnalysisResult, 401: ErrorResponse}, auth=jwt_auth, tags=["Mulasztas - EXPERIMENTAL"])
def get_my_mulasztas(request, include_igazolt: bool = False):
    """
    Get current user's uploaded Mulasztas records with analysis.
    
    EXPERIMENTAL FEATURE - Returns student's uploaded eKréta attendance records
    with analysis of coverage by existing igazolások.
    
    Args:
        include_igazolt: If True, includes already justified absences. Default is False.
    
    Requires authentication. Students can only see their own records.
    """
    analysis = analyze_mulasztas_coverage(request.auth, include_igazolt=include_igazolt)
    return 200, analysis


@api.delete("/mulasztas/my", response={200: dict, 401: ErrorResponse}, auth=jwt_auth, tags=["Mulasztas - EXPERIMENTAL"])
def delete_my_mulasztas(request):
    """
    Delete all student-uploaded Mulasztas records for the current user.
    
    EXPERIMENTAL FEATURE - Allows students to clear their uploaded eKréta data.
    
    Requires authentication. Students can only delete their own records.
    """
    deleted_count = Mulasztas.objects.filter(uploaded_by_student=request.auth).delete()[0]
    logger.info(f"User {request.auth.username} deleted {deleted_count} student-uploaded Mulasztas records")
    
    return 200, {
        'message': f'{deleted_count} mulasztás rekord törölve.',
        'deleted_count': deleted_count
    }


@api.get("/mulasztas/{mulasztas_id}", response={200: MulasztasSchema, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Mulasztas"])
def get_mulasztas(request, mulasztas_id: int):
    """Get absence by ID (requires authentication)"""
    mulasztas = get_object_or_404(Mulasztas, id=mulasztas_id)
    return 200, mulasztas


# IgazolasTipus Endpoints

@api.get("/igazolas-tipus", response={200: List[IgazolasTipusSchema], 401: ErrorResponse}, auth=jwt_auth, tags=["IgazolasTipus"])
def list_igazolas_tipus(request):
    """Get all justification types (requires authentication)"""
    tipusok = IgazolasTipus.objects.all().prefetch_related('nem_fogado_osztalyok')
    result = []
    
    for tipus in tipusok:
        nem_fogado_osztalyok = [
            {
                'id': osztaly.id,
                'tagozat': osztaly.tagozat,
                'kezdes_eve': osztaly.kezdes_eve,
                'nev': str(osztaly)
            }
            for osztaly in tipus.nem_fogado_osztalyok.all()
        ]
        
        tipus_data = {
            'id': tipus.id,
            'nev': tipus.nev,
            'leiras': tipus.leiras,
            'beleszamit': tipus.beleszamit,
            'iskolaerdeku': tipus.iskolaerdeku,
            'nem_fogado_osztalyok': nem_fogado_osztalyok,
            'category': tipus.category,
            'category_emoji': tipus.category_emoji,
            'has_sub_form': tipus.has_sub_form,
            'sub_form_schema': tipus.sub_form_schema,
            'display_order': tipus.display_order,
            'supports_group_absence': tipus.supports_group_absence,
            'requires_studios': tipus.requires_studios
        }
        result.append(tipus_data)
    
    return 200, result


@api.get("/igazolas-tipus/categorized", response={200: dict, 401: ErrorResponse}, auth=jwt_auth, tags=["IgazolasTipus"])
def get_categorized_igazolas_types(request):
    """
    Get all igazolás types grouped by category.
    
    Returns types organized by category with metadata for UI rendering.
    """
    # Get user's class for filtering
    profile, _ = Profile.objects.get_or_create(user=request.auth)
    osztaly = profile.osztalyom()
    
    # Get all types, excluding those disabled for this class
    all_types = IgazolasTipus.objects.all()
    if osztaly:
        all_types = all_types.exclude(nem_fogado_osztalyok=osztaly)
    
    # Group by category
    categories = {}
    for tipus in all_types:
        cat = tipus.category
        if cat not in categories:
            categories[cat] = {
                'name': tipus.get_category_display(),
                'emoji': tipus.category_emoji or get_default_emoji(cat),
                'types': []
            }
        
        categories[cat]['types'].append({
            'id': tipus.id,
            'nev': tipus.nev,
            'leiras': tipus.leiras,
            'beleszamit': tipus.beleszamit,
            'iskolaerdeku': tipus.iskolaerdeku,
            'supports_group_absence': tipus.supports_group_absence,
            'requires_studios': tipus.requires_studios,
            'has_sub_form': tipus.has_sub_form,
            'sub_form_schema': tipus.sub_form_schema,
            'display_order': tipus.display_order,
            'category': tipus.category,
            'category_emoji': tipus.category_emoji,
        })
    
    # Sort categories and types within
    sorted_categories = []
    category_order = ['egeszsegugy', 'verseny', 'kulturalis', 'kozlekedes', 
                     'tanulmanyi', 'csaladi', 'egyeb']
    
    for cat_key in category_order:
        if cat_key in categories:
            cat_data = categories[cat_key]
            cat_data['types'].sort(key=lambda t: (t['display_order'], t['nev']))
            sorted_categories.append({
                'key': cat_key,
                **cat_data
            })
    
    return 200, {
        'categories': sorted_categories,
        'total_types': sum(len(c['types']) for c in sorted_categories)
    }


def get_default_emoji(category: str) -> str:
    """Get default emoji for category if not set in model."""
    emoji_map = {
        'egeszsegugy': '🏥',
        'verseny': '🏆',
        'kulturalis': '🎭',
        'kozlekedes': '🚌',
        'tanulmanyi': '📚',
        'csaladi': '👨‍👩‍👧',
        'egyeb': '⚙️',
    }
    return emoji_map.get(category, '📝')


@api.get("/igazolas-tipus/{tipus_id}", response={200: IgazolasTipusSchema, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["IgazolasTipus"])
def get_igazolas_tipus(request, tipus_id: int):
    """Get justification type by ID (requires authentication)"""
    tipus = get_object_or_404(IgazolasTipus.objects.prefetch_related('nem_fogado_osztalyok'), id=tipus_id)
    
    nem_fogado_osztalyok = [
        {
            'id': osztaly.id,
            'tagozat': osztaly.tagozat,
            'kezdes_eve': osztaly.kezdes_eve,
            'nev': str(osztaly)
        }
        for osztaly in tipus.nem_fogado_osztalyok.all()
    ]
    
    return 200, {
        'id': tipus.id,
        'nev': tipus.nev,
        'leiras': tipus.leiras,
        'beleszamit': tipus.beleszamit,
        'iskolaerdeku': tipus.iskolaerdeku,
        'nem_fogado_osztalyok': nem_fogado_osztalyok,
        'category': tipus.category,
        'category_emoji': tipus.category_emoji,
        'has_sub_form': tipus.has_sub_form,
        'sub_form_schema': tipus.sub_form_schema,
        'display_order': tipus.display_order,
        'supports_group_absence': tipus.supports_group_absence,
        'requires_studios': tipus.requires_studios
    }


@api.put("/osztaly/igazolas-tipus/toggle", response={200: ToggleIgazolasTipusResponse, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["IgazolasTipus"])
def toggle_igazolas_tipus_for_osztaly(request, data: ToggleIgazolasTipusRequest):
    """
    Enable or disable a specific igazolas tipus for the teacher's own class.
    
    Requires authentication. Only class teachers (ofő) can access this endpoint.
    When enabled=True, the tipus is accepted (removed from nem_fogadott_igazolas_tipusok).
    When enabled=False, the tipus is not accepted (added to nem_fogadott_igazolas_tipusok).
    """
    # Check if user is a class teacher
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers (ofő) can modify igazolas tipus settings'
        }
    
    # Get the teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'No class found for this teacher'
        }
    
    # Verify tipus exists
    try:
        tipus = IgazolasTipus.objects.get(id=data.tipus_id)
    except IgazolasTipus.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'IgazolasTipus with id {data.tipus_id} does not exist'
        }
    
    # Toggle the tipus
    if data.enabled:
        # Enable: remove from nem_fogadott_igazolas_tipusok
        teacher_class.nem_fogadott_igazolas_tipusok.remove(tipus)
        message = f'Igazolas tipus "{tipus.nev}" is now accepted for class {teacher_class}'
    else:
        # Disable: add to nem_fogadott_igazolas_tipusok
        teacher_class.nem_fogadott_igazolas_tipusok.add(tipus)
        message = f'Igazolas tipus "{tipus.nev}" is now NOT accepted for class {teacher_class}'
    
    logger.info(f"Teacher {request.auth.username} toggled tipus {tipus.nev} (ID: {tipus.id}) to {'enabled' if data.enabled else 'disabled'} for class {teacher_class}")
    
    return 200, {
        'message': message,
        'success': True,
        'tipus_id': tipus.id,
        'enabled': data.enabled
    }


# Igazolas Endpoints

@api.get("/igazolas", response={200: List[IgazolasSchema], 401: ErrorResponse}, auth=jwt_auth, tags=["Igazolas"])
def list_igazolas(request, mode: str = "live", debug_performance: str = "false"):
    """
    Get all justifications (requires authentication).
    
    This endpoint syncs with FTV based on the mode parameter.
    Only accessible by osztályfőnök (class teachers).
    
    Args:
        mode: 'cached' to return stored data without sync (fast), 
              'live' to sync with FTV first (default, slower but fresh)
        debug_performance: 'true' to fetch and log performance details from FTV backend
    """
    print(f"\n{'='*100}")
    print(f"🌐 API ENDPOINT: /igazolas")
    print(f"   User: {request.auth.username} (ID: {request.auth.id})")
    print(f"   Mode: {mode}")
    print(f"   Debug Performance: {debug_performance}")
    print(f"{'='*100}\n")
    
    # Convert debug_performance string to boolean
    debug_perf = debug_performance.lower() in ('true', '1', 'yes')
    
    # Determine if we should print performance (dev mode only)
    should_print_perf = debug_perf and settings.DEBUG
    
    # Get teacher's class
    print(f"👨‍🏫 Checking teacher profile and class...")
    teacher_profile = Profile.objects.filter(user=request.auth).first()
    if not teacher_profile:
        print(f"   ✗ ERROR: No profile found for user\n")
        return 401, {
            'error': 'Unauthorized',
            'detail': 'No profile found for user'
        }
    
    print(f"   ✓ Profile found: ID={teacher_profile.id}")
    teacher_class = teacher_profile.osztalyom()
    if not teacher_class:
        print(f"   ✗ ERROR: No class found for this teacher\n")
        return 401, {
            'error': 'Unauthorized',
            'detail': 'No class found for this teacher'
        }
    
    print(f"   ✓ Teacher's class: {teacher_class} (ID: {teacher_class.id})\n")
    
    # Sync with FTV only if mode is 'live'
    sync_result = None
    if mode == "live":
        print(f"🔄 MODE=LIVE: Triggering FTV sync...")
        try:
            logger.info(f"User {request.auth.username} requested /igazolas - triggering class-specific FTV sync")
            sync_result = sync_class_absences_from_ftv(teacher_class, debug_performance=debug_perf)
            print(f"✅ FTV Sync completed successfully")
            print(f"   Stats: {sync_result.get('statistics')}\n")
            logger.info(f"FTV sync completed: {sync_result.get('statistics')}")
            
            # Print performance details in dev mode
            if should_print_perf and sync_result.get('ftv_performance'):
                print(f"📊 Performance Details: {sync_result['ftv_performance']}\n")
                logger.info(f"FTV Performance Details: {sync_result['ftv_performance']}")
        except FTVSyncError as e:
            print(f"❌ FTV sync failed: {str(e)}")
            print(f"   → Continuing with existing data\n")
            logger.error(f"FTV sync failed but continuing with existing data: {str(e)}")
        except Exception as e:
            print(f"❌ Unexpected error during FTV sync: {str(e)}")
            print(f"   → Continuing with existing data\n")
            import traceback
            traceback.print_exc()
            logger.error(f"Unexpected error during FTV sync: {str(e)}")
    else:
        print(f"💾 MODE=CACHED: Skipping FTV sync\n")
        logger.info(f"User {request.auth.username} requested /igazolas in cached mode - skipping FTV sync")
    
    # Get cache metadata to include in response headers or logging
    cache_metadata = get_cache_metadata(f'class_{teacher_class.id}')
    print(f"💾 Cache metadata: {cache_metadata}\n")
    logger.info(f"Cache metadata: {cache_metadata}")
    
    # Fetch igazolások for the teacher's class
    igazolasok = teacher_profile.osztalyom_igazolasai().select_related('profile', 'tipus').prefetch_related('mulasztasok')
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
    
    print(f"📤 RESPONSE DATA:")
    print(f"   Total igazolások: {len(result)}")
    ftv_count = sum(1 for i in result if i.get('ftv'))
    print(f"   FTV igazolások: {ftv_count}")
    print(f"   Non-FTV igazolások: {len(result) - ftv_count}")
    if result:
        print(f"   Sample (first record): eleje={result[0].get('eleje')}, vege={result[0].get('vege')}, ftv={result[0].get('ftv')}")
    print(f"{'='*100}\n")
    
    # Add cache metadata to response (Ninja doesn't support custom headers easily, so we log it)
    # The frontend can make a separate call to get metadata if needed
    return 200, result


@api.get("/igazolas/my", response={200: List[IgazolasSchema], 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Igazolas"])
def get_my_igazolas(request, mode: str = "live", debug_performance: str = "false"):
    """
    Get current user's justifications (requires authentication).
    
    This endpoint syncs with FTV based on the mode parameter.
    Students can see their own records here.
    
    Args:
        mode: 'cached' to return stored data without sync (fast), 
              'live' to sync with FTV first (default, slower but fresh)
        debug_performance: 'true' to fetch and log performance details from FTV backend
    """
    print(f"\n{'='*100}")
    print(f"🌐 API ENDPOINT: /igazolas/my")
    print(f"   User: {request.auth.username} (ID: {request.auth.id})")
    print(f"   Email: {request.auth.email}")
    print(f"   Mode: {mode}")
    print(f"   Debug Performance: {debug_performance}")
    print(f"{'='*100}\n")
    
    # Convert debug_performance string to boolean
    debug_perf = debug_performance.lower() in ('true', '1', 'yes')
    
    # Determine if we should print performance (dev mode only)
    should_print_perf = debug_perf and settings.DEBUG
    
    # Check if user has email for FTV lookup
    if not request.auth.email:
        print(f"⚠️  WARNING: User has no email - cannot sync with FTV")
        print(f"   → Switching to 'cached' mode\n")
        logger.warning(f"User {request.auth.username} has no email - cannot sync with FTV")
        # Continue without sync
        mode = "cached"
    
    # Sync with FTV only if mode is 'live' and user has email
    sync_result = None
    if mode == "live" and request.auth.email:
        print(f"🔄 MODE=LIVE: Triggering FTV sync...")
        try:
            logger.info(f"User {request.auth.username} requested /igazolas/my - triggering user-specific FTV sync")
            sync_result = sync_user_absences_from_ftv(request.auth, debug_performance=debug_perf)
            print(f"✅ FTV Sync completed successfully")
            print(f"   Stats: {sync_result.get('statistics')}\n")
            logger.info(f"FTV sync completed: {sync_result.get('statistics')}")
            
            # Print performance details in dev mode
            if should_print_perf and sync_result.get('ftv_performance'):
                print(f"📊 Performance Details: {sync_result['ftv_performance']}\n")
                logger.info(f"FTV Performance Details: {sync_result['ftv_performance']}")
        except FTVSyncError as e:
            print(f"❌ FTV sync failed: {str(e)}")
            print(f"   → Continuing with existing data\n")
            logger.error(f"FTV sync failed but continuing with existing data: {str(e)}")
        except Exception as e:
            print(f"❌ Unexpected error during FTV sync: {str(e)}")
            print(f"   → Continuing with existing data\n")
            import traceback
            traceback.print_exc()
            logger.error(f"Unexpected error during FTV sync: {str(e)}")
    elif mode == "cached":
        print(f"💾 MODE=CACHED: Skipping FTV sync\n")
        logger.info(f"User {request.auth.username} requested /igazolas/my in cached mode - skipping FTV sync")
    else:
        print(f"⚠️  No sync triggered (mode={mode}, has_email={bool(request.auth.email)})\n")
    
    # Get cache metadata to include in response headers or logging
    cache_metadata = get_cache_metadata(f'user_{request.auth.id}')
    logger.info(f"Cache metadata: {cache_metadata}")
    
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
        
        print(f"📤 RESPONSE DATA:")
        print(f"   Total igazolások: {len(result)}")
        ftv_count = sum(1 for i in result if i.get('ftv'))
        print(f"   FTV igazolások: {ftv_count}")
        print(f"   Non-FTV igazolások: {len(result) - ftv_count}")
        if result:
            print(f"   Sample (first record): eleje={result[0].get('eleje')}, vege={result[0].get('vege')}, ftv={result[0].get('ftv')}")
        print(f"{'='*100}\n")
        
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
        bkk_verification=data.bkk_verification,
        sub_form_data=data.sub_form_data  # New field
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
def forgot_password(request, data: ForgotPasswordRequest):
    """
    Request password reset by sending OTP to user's email.
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
# @ratelimit(key='user_or_ip', rate='10/h', method='POST', block=True)
def check_otp(request, data: CheckOTPRequest):
    """
    Verify OTP code and return temporary reset token.

    Rate limited to 10 requests per hour per user or IP.
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
def change_password_otp(request, data: ChangePasswordOTPRequest):
    """
    Change password using temporary reset token.
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
    
    # Get all students in the class (prefetch to ensure fresh data)
    students = teacher_class.tanulok.all().order_by('last_name', 'first_name')
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
            'last_action': student.last_login,
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

@api.get("/sync/ftv/check-registration", response={200: dict}, auth=jwt_auth, tags=["FTV Sync"])
def check_ftv_registration(request):
    """
    Check if the current user is registered in the FTV system.
    
    This endpoint is used by the frontend to determine if FTV-related
    features should be displayed or if sync is possible.
    
    Returns:
        - ftv_registered: bool - Whether user exists in FTV
        - email: str - User's email (used for FTV lookup)
        - message: str - Human-readable status message
    """
    if not request.auth.email:
        return 200, {
            'ftv_registered': False,
            'email': None,
            'message': 'User has no email address - cannot check FTV registration'
        }
    
    try:
        from .ftv_sync import fetch_ftv_profile_by_email
        ftv_profile = fetch_ftv_profile_by_email(request.auth.email)
        
        if ftv_profile:
            return 200, {
                'ftv_registered': True,
                'email': request.auth.email,
                'ftv_user_id': ftv_profile.get('user_id'),
                'message': f'User is registered in FTV system (FTV ID: {ftv_profile.get("user_id")})'
            }
        else:
            return 200, {
                'ftv_registered': False,
                'email': request.auth.email,
                'message': 'User is not registered in FTV system - no filming absences available'
            }
    except Exception as e:
        logger.error(f"Error checking FTV registration for {request.auth.username}: {str(e)}")
        return 200, {
            'ftv_registered': False,
            'email': request.auth.email,
            'message': 'Unable to check FTV registration - FTV API may be unavailable',
            'error': str(e)
        }


@api.get("/sync/ftv/metadata", response={200: dict}, auth=jwt_auth, tags=["FTV Sync"])
def get_ftv_sync_metadata(request, sync_type: str = "base"):
    """
    Get FTV sync metadata (last sync time, status, etc.).
    
    This endpoint returns information about the last FTV sync without triggering a new sync.
    Useful for the frontend to display cache freshness information.
    
    Args:
        sync_type: Type of sync to check - 'base', 'user', or 'class' (default: 'base')
                   For 'user' type, returns metadata for the current user
                   For 'class' type, returns metadata for the current user's class
    """
    # Determine the actual sync_type based on the user
    if sync_type == 'user':
        actual_sync_type = f'user_{request.auth.id}'
    elif sync_type == 'class':
        teacher_profile = Profile.objects.filter(user=request.auth).first()
        if teacher_profile and teacher_profile.osztalyom():
            actual_sync_type = f'class_{teacher_profile.osztalyom().id}'
        else:
            actual_sync_type = 'base'
    else:
        actual_sync_type = 'base'
    
    metadata = get_cache_metadata(actual_sync_type)
    return 200, {
        'success': True,
        'sync_type': sync_type,
        'metadata': metadata
    }


@api.post("/sync/ftv", response={200: dict, 500: ErrorResponse}, auth=jwt_auth, tags=["FTV Sync"])
def manual_ftv_sync(request, debug_performance: str = "false"):
    """
    Manually trigger FTV base sync (requires authentication).
    
    This endpoint performs a lightweight base sync (classes + students only).
    For full sync with absences, the system will use user-specific or class-specific endpoints.
    
    Args:
        debug_performance: 'true' to fetch and log performance details from FTV backend
    """
    # Convert debug_performance string to boolean
    debug_perf = debug_performance.lower() in ('true', '1', 'yes')
    
    # Determine if we should print performance (dev mode only)
    should_print_perf = debug_perf and settings.DEBUG
    
    try:
        logger.info(f"Manual FTV base sync triggered by user {request.auth.username}")
        sync_result = sync_base_from_ftv(debug_performance=debug_perf)
        
        # Print performance details in dev mode
        if should_print_perf and sync_result.get('ftv_performance'):
            logger.info(f"FTV Performance Details: {sync_result['ftv_performance']}")
        
        return 200, {
            'success': True,
            'message': 'FTV base sync completed successfully',
            'statistics': sync_result.get('statistics'),
            'metadata': get_cache_metadata('base')
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


# ============================================================================
# System Messages Endpoints (No Authentication Required)
# ============================================================================

@api.get("/system-messages", response={200: List[SystemMessageSchema]}, auth=None, tags=["System Messages"])
def get_all_system_messages(request):
    """
    Get all system messages (no authentication required).
    
    Returns all system messages regardless of their display time window.
    Useful for admin/debugging purposes.
    """
    messages = SystemMessage.objects.all()
    
    return 200, [
        {
            'id': msg.id,
            'title': msg.title,
            'message': msg.message,
            'severity': msg.severity,
            'messageType': msg.messageType,
            'showFrom': msg.showFrom,
            'showTo': msg.showTo,
            'created_at': msg.created_at,
            'updated_at': msg.updated_at,
            'is_active': msg.is_active()
        }
        for msg in messages
    ]


@api.get("/system-messages/active", response={200: List[SystemMessageSchema]}, auth=None, tags=["System Messages"])
def get_active_system_messages(request):
    """
    Get currently active system messages (no authentication required).
    
    Returns only system messages that should be displayed right now
    (current time is between showFrom and showTo).
    """
    active_messages = SystemMessage.get_active_messages()
    
    return 200, [
        {
            'id': msg.id,
            'title': msg.title,
            'message': msg.message,
            'severity': msg.severity,
            'messageType': msg.messageType,
            'showFrom': msg.showFrom,
            'showTo': msg.showTo,
            'created_at': msg.created_at,
            'updated_at': msg.updated_at,
            'is_active': True  # All messages returned here are active
        }
        for msg in active_messages
    ]


# ============================================================================
# Tanév Rendje Endpoints
# ============================================================================

@api.get("/tanev_rendje", response={200: TanevRendjeSchema, 401: ErrorResponse}, auth=jwt_auth, tags=["Tanév Rendje"])
def get_tanev_rendje(request, from_date: str = None, to_date: str = None):
    """
    Get school year schedule including breaks and overrides (requires authentication).
    
    Args:
        from_date: Optional filter - start date in YYYY-MM-DD format
        to_date: Optional filter - end date in YYYY-MM-DD format
    
    Returns:
        Combined data from TanitasiSzunet and Override models
    """
    # Parse date filters if provided
    from datetime import datetime as dt
    
    szunetek_query = TanitasiSzunet.objects.all()
    overrides_query = Override.objects.all()
    
    if from_date:
        try:
            from_date_parsed = dt.strptime(from_date, '%Y-%m-%d').date()
            szunetek_query = szunetek_query.filter(to_date__gte=from_date_parsed)
            overrides_query = overrides_query.filter(date__gte=from_date_parsed)
        except ValueError:
            logger.warning(f"Invalid from_date format: {from_date}")
    
    if to_date:
        try:
            to_date_parsed = dt.strptime(to_date, '%Y-%m-%d').date()
            szunetek_query = szunetek_query.filter(from_date__lte=to_date_parsed)
            overrides_query = overrides_query.filter(date__lte=to_date_parsed)
        except ValueError:
            logger.warning(f"Invalid to_date format: {to_date}")
    
    # Fetch data
    tanitasi_szunetek = szunetek_query.order_by('from_date')
    overrides = overrides_query.select_related('class_id').order_by('date')
    
    # Build response
    szunetek_data = [
        {
            'id': szunet.id,
            'type': szunet.type,
            'name': szunet.name,
            'from_date': szunet.from_date,
            'to_date': szunet.to_date,
            'description': szunet.description
        }
        for szunet in tanitasi_szunetek
    ]
    
    overrides_data = [
        {
            'id': override.id,
            'date': override.date,
            'is_required': override.is_required,
            'class_id': override.class_id.id if override.class_id else None,
            'class_name': str(override.class_id) if override.class_id else None,
            'reason': override.reason
        }
        for override in overrides
    ]
    
    return 200, {
        'tanitasi_szunetek': szunetek_data,
        'overrides': overrides_data
    }


# ============================================================================
# User Info Endpoints
# ============================================================================

@api.get("/am-i-superuser", response={200: SuperuserCheckResponse, 401: ErrorResponse}, auth=jwt_auth, tags=["User Info"])
def am_i_superuser(request):
    """
    Check if the current user is a superuser (requires authentication).
    
    This endpoint is used by the frontend to determine which UI components to display.
    All actual operations are still protected by backend permissions checks.
    """
    return 200, {
        'is_superuser': request.auth.is_superuser,
        'username': request.auth.username
    }


# ============================================================================
# Override Endpoints - Teacher Only (Own Class)
# ============================================================================

@api.post("/override/class", response={201: OverrideSchema, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Override - Teacher"])
def create_class_override(request, data: OverrideCreateRequest):
    """
    Create a new override for the teacher's own class (requires authentication).
    
    Only class teachers (osztályfőnök) can create overrides for their class.
    The override will automatically be assigned to the teacher's class.
    """
    # Check if user is a class teacher
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers (ofő) can create class overrides'
        }
    
    # Get the teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'No class found for this teacher'
        }
    
    # Create override for teacher's class (ignore class_id from request)
    override = Override.objects.create(
        date=data.date,
        is_required=data.is_required,
        class_id=teacher_class,  # Always use teacher's class
        reason=data.reason
    )
    
    logger.info(f"Teacher {request.auth.username} created override for class {teacher_class} on {data.date}")
    
    return 201, {
        'id': override.id,
        'date': override.date,
        'is_required': override.is_required,
        'class_id': override.class_id.id if override.class_id else None,
        'class_name': str(override.class_id) if override.class_id else None,
        'reason': override.reason
    }


@api.put("/override/class/{override_id}", response={200: OverrideSchema, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Override - Teacher"])
def update_class_override(request, override_id: int, data: OverrideUpdateRequest):
    """
    Update an existing override for the teacher's own class (requires authentication).
    
    Only class teachers (osztályfőnök) can update overrides for their class.
    Teachers can only update overrides that belong to their class.
    """
    # Check if user is a class teacher
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers (ofő) can update class overrides'
        }
    
    # Get the teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'No class found for this teacher'
        }
    
    # Get the override
    try:
        override = Override.objects.get(id=override_id)
    except Override.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Override with id {override_id} does not exist'
        }
    
    # Verify override belongs to teacher's class
    if override.class_id != teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'You can only update overrides for your own class'
        }
    
    # Update fields if provided
    if data.date is not None:
        override.date = data.date
    if data.is_required is not None:
        override.is_required = data.is_required
    if data.reason is not None:
        override.reason = data.reason
    # Note: class_id cannot be changed for class-specific overrides
    
    override.save()
    
    logger.info(f"Teacher {request.auth.username} updated override {override_id} for class {teacher_class}")
    
    return 200, {
        'id': override.id,
        'date': override.date,
        'is_required': override.is_required,
        'class_id': override.class_id.id if override.class_id else None,
        'class_name': str(override.class_id) if override.class_id else None,
        'reason': override.reason
    }


@api.delete("/override/class/{override_id}", response={200: dict, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Override - Teacher"])
def delete_class_override(request, override_id: int):
    """
    Delete an existing override for the teacher's own class (requires authentication).
    
    Only class teachers (osztályfőnök) can delete overrides for their class.
    Teachers can only delete overrides that belong to their class.
    """
    # Check if user is a class teacher
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers (ofő) can delete class overrides'
        }
    
    # Get the teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'No class found for this teacher'
        }
    
    # Get the override
    try:
        override = Override.objects.get(id=override_id)
    except Override.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Override with id {override_id} does not exist'
        }
    
    # Verify override belongs to teacher's class
    if override.class_id != teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'You can only delete overrides for your own class'
        }
    
    override.delete()
    
    logger.info(f"Teacher {request.auth.username} deleted override {override_id} for class {teacher_class}")
    
    return 200, {
        'message': f'Override {override_id} deleted successfully',
        'success': True
    }


# ============================================================================
# Override Endpoints - Superuser Only (Global)
# ============================================================================

@api.post("/override/global", response={201: OverrideSchema, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Override - Superuser"])
def create_global_override(request, data: OverrideCreateRequest):
    """
    Create a new global override (requires superuser authentication).
    
    Global overrides apply to all classes unless a specific class_id is provided.
    Only superusers can create global overrides.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can create global overrides'
        }
    
    # Validate class_id if provided
    class_instance = None
    if data.class_id:
        try:
            class_instance = Osztaly.objects.get(id=data.class_id)
        except Osztaly.DoesNotExist:
            return 400, {
                'error': 'Validation error',
                'detail': f'Class with id {data.class_id} does not exist'
            }
    
    # Create override
    override = Override.objects.create(
        date=data.date,
        is_required=data.is_required,
        class_id=class_instance,
        reason=data.reason
    )
    
    scope = f"class {class_instance}" if class_instance else "all classes"
    logger.info(f"Superuser {request.auth.username} created global override for {scope} on {data.date}")
    
    return 201, {
        'id': override.id,
        'date': override.date,
        'is_required': override.is_required,
        'class_id': override.class_id.id if override.class_id else None,
        'class_name': str(override.class_id) if override.class_id else None,
        'reason': override.reason
    }


@api.put("/override/global/{override_id}", response={200: OverrideSchema, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Override - Superuser"])
def update_global_override(request, override_id: int, data: OverrideUpdateRequest):
    """
    Update an existing global override (requires superuser authentication).
    
    Only superusers can update any override (global or class-specific).
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can update global overrides'
        }
    
    # Get the override
    try:
        override = Override.objects.get(id=override_id)
    except Override.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Override with id {override_id} does not exist'
        }
    
    # Update fields if provided
    if data.date is not None:
        override.date = data.date
    if data.is_required is not None:
        override.is_required = data.is_required
    if data.reason is not None:
        override.reason = data.reason
    
    # Update class_id if provided (can be None to make it global, or specific class)
    if 'class_id' in data.__dict__:  # Check if field was explicitly provided
        if data.class_id is None:
            override.class_id = None
        else:
            try:
                class_instance = Osztaly.objects.get(id=data.class_id)
                override.class_id = class_instance
            except Osztaly.DoesNotExist:
                return 400, {
                    'error': 'Validation error',
                    'detail': f'Class with id {data.class_id} does not exist'
                }
    
    override.save()
    
    logger.info(f"Superuser {request.auth.username} updated override {override_id}")
    
    return 200, {
        'id': override.id,
        'date': override.date,
        'is_required': override.is_required,
        'class_id': override.class_id.id if override.class_id else None,
        'class_name': str(override.class_id) if override.class_id else None,
        'reason': override.reason
    }


@api.delete("/override/global/{override_id}", response={200: dict, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Override - Superuser"])
def delete_global_override(request, override_id: int):
    """
    Delete an existing global override (requires superuser authentication).
    
    Only superusers can delete any override (global or class-specific).
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can delete global overrides'
        }
    
    # Get the override
    try:
        override = Override.objects.get(id=override_id)
    except Override.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Override with id {override_id} does not exist'
        }
    
    override.delete()
    
    logger.info(f"Superuser {request.auth.username} deleted override {override_id}")
    
    return 200, {
        'message': f'Override {override_id} deleted successfully',
        'success': True
    }


# ============================================================================
# Tanítási Szünet Endpoints - Superuser Only
# ============================================================================

@api.post("/tanitasi-szunet", response={201: TanitasiSzunetSchema, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Tanítási Szünet - Superuser"])
def create_tanitasi_szunet(request, data: TanitasiSzunetCreateRequest):
    """
    Create a new school break (requires superuser authentication).
    
    School breaks apply globally to all students and classes.
    Only superusers can create school breaks.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can create school breaks'
        }
    
    # Validate dates
    if data.to_date < data.from_date:
        return 400, {
            'error': 'Validation error',
            'detail': 'End date must be after or equal to start date'
        }
    
    # Create school break
    szunet = TanitasiSzunet.objects.create(
        type=data.type,
        name=data.name,
        from_date=data.from_date,
        to_date=data.to_date,
        description=data.description
    )
    
    logger.info(f"Superuser {request.auth.username} created school break: {szunet}")
    
    return 201, {
        'id': szunet.id,
        'type': szunet.type,
        'name': szunet.name,
        'from_date': szunet.from_date,
        'to_date': szunet.to_date,
        'description': szunet.description
    }


@api.put("/tanitasi-szunet/{szunet_id}", response={200: TanitasiSzunetSchema, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Tanítási Szünet - Superuser"])
def update_tanitasi_szunet(request, szunet_id: int, data: TanitasiSzunetUpdateRequest):
    """
    Update an existing school break (requires superuser authentication).
    
    Only superusers can update school breaks.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can update school breaks'
        }
    
    # Get the school break
    try:
        szunet = TanitasiSzunet.objects.get(id=szunet_id)
    except TanitasiSzunet.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'School break with id {szunet_id} does not exist'
        }
    
    # Update fields if provided
    if data.type is not None:
        szunet.type = data.type
    if data.name is not None:
        szunet.name = data.name
    if data.from_date is not None:
        szunet.from_date = data.from_date
    if data.to_date is not None:
        szunet.to_date = data.to_date
    if data.description is not None:
        szunet.description = data.description
    
    # Validate dates
    if szunet.to_date < szunet.from_date:
        return 400, {
            'error': 'Validation error',
            'detail': 'End date must be after or equal to start date'
        }
    
    szunet.save()
    
    logger.info(f"Superuser {request.auth.username} updated school break {szunet_id}")
    
    return 200, {
        'id': szunet.id,
        'type': szunet.type,
        'name': szunet.name,
        'from_date': szunet.from_date,
        'to_date': szunet.to_date,
        'description': szunet.description
    }


@api.delete("/tanitasi-szunet/{szunet_id}", response={200: dict, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Tanítási Szünet - Superuser"])
def delete_tanitasi_szunet(request, szunet_id: int):
    """
    Delete an existing school break (requires superuser authentication).
    
    Only superusers can delete school breaks.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can delete school breaks'
        }
    
    # Get the school break
    try:
        szunet = TanitasiSzunet.objects.get(id=szunet_id)
    except TanitasiSzunet.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'School break with id {szunet_id} does not exist'
        }
    
    szunet.delete()
    
    logger.info(f"Superuser {request.auth.username} deleted school break {szunet_id}")
    
    return 200, {
        'message': f'School break {szunet_id} deleted successfully',
        'success': True
    }


# ============================================================================
# ADMIN PHASE 1 ENDPOINTS
# ============================================================================

# Feature #1: Password Management

@api.post("/admin/users/{user_id}/generate-password", response={200: GeneratePasswordResponse, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Password Management"])
def generate_user_password(request, user_id: int, send_email: bool = False):
    """
    Generate a strong password for a user.
    
    Requires superuser authentication. If send_email=True, sends password via email.
    If send_email=False, returns password in response (one-time display only).
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can generate passwords for users'
        }
    
    # Get the user
    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'User with id {user_id} does not exist or is inactive'
        }
    
    # Generate strong password
    new_password = generate_strong_password()
    
    # Hash and save password
    user.set_password(new_password)
    user.save()
    
    # Invalidate existing sessions
    invalidate_user_sessions(user)
    
    # Log the action
    logger.info(f"Superuser {request.auth.username} generated new password for user {user.username}")
    
    # Send email or return password
    if send_email:
        if not user.email:
            return 400, {
                'error': 'Bad request',
                'detail': 'User has no email address configured'
            }
        
        email_sent = send_password_generated_email(user, new_password)
        
        if email_sent:
            return 200, {
                'password': None,
                'message': f'New password generated and sent to {user.email}',
                'email_sent': True
            }
        else:
            return 400, {
                'error': 'Email failed',
                'detail': 'Failed to send email. Password was changed but not delivered.'
            }
    else:
        # Return password in response (one-time display)
        return 200, {
            'password': new_password,
            'message': 'New password generated. This is the only time it will be displayed.',
            'email_sent': False
        }


@api.post("/admin/users/{user_id}/reset-password", response={200: ResetPasswordResponse, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Password Management"])
def reset_user_password(request, user_id: int, data: ResetPasswordRequest):
    """
    Reset user password to a specified value.
    
    Requires superuser authentication. Validates password strength.
    Optionally sends email notification to user.
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can reset user passwords'
        }
    
    # Get the user
    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'User with id {user_id} does not exist or is inactive'
        }
    
    # Validate password strength
    is_valid, error_message = validate_password_strength(data.new_password)
    if not is_valid:
        return 400, {
            'error': 'Weak password',
            'detail': error_message
        }
    
    # Hash and save password
    user.set_password(data.new_password)
    user.save()
    
    # Invalidate existing sessions
    invalidate_user_sessions(user)
    
    # Log the action
    logger.info(f"Superuser {request.auth.username} reset password for user {user.username}")
    
    # Send notification email if requested
    email_sent = False
    if data.send_email:
        if user.email:
            email_sent = send_password_changed_notification(user)
    
    return 200, {
        'message': f'Password reset successfully for user {user.username}',
        'email_sent': email_sent
    }


# Feature #3: Teacher Assignment to Classes

@api.post("/admin/classes/{class_id}/assign-teacher", response={200: AssignTeacherResponse, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse}, auth=jwt_auth, tags=["Admin - Teacher Assignment"])
def assign_teacher_to_class(request, class_id: int, data: TeacherAssignmentRequest):
    """
    Assign a teacher to a class.
    
    Requires superuser authentication. Prevents duplicate assignments.
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can assign teachers to classes'
        }
    
    # Get the class
    try:
        osztaly = Osztaly.objects.get(id=class_id)
    except Osztaly.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Class with id {class_id} does not exist'
        }
    
    # Get the teacher
    try:
        teacher = User.objects.get(id=data.teacher_id, is_active=True)
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'User with id {data.teacher_id} does not exist or is inactive'
        }
    
    # Check if already assigned
    if teacher in osztaly.osztalyfonokok.all():
        return 409, {
            'error': 'Conflict',
            'detail': f'Teacher {teacher.username} is already assigned to class {osztaly}'
        }
    
    # Assign teacher to class
    osztaly.osztalyfonokok.add(teacher)
    
    # Log the action
    logger.info(f"Superuser {request.auth.username} assigned teacher {teacher.username} to class {osztaly}")
    
    return 200, {
        'message': f'Teacher {teacher.username} assigned to class {osztaly}',
        'teacher': {
            'id': teacher.id,
            'username': teacher.username,
            'name': get_user_full_name(teacher),
            'is_superuser': teacher.is_superuser
        },
        'class_info': {
            'id': osztaly.id,
            'name': str(osztaly)
        }
    }


@api.delete("/admin/classes/{class_id}/remove-teacher/{teacher_id}", response={200: RemoveTeacherResponse, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Teacher Assignment"])
def remove_teacher_from_class(request, class_id: int, teacher_id: int):
    """
    Remove a teacher from a class.
    
    Requires superuser authentication. Ensures at least one teacher remains assigned.
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can remove teachers from classes'
        }
    
    # Get the class
    try:
        osztaly = Osztaly.objects.get(id=class_id)
    except Osztaly.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Class with id {class_id} does not exist'
        }
    
    # Get the teacher
    try:
        teacher = User.objects.get(id=teacher_id)
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'User with id {teacher_id} does not exist'
        }
    
    # Check if can remove
    can_remove, error_msg = can_remove_teacher_from_class(osztaly, teacher)
    if not can_remove:
        return 400, {
            'error': 'Bad request',
            'detail': error_msg
        }
    
    # Remove teacher from class
    osztaly.osztalyfonokok.remove(teacher)
    
    # Log the action
    logger.info(f"Superuser {request.auth.username} removed teacher {teacher.username} from class {osztaly}")
    
    return 200, {
        'message': f'Teacher {teacher.username} removed from class {osztaly}',
        'removed': True
    }


@api.post("/admin/users/osztalyfonok/move-to-class", response={200: MoveOsztalyfonokResponse, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Teacher Assignment"])
def move_osztalyfonok_test_user(request, data: MoveOsztalyfonokRequest):
    """
    Move the 'osztalyfonok' test user to a different class.
    
    Requires superuser authentication. Removes from all current classes and assigns to new class.
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can move the osztalyfonok test user'
        }
    
    # Find the osztalyfonok user
    try:
        osztalyfonok_user = User.objects.get(username='osztalyfonok', is_active=True)
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': 'The osztalyfonok test user does not exist'
        }
    
    # Get the new class
    try:
        new_class = Osztaly.objects.get(id=data.class_id)
    except Osztaly.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Class with id {data.class_id} does not exist'
        }
    
    # Get current classes
    current_classes = Osztaly.objects.filter(osztalyfonokok=osztalyfonok_user)
    previous_class = current_classes.first()
    
    # Remove from all current classes
    for osztaly in current_classes:
        osztaly.osztalyfonokok.remove(osztalyfonok_user)
    
    # Add to new class
    new_class.osztalyfonokok.add(osztalyfonok_user)
    
    # Log the action
    logger.info(f"Superuser {request.auth.username} moved osztalyfonok from {previous_class} to {new_class}")
    
    return 200, {
        'message': f'Osztalyfonok test user moved to class {new_class}',
        'previous_class': {
            'id': previous_class.id,
            'name': str(previous_class)
        } if previous_class else None,
        'new_class': {
            'id': new_class.id,
            'name': str(new_class)
        }
    }


@api.get("/admin/classes/{class_id}/teachers", response={200: GetTeachersResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Teacher Assignment"])
def get_class_teachers(request, class_id: int):
    """
    Get all teachers assigned to a class.
    
    Requires superuser authentication.
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view class teachers'
        }
    
    # Get the class
    try:
        osztaly = Osztaly.objects.get(id=class_id)
    except Osztaly.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Class with id {class_id} does not exist'
        }
    
    # Get teachers
    teachers = osztaly.osztalyfonokok.all().order_by('last_name', 'first_name')
    
    teachers_data = [
        {
            'id': teacher.id,
            'username': teacher.username,
            'name': get_user_full_name(teacher),
            'is_superuser': teacher.is_superuser,
            'assigned_date': None  # We don't track assignment dates currently
        }
        for teacher in teachers
    ]
    
    return 200, {
        'teachers': teachers_data
    }


# Feature #6: Permissions Management

@api.post("/admin/users/{user_id}/promote-superuser", response={200: PromoteDemoteResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Permissions"])
def promote_to_superuser(request, user_id: int):
    """
    Promote a user to superuser status.
    
    Requires superuser authentication. Logs the permission change.
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can promote users'
        }
    
    # Get the user
    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'User with id {user_id} does not exist or is inactive'
        }
    
    # Check if already superuser
    if user.is_superuser:
        return 200, {
            'message': f'User {user.username} is already a superuser',
            'user': {
                'id': user.id,
                'username': user.username,
                'is_superuser': True
            }
        }
    
    # Promote user
    previous_value = user.is_superuser
    user.is_superuser = True
    user.is_staff = True  # Superusers should also have staff access
    user.save()
    
    # Log permission change
    log_permission_change(
        user=user,
        changed_by=request.auth,
        action=PermissionChangeLog.ACTION_PROMOTED,
        previous_value=previous_value,
        new_value=True
    )
    
    # Send notification email
    if user.email:
        send_permission_change_email(user, promoted=True, changed_by=request.auth)
    
    logger.info(f"Superuser {request.auth.username} promoted {user.username} to superuser")
    
    return 200, {
        'message': f'User {user.username} promoted to superuser',
        'user': {
            'id': user.id,
            'username': user.username,
            'is_superuser': True
        }
    }


@api.post("/admin/users/{user_id}/demote-superuser", response={200: PromoteDemoteResponse, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Permissions"])
def demote_from_superuser(request, user_id: int):
    """
    Demote a user from superuser status.
    
    Requires superuser authentication. Prevents self-demotion. Logs the permission change.
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can demote users'
        }
    
    # Prevent self-demotion
    if request.auth.id == user_id:
        return 400, {
            'error': 'Bad request',
            'detail': 'You cannot demote yourself'
        }
    
    # Get the user
    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'User with id {user_id} does not exist or is inactive'
        }
    
    # Check if not superuser
    if not user.is_superuser:
        return 200, {
            'message': f'User {user.username} is not a superuser',
            'user': {
                'id': user.id,
                'username': user.username,
                'is_superuser': False
            }
        }
    
    # Demote user
    previous_value = user.is_superuser
    user.is_superuser = False
    user.save()
    
    # Log permission change
    log_permission_change(
        user=user,
        changed_by=request.auth,
        action=PermissionChangeLog.ACTION_DEMOTED,
        previous_value=previous_value,
        new_value=False
    )
    
    # Send notification email
    if user.email:
        send_permission_change_email(user, promoted=False, changed_by=request.auth)
    
    logger.info(f"Superuser {request.auth.username} demoted {user.username} from superuser")
    
    return 200, {
        'message': f'User {user.username} demoted from superuser',
        'user': {
            'id': user.id,
            'username': user.username,
            'is_superuser': False
        }
    }


@api.get("/admin/users/{user_id}/permissions", response={200: UserPermissionsResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Permissions"])
def get_user_permissions(request, user_id: int):
    """
    Get user's current permissions and change history.
    
    Requires superuser authentication.
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view user permissions'
        }
    
    # Get the user
    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'User with id {user_id} does not exist or is inactive'
        }
    
    # Get permission change history
    history = get_permission_history(user, limit=50)
    
    change_history = [
        {
            'changed_by': log.changed_by.username if log.changed_by else 'System',
            'changed_at': log.changed_at,
            'action': log.action,
            'previous_value': log.previous_value,
            'new_value': log.new_value
        }
        for log in history
    ]
    
    return 200, {
        'user_id': user.id,
        'username': user.username,
        'is_superuser': user.is_superuser,
        'is_staff': user.is_staff,
        'permissions': [],  # Django permissions can be added here if needed
        'change_history': change_history
    }


# Feature #4: Student Login Statistics

@api.get("/admin/students/login-stats", response={200: LoginStatsResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Login Statistics"])
def get_student_login_statistics(request):
    """
    Get comprehensive student login statistics.
    
    Requires superuser authentication. Returns per-class breakdown with individual student stats.
    """
    # Check superuser permission
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view login statistics'
        }
    
    # Get all classes
    classes = Osztaly.objects.all().prefetch_related('tanulok')
    
    total_students = 0
    total_logged_in = 0
    per_class_stats = []
    
    for osztaly in classes:
        students = osztaly.tanulok.all()
        class_total = students.count()
        class_logged_in = 0
        
        students_data = []
        for student in students:
            profile = Profile.objects.filter(user=student).first()
            login_count = profile.login_count if profile else 0
            
            has_logged_in = student.last_login is not None
            if has_logged_in:
                class_logged_in += 1
            
            students_data.append({
                'id': student.id,
                'name': get_user_full_name(student),
                'last_login': student.last_login,
                'login_count': login_count
            })
        
        per_class_stats.append({
            'class_id': osztaly.id,
            'class_name': str(osztaly),
            'total': class_total,
            'logged_in': class_logged_in,
            'never_logged_in': class_total - class_logged_in,
            'students': sorted(students_data, key=lambda x: (x['last_login'] is None, x['name']))
        })
        
        total_students += class_total
        total_logged_in += class_logged_in
    
    return 200, {
        'summary': {
            'total': total_students,
            'logged_in': total_logged_in,
            'never_logged_in': total_students - total_logged_in
        },
        'per_class': sorted(per_class_stats, key=lambda x: x['class_name'])
    }


# ============================================================================
# Mulasztas (eKréta Upload) Helper Functions - EXPERIMENTAL
# ============================================================================

# Helper function for Mulasztas analysis
def analyze_mulasztas_coverage(user: User, include_igazolt: bool = False) -> dict:
    """
    Analyze which student-uploaded Mulasztas records are covered by existing Igazolas records.
    
    Args:
        user: The student user
        include_igazolt: If True, includes already justified absences
    
    Returns:
        Dictionary with analysis results
    """
    from datetime import datetime, time, timedelta
    
    # Get all student-uploaded mulasztas records for the user
    mulasztasok_query = Mulasztas.objects.filter(uploaded_by_student=user)
    if not include_igazolt:
        mulasztasok_query = mulasztasok_query.filter(igazolt=False)
    
    mulasztasok = list(mulasztasok_query.order_by('-datum', 'ora'))
    
    # Get all accepted igazolas records for the user
    profile = Profile.objects.filter(user=user).first()
    if not profile:
        # No profile, no igazolások
        return {
            'total_mulasztasok': len(mulasztasok),
            'igazolt_count': 0,
            'nem_igazolt_count': len(mulasztasok),
            'covered_by_igazolas': 0,
            'not_covered': len(mulasztasok),
            'mulasztasok': [
                {
                    'id': m.id,
                    'datum': m.datum,
                    'ora': m.ora,
                    'tantargy': m.tantargy,
                    'tema': m.tema,
                    'tipus': m.tipus,
                    'igazolt': m.igazolt,
                    'tanorai_celu_mulasztas': m.tanorai_celu_mulasztas,
                    'igazolas_tipusa': m.igazolas_tipusa,
                    'rogzites_datuma': m.rogzites_datuma,
                    'mulasztas_ok': m.mulasztas_ok,
                    'mulasztas_statusz': m.mulasztas_statusz,
                    'uploaded_at': m.uploaded_at,
                    'matched_igazolas_id': None,
                    'is_covered': False
                }
                for m in mulasztasok
            ]
        }
    
    # Get accepted AND pending igazolások (to show what could potentially cover the mulasztás)
    igazolasok = Igazolas.objects.filter(
        profile=profile,
        allapot__in=['Elfogadva', 'Függőben']
    ).order_by('-eleje')
    
    # Analyze coverage
    covered_count = 0
    igazolt_count = sum(1 for m in mulasztasok if m.igazolt)
    nem_igazolt_count = len(mulasztasok) - igazolt_count
    
    # Hungarian bell schedule (csengetési rend) - matching frontend periods.ts
    BELL_SCHEDULE = [
        ("07:30", "08:15"),  # 0. óra
        ("08:25", "09:10"),  # 1. óra
        ("09:20", "10:05"),  # 2. óra
        ("10:20", "11:05"),  # 3. óra
        ("11:15", "12:00"),  # 4. óra
        ("12:20", "13:05"),  # 5. óra
        ("13:25", "14:10"),  # 6. óra
        ("14:20", "15:05"),  # 7. óra
        ("15:15", "16:00"),  # 8. óra
    ]
    
    mulasztasok_data = []
    
    for mulasztas in mulasztasok:
        # Convert mulasztas date + ora to datetime range using actual bell schedule
        ora_index = mulasztas.ora  # 0-8
        if ora_index < 0 or ora_index >= len(BELL_SCHEDULE):
            # Invalid lesson number, skip or use default
            lesson_start = timezone.make_aware(datetime.combine(mulasztas.datum, time(8, 0)))
            lesson_end = lesson_start + timedelta(minutes=45)
        else:
            start_time_str, end_time_str = BELL_SCHEDULE[ora_index]
            start_hour, start_min = map(int, start_time_str.split(':'))
            end_hour, end_min = map(int, end_time_str.split(':'))
            
            lesson_start_naive = datetime.combine(mulasztas.datum, time(start_hour, start_min))
            lesson_end_naive = datetime.combine(mulasztas.datum, time(end_hour, end_min))
            
            # Make timezone-aware
            lesson_start = timezone.make_aware(lesson_start_naive)
            lesson_end = timezone.make_aware(lesson_end_naive)
        
        # Check if any igazolas covers this mulasztas
        matched_igazolas = None
        is_covered = False
        
        for igazolas in igazolasok:
            # Ensure igazolas times are timezone-aware for comparison
            igazolas_eleje = igazolas.eleje
            igazolas_vege = igazolas.vege
            
            if timezone.is_naive(igazolas_eleje):
                igazolas_eleje = timezone.make_aware(igazolas_eleje)
            if timezone.is_naive(igazolas_vege):
                igazolas_vege = timezone.make_aware(igazolas_vege)
            
            # Check if the lesson time overlaps with the igazolas time range
            if (igazolas_eleje <= lesson_start <= igazolas_vege) or \
               (igazolas_eleje <= lesson_end <= igazolas_vege) or \
               (lesson_start <= igazolas_eleje and lesson_end >= igazolas_vege):
                matched_igazolas = igazolas
                is_covered = True
                covered_count += 1
                break
        
        mulasztasok_data.append({
            'id': mulasztas.id,
            'datum': mulasztas.datum,
            'ora': mulasztas.ora,
            'tantargy': mulasztas.tantargy,
            'tema': mulasztas.tema,
            'tipus': mulasztas.tipus,
            'igazolt': mulasztas.igazolt,
            'tanorai_celu_mulasztas': mulasztas.tanorai_celu_mulasztas,
            'igazolas_tipusa': mulasztas.igazolas_tipusa,
            'rogzites_datuma': mulasztas.rogzites_datuma,
            'mulasztas_ok': mulasztas.mulasztas_ok,
            'mulasztas_statusz': mulasztas.mulasztas_statusz,
            'uploaded_at': mulasztas.uploaded_at,
            'matched_igazolas_id': matched_igazolas.id if matched_igazolas else None,
            'is_covered': is_covered
        })
    
    return {
        'total_mulasztasok': len(mulasztasok),
        'igazolt_count': igazolt_count,
        'nem_igazolt_count': nem_igazolt_count,
        'covered_by_igazolas': covered_count,
        'not_covered': nem_igazolt_count - covered_count,  # Always exclude igazolt from not_covered count
        'mulasztasok': mulasztasok_data
    }


# ============================================================================
# Admin Phase 2 Endpoints - Analytics & Monitoring
# ============================================================================

# Feature #2: Class Activity Heatmap

@api.get("/admin/classes/activity-heatmap", response={200: ActivityHeatmapResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Analytics"])
def get_class_activity_heatmap(request, from_date: str, to_date: str, metric_type: str = 'submissions'):
    """
    Get activity heatmap data for all classes.
    
    Metric types: 'submissions', 'approvals', 'logins'
    Returns intensity-coded data points for visualization.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view analytics'
        }
    
    from datetime import datetime, timedelta
    from django.db.models import Count, Q
    
    start = datetime.fromisoformat(from_date).date()
    end = datetime.fromisoformat(to_date).date()
    
    # Generate date range
    dates = []
    current = start
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)
    
    classes = Osztaly.objects.all()
    classes_data = []
    
    for osztaly in classes:
        activity_data = []
        
        for date in dates:
            if metric_type == 'submissions':
                count = Igazolas.objects.filter(
                    profile__user__in=osztaly.tanulok.all(),
                    rogzites_datuma=date
                ).count()
            elif metric_type == 'approvals':
                count = Igazolas.objects.filter(
                    profile__user__in=osztaly.tanulok.all(),
                    rogzites_datuma=date,
                    allapot='Elfogadva'
                ).count()
            else:  # logins
                count = Profile.objects.filter(
                    user__in=osztaly.tanulok.all(),
                    user__last_login__date=date
                ).count()
            
            # Calculate intensity (0-5 scale)
            if count == 0:
                intensity = 0
            elif count <= 2:
                intensity = 1
            elif count <= 5:
                intensity = 2
            elif count <= 10:
                intensity = 3
            elif count <= 20:
                intensity = 4
            else:
                intensity = 5
            
            activity_data.append({
                'date': date,
                'value': count,
                'intensity': intensity
            })
        
        classes_data.append({
            'id': osztaly.id,
            'name': str(osztaly),
            'data': activity_data
        })
    
    return {
        'dates': dates,
        'classes': classes_data
    }


@api.get("/admin/classes/overview-stats", response={200: ClassesOverviewResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Analytics"])
def get_classes_overview_stats(request):
    """
    Get overview statistics for all classes.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view analytics'
        }
    
    from django.db.models import Count, Q, Max
    
    classes = Osztaly.objects.all()
    classes_stats = []
    
    for osztaly in classes:
        student_ids = osztaly.tanulok.values_list('id', flat=True)
        
        # Get stats
        total_students = len(student_ids)
        active_students = Igazolas.objects.filter(
            profile__user_id__in=student_ids
        ).values('profile__user_id').distinct().count()
        
        pending_count = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            allapot='Folyamatban'
        ).count()
        
        total_igazolasok = Igazolas.objects.filter(
            profile__user_id__in=student_ids
        ).count()
        
        approved = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            allapot='Elfogadva'
        ).count()
        
        approval_rate = (approved / total_igazolasok * 100) if total_igazolasok > 0 else 0.0
        
        last_activity = Igazolas.objects.filter(
            profile__user_id__in=student_ids
        ).aggregate(Max('rogzites_datuma'))['rogzites_datuma__max']
        
        classes_stats.append({
            'id': osztaly.id,
            'name': str(osztaly),
            'total_students': total_students,
            'active_students': active_students,
            'pending_count': pending_count,
            'approval_rate': round(approval_rate, 2),
            'last_activity': last_activity
        })
    
    return {
        'classes': classes_stats
    }


# Feature #5: Teacher Workload Dashboard

@api.get("/admin/teachers/workload", response={200: TeacherWorkloadResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Analytics"])
def get_teacher_workload(request):
    """
    Get workload statistics for all teachers.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view analytics'
        }
    
    from datetime import date
    
    # Get all teachers (users assigned to classes)
    teachers = User.objects.filter(osztalyfonokok__isnull=False).distinct()
    teacher_workloads = []
    
    for teacher in teachers:
        teacher_classes = Osztaly.objects.filter(osztalyfonokok=teacher)
        class_names = [str(c) for c in teacher_classes]
        
        # Get all students from teacher's classes
        student_ids = []
        for osztaly in teacher_classes:
            student_ids.extend(osztaly.tanulok.values_list('id', flat=True))
        
        total_students = len(set(student_ids))
        
        # Pending count
        pending_count = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            allapot='Folyamatban'
        ).count()
        
        # Today's stats
        today = date.today()
        approved_today = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            allapot='Elfogadva',
            rogzites_datuma=today
        ).count()
        
        rejected_today = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            allapot='Elutasítva',
            rogzites_datuma=today
        ).count()
        
        # TODO: Calculate avg_response_time_hours when timestamp tracking is added
        avg_response_time = None
        
        teacher_workloads.append({
            'id': teacher.id,
            'name': get_user_full_name(teacher),
            'classes': class_names,
            'total_students': total_students,
            'pending_count': pending_count,
            'approved_today': approved_today,
            'rejected_today': rejected_today,
            'avg_response_time_hours': avg_response_time
        })
    
    return {
        'teachers': teacher_workloads
    }


# Feature #7: Teacher Activity Monitoring

@api.get("/admin/teachers/{teacher_id}/activity", response={200: TeacherActivityResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Analytics"])
def get_teacher_activity(request, teacher_id: int, from_date: str, to_date: str):
    """
    Get detailed activity for a specific teacher.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view analytics'
        }
    
    from datetime import datetime
    from django.db.models import Count, Q
    
    try:
        teacher = User.objects.get(id=teacher_id)
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': 'Teacher not found'
        }
    
    start = datetime.fromisoformat(from_date).date()
    end = datetime.fromisoformat(to_date).date()
    
    # Get teacher's classes
    teacher_classes = Osztaly.objects.filter(osztalyfonokok=teacher)
    student_ids = []
    for osztaly in teacher_classes:
        student_ids.extend(osztaly.tanulok.values_list('id', flat=True))
    
    # Get profile for login count
    profile = Profile.objects.filter(user=teacher).first()
    login_count = profile.login_count if profile else 0
    
    # Actions breakdown
    approved = Igazolas.objects.filter(
        profile__user_id__in=student_ids,
        allapot='Elfogadva',
        rogzites_datuma__range=[start, end]
    ).count()
    
    rejected = Igazolas.objects.filter(
        profile__user_id__in=student_ids,
        allapot='Elutasítva',
        rogzites_datuma__range=[start, end]
    ).count()
    
    commented = Igazolas.objects.filter(
        profile__user_id__in=student_ids,
        megjegyzes_tanar__isnull=False,
        rogzites_datuma__range=[start, end]
    ).exclude(megjegyzes_tanar='').count()
    
    total_actions = approved + rejected + commented
    
    # Activity timeline
    timeline_data = Igazolas.objects.filter(
        profile__user_id__in=student_ids,
        rogzites_datuma__range=[start, end]
    ).extra(
        select={'date': 'DATE(rogzites_datuma)'}
    ).values('date', 'allapot').annotate(count=Count('id'))
    
    # Group by date
    timeline = {}
    for item in timeline_data:
        date = item['date']
        if date not in timeline:
            timeline[date] = {'date': date, 'approved': 0, 'rejected': 0, 'other': 0}
        
        if item['allapot'] == 'Elfogadva':
            timeline[date]['approved'] = item['count']
        elif item['allapot'] == 'Elutasítva':
            timeline[date]['rejected'] = item['count']
        else:
            timeline[date]['other'] += item['count']
    
    # Convert to list format
    activity_timeline = []
    for date, data in sorted(timeline.items()):
        if data['approved'] > 0:
            activity_timeline.append({'date': date, 'action_type': 'approved', 'count': data['approved']})
        if data['rejected'] > 0:
            activity_timeline.append({'date': date, 'action_type': 'rejected', 'count': data['rejected']})
        if data['other'] > 0:
            activity_timeline.append({'date': date, 'action_type': 'other', 'count': data['other']})
    
    return {
        'user': {
            'id': teacher.id,
            'username': teacher.username,
            'first_name': teacher.first_name,
            'last_name': teacher.last_name,
            'email': teacher.email
        },
        'login_count': login_count,
        'total_actions': total_actions,
        'actions_breakdown': {
            'approved': approved,
            'rejected': rejected,
            'commented': commented
        },
        'activity_timeline': activity_timeline
    }


# Feature #20: Approval Rate Analysis

@api.get("/admin/analytics/approval-rates", response={200: ApprovalRatesResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Analytics"])
def get_approval_rates(request, from_date: str, to_date: str, group_by: str = 'teacher'):
    """
    Get approval rate analytics.
    
    group_by options: 'teacher', 'type', 'class', 'all'
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view analytics'
        }
    
    from datetime import datetime, timedelta
    from django.db.models import Count, Q
    
    start = datetime.fromisoformat(from_date).date()
    end = datetime.fromisoformat(to_date).date()
    
    # Overall rate
    total = Igazolas.objects.filter(rogzites_datuma__range=[start, end]).count()
    approved = Igazolas.objects.filter(
        rogzites_datuma__range=[start, end],
        allapot='Elfogadva'
    ).count()
    
    overall_rate = (approved / total * 100) if total > 0 else 0.0
    
    # By teacher
    by_teacher = []
    teachers = User.objects.filter(osztalyfonokok__isnull=False).distinct()
    for teacher in teachers:
        teacher_classes = Osztaly.objects.filter(osztalyfonokok=teacher)
        student_ids = []
        for osztaly in teacher_classes:
            student_ids.extend(osztaly.tanulok.values_list('id', flat=True))
        
        teacher_total = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            rogzites_datuma__range=[start, end]
        ).count()
        
        teacher_approved = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            rogzites_datuma__range=[start, end],
            allapot='Elfogadva'
        ).count()
        
        teacher_rejected = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            rogzites_datuma__range=[start, end],
            allapot='Elutasítva'
        ).count()
        
        if teacher_total > 0:
            by_teacher.append({
                'teacher_id': teacher.id,
                'teacher_name': get_user_full_name(teacher),
                'total': teacher_total,
                'approved': teacher_approved,
                'rejected': teacher_rejected,
                'approval_rate': round(teacher_approved / teacher_total * 100, 2)
            })
    
    # By type
    by_type = []
    types = IgazolasTipus.objects.all()
    for igazolas_type in types:
        type_total = Igazolas.objects.filter(
            tipus=igazolas_type,
            rogzites_datuma__range=[start, end]
        ).count()
        
        type_approved = Igazolas.objects.filter(
            tipus=igazolas_type,
            rogzites_datuma__range=[start, end],
            allapot='Elfogadva'
        ).count()
        
        type_rejected = Igazolas.objects.filter(
            tipus=igazolas_type,
            rogzites_datuma__range=[start, end],
            allapot='Elutasítva'
        ).count()
        
        if type_total > 0:
            by_type.append({
                'type_id': igazolas_type.id,
                'type_name': igazolas_type.nev,
                'total': type_total,
                'approved': type_approved,
                'rejected': type_rejected,
                'approval_rate': round(type_approved / type_total * 100, 2)
            })
    
    # By class
    by_class = []
    classes = Osztaly.objects.all()
    for osztaly in classes:
        student_ids = osztaly.tanulok.values_list('id', flat=True)
        
        class_total = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            rogzites_datuma__range=[start, end]
        ).count()
        
        class_approved = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            rogzites_datuma__range=[start, end],
            allapot='Elfogadva'
        ).count()
        
        class_rejected = Igazolas.objects.filter(
            profile__user_id__in=student_ids,
            rogzites_datuma__range=[start, end],
            allapot='Elutasítva'
        ).count()
        
        if class_total > 0:
            by_class.append({
                'class_id': osztaly.id,
                'class_name': str(osztaly),
                'total': class_total,
                'approved': class_approved,
                'rejected': class_rejected,
                'approval_rate': round(class_approved / class_total * 100, 2)
            })
    
    # Trend over time (daily)
    trend = []
    current = start
    while current <= end:
        day_total = Igazolas.objects.filter(rogzites_datuma=current).count()
        day_approved = Igazolas.objects.filter(
            rogzites_datuma=current,
            allapot='Elfogadva'
        ).count()
        
        if day_total > 0:
            trend.append({
                'date': current,
                'approval_rate': round(day_approved / day_total * 100, 2),
                'total': day_total
            })
        
        current += timedelta(days=1)
    
    return {
        'overall_rate': round(overall_rate, 2),
        'by_teacher': by_teacher,
        'by_type': by_type,
        'by_class': by_class,
        'trend': trend
    }


# ============================================================================
# System Management Features (Phase 1)
# ============================================================================

# Feature #10: Database Statistics

@api.get("/admin/system/database-stats", response={200: dict, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - System Management"])
def get_database_stats(request):
    """
    Get database statistics including record counts, growth rates, and table sizes.
    
    Requires superuser permission.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view database statistics'
        }
    
    from django.db.models import Count
    from datetime import datetime, timedelta
    from django.db import connection
    
    # Get total counts
    total_users = User.objects.count()
    total_classes = Osztaly.objects.count()
    total_igazolasok = Igazolas.objects.count()
    total_mulasztasok = Mulasztas.objects.count()
    total_igazolas_types = IgazolasTipus.objects.count()
    
    # Calculate growth rates
    now = timezone.now()
    yesterday = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    
    # Daily growth
    daily_igazolasok = Igazolas.objects.filter(
        rogzites_datuma__gte=yesterday.date()
    ).count()
    
    # Weekly growth
    weekly_igazolasok = Igazolas.objects.filter(
        rogzites_datuma__gte=week_ago.date()
    ).count()
    
    # Monthly growth
    monthly_igazolasok = Igazolas.objects.filter(
        rogzites_datuma__gte=month_ago.date()
    ).count()
    
    # Get database size (SQLite specific)
    db_size_mb = None
    try:
        import os
        db_path = connection.settings_dict.get('NAME')
        if db_path and os.path.exists(db_path):
            db_size_bytes = os.path.getsize(db_path)
            db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
    except Exception as e:
        logger.warning(f"Could not get database size: {str(e)}")
    
    # Largest tables (by record count)
    largest_tables = [
        {
            'name': 'Igazolások',
            'count': total_igazolasok,
            'percentage': round((total_igazolasok / (total_igazolasok + total_mulasztasok + total_users + total_classes) * 100), 2) if (total_igazolasok + total_mulasztasok + total_users + total_classes) > 0 else 0
        },
        {
            'name': 'Mulasztások',
            'count': total_mulasztasok,
            'percentage': round((total_mulasztasok / (total_igazolasok + total_mulasztasok + total_users + total_classes) * 100), 2) if (total_igazolasok + total_mulasztasok + total_users + total_classes) > 0 else 0
        },
        {
            'name': 'Users',
            'count': total_users,
            'percentage': round((total_users / (total_igazolasok + total_mulasztasok + total_users + total_classes) * 100), 2) if (total_igazolasok + total_mulasztasok + total_users + total_classes) > 0 else 0
        },
        {
            'name': 'Classes',
            'count': total_classes,
            'percentage': round((total_classes / (total_igazolasok + total_mulasztasok + total_users + total_classes) * 100), 2) if (total_igazolasok + total_mulasztasok + total_users + total_classes) > 0 else 0
        }
    ]
    
    # Sort by count descending
    largest_tables.sort(key=lambda x: x['count'], reverse=True)
    
    logger.info(f"User {request.auth.username} retrieved database statistics")
    
    return 200, {
        'total_counts': {
            'users': total_users,
            'classes': total_classes,
            'igazolasok': total_igazolasok,
            'mulasztasok': total_mulasztasok,
            'igazolas_types': total_igazolas_types
        },
        'growth_rates': {
            'igazolasok_7d': weekly_igazolasok,
            'mulasztasok_7d': 0,  # Not yet calculated
            'users_30d': 0  # Not yet calculated
        },
        'total_users': total_users,
        'total_classes': total_classes,
        'total_igazolasok': total_igazolasok,
        'total_mulasztasok': total_mulasztasok,
        'growth_rate': {
            'daily': daily_igazolasok,
            'weekly': weekly_igazolasok,
            'monthly': monthly_igazolasok
        },
        'database_size_mb': db_size_mb,
        'db_size_mb': db_size_mb,
        'largest_tables': largest_tables
    }


# Feature #12: Storage Usage Monitoring

@api.get("/admin/system/storage-stats", response={200: dict, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - System Management"])
def get_storage_stats(request):
    """
    Get storage usage statistics including file sizes and types.
    
    Requires superuser permission.
    Note: This system uses Google Drive for file storage (imgDriveURL),
    so this endpoint calculates estimated usage based on metadata.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view storage statistics'
        }
    
    # Count igazolások with images
    igazolasok_with_images = Igazolas.objects.filter(
        imgDriveURL__isnull=False
    ).exclude(imgDriveURL='').count()
    
    # Estimate storage (assuming average 2MB per image)
    # This is a rough estimate since actual files are on Google Drive
    estimated_images_mb = igazolasok_with_images * 2.0
    
    # Count BKK verification data (stored as JSON)
    igazolasok_with_bkk = Igazolas.objects.filter(
        bkk_verification__isnull=False
    ).count()
    
    # Estimate BKK data size (assuming average 10KB per verification)
    estimated_bkk_mb = round((igazolasok_with_bkk * 10) / 1024, 2)
    
    # Total estimated
    total_mb = round(estimated_images_mb + estimated_bkk_mb, 2)
    
    # Largest "files" (actually records with large data)
    largest_files = []
    
    # Recent igazolások with images
    recent_with_images = Igazolas.objects.filter(
        imgDriveURL__isnull=False
    ).exclude(imgDriveURL='').order_by('-rogzites_datuma')[:10]
    
    for igazolas in recent_with_images:
        largest_files.append({
            'name': f'Igazolas #{igazolas.id} - {igazolas.profile.user.get_full_name() or igazolas.profile.user.username}',
            'size_mb': 2.0,  # Estimated
            'type': 'image',
            'uploaded_date': datetime.combine(igazolas.rogzites_datuma, datetime.min.time())
        })
    
    # Storage trend (last 30 days)
    from datetime import timedelta
    trend = []
    for days_ago in range(30, 0, -1):
        date = (timezone.now() - timedelta(days=days_ago)).date()
        count_up_to_date = Igazolas.objects.filter(
            imgDriveURL__isnull=False,
            rogzites_datuma__lte=date
        ).exclude(imgDriveURL='').count()
        
        trend.append({
            'date': date,
            'total_mb': round(count_up_to_date * 2.0, 2)
        })
    
    logger.info(f"User {request.auth.username} retrieved storage statistics")
    
    # Get database size
    db_path = Path(settings.DATABASES['default']['NAME'])
    db_size_bytes = db_path.stat().st_size if db_path.exists() else 0
    db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
    
    # Recalculate total with database size
    total_storage = round(estimated_images_mb + estimated_bkk_mb + db_size_mb, 2)
    
    return 200, {
        'breakdown': {
            'images_mb': round(estimated_images_mb, 2),
            'bkk_data_mb': estimated_bkk_mb,
            'database_mb': db_size_mb
        },
        'total_storage_mb': total_storage,
        'largest_files': largest_files[:10],
        'trend': trend
    }


# Feature #19: Maintenance Mode

@api.get("/admin/maintenance/status", response={200: dict, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - System Management"])
def get_maintenance_status(request):
    """
    Get current maintenance mode status.
    
    Requires superuser permission.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view maintenance status'
        }
    
    # Check for active maintenance system message
    active_maintenance = SystemMessage.objects.filter(
        messageType='operator',
        severity='warning',
        title__icontains='maintenance'
    ).order_by('-showFrom').first()
    
    if active_maintenance and active_maintenance.is_active():
        return 200, {
            'is_active': True,
            'message': active_maintenance.message,
            'scheduled_start': active_maintenance.showFrom,
            'scheduled_end': active_maintenance.showTo,
            'allowed_users': ['superuser']  # Only superusers can access during maintenance
        }
    
    return 200, {
        'is_active': False,
        'message': None,
        'scheduled_start': None,
        'scheduled_end': None,
        'allowed_users': []
    }


@api.post("/admin/maintenance/toggle", response={200: dict, 400: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - System Management"])
def toggle_maintenance_mode(request, payload: dict = Body(...)):
    """
    Enable or disable maintenance mode.
    
    Requires superuser permission.
    When enabled, creates a system message that will be displayed to all users.
    
    Payload:
    - enabled: bool (optional, default: True if message provided, False otherwise)
    - message: str (optional, default maintenance message)
    - scheduled_start: datetime (optional, default: now)
    - scheduled_end: datetime (optional, default: now + 2 hours)
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can toggle maintenance mode'
        }
    
    # Check if there's an active maintenance message
    has_active = SystemMessage.objects.filter(
        messageType='operator',
        severity='warning',
        title__icontains='Maintenance',
        showTo__gte=timezone.now()
    ).exists()
    
    # If message is provided, enable maintenance; otherwise toggle
    message = payload.get('message')
    enabled = payload.get('enabled', message is not None if not has_active else not has_active)
    
    if enabled:
        # Create maintenance mode message
        if not message:
            message = 'A rendszer karbantartás alatt áll. Kérjük próbálja meg később.'
        
        scheduled_start = payload.get('scheduled_start')
        scheduled_end = payload.get('scheduled_end')
        
        start = scheduled_start or timezone.now()
        end = scheduled_end or (timezone.now() + timedelta(hours=2))
        
        # Deactivate any existing maintenance messages first
        SystemMessage.objects.filter(
            messageType='operator',
            severity='warning',
            title__icontains='Maintenance'
        ).delete()
        
        # Create new maintenance message
        maintenance_msg = SystemMessage.objects.create(
            title='Maintenance Mode',
            message=message,
            severity='warning',
            messageType='operator',
            showFrom=start,
            showTo=end
        )
        
        logger.info(f"User {request.auth.username} enabled maintenance mode until {end}")
        
        return 200, {
            'is_active': True,
            'message': message,
            'scheduled_start': start,
            'scheduled_end': end
        }
    else:
        # Disable maintenance mode by expiring active messages
        SystemMessage.objects.filter(
            messageType='operator',
            severity='warning',
            title__icontains='Maintenance',
            showTo__gte=timezone.now()
        ).update(showTo=timezone.now())
        
        logger.info(f"User {request.auth.username} disabled maintenance mode")
        
        return 200, {
            'is_active': False,
            'message': 'Maintenance mode disabled'
        }


# ============================================================================
# Student & Teacher Features
# ============================================================================

# Feature #25: Group Absences Support

@api.get("/igazolastipus/group-enabled", response={200: List[dict], 401: ErrorResponse}, auth=jwt_auth, tags=["Group Absences"])
def get_group_enabled_types(request):
    """
    Get all igazolás types that support group absences.
    
    Requires authentication.
    """
    types = IgazolasTipus.objects.filter(supports_group_absence=True)
    
    return 200, [
        {
            'id': t.id,
            'nev': t.nev,
            'leiras': t.leiras,
            'supports_group_absence': t.supports_group_absence,
            'requires_studios': t.requires_studios
        }
        for t in types
    ]


@api.get("/students/classmates-eligible", response={200: dict, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Group Absences"])
def get_eligible_classmates(request, igazolas_type_id: int = None, for_studios_only: bool = False):
    """
    Get list of classmates eligible for group igazolás.
    
    Args:
        igazolas_type_id: Optional filter by igazolás type
        for_studios_only: If True, only return stúdiós students
    
    Requires authentication. Returns classmates from the same class.
    """
    # Get current user's profile and class
    try:
        profile = Profile.objects.get(user=request.auth)
        osztaly = profile.osztalyom()
        
        if not osztaly:
            return 404, {
                'error': 'Not found',
                'detail': 'No class found for current user'
            }
        
        # Get all classmates except current user
        classmates = User.objects.filter(
            id__in=osztaly.tanulok.all().values_list('id', flat=True)
        ).exclude(id=request.auth.id).select_related('profile')
        
        # Filter by studios if requested
        if for_studios_only:
            classmates = classmates.filter(profile__is_studios=True)
        
        # If specific type requested, check if it requires studios
        if igazolas_type_id:
            try:
                igazolas_type = IgazolasTipus.objects.get(id=igazolas_type_id)
                if igazolas_type.requires_studios:
                    classmates = classmates.filter(profile__is_studios=True)
            except IgazolasTipus.DoesNotExist:
                pass
        
        # Build response
        eligible_students = []
        for student in classmates:
            try:
                student_profile = student.profile
                full_name = f"{student.last_name} {student.first_name}" if student.last_name and student.first_name else student.username
                
                eligible_students.append({
                    'id': student.id,
                    'username': student.username,
                    'first_name': student.first_name or '',
                    'last_name': student.last_name or '',
                    'full_name': full_name,
                    'is_studios': student_profile.is_studios
                })
            except Profile.DoesNotExist:
                # Skip students without profiles
                continue
        
        return 200, {
            'eligible_students': eligible_students,
            'total_count': len(eligible_students)
        }
        
    except Profile.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': 'Profile not found for current user'
        }


@api.post("/igazolasok/create-group", response={201: dict, 400: ErrorResponse, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Group Absences"])
def create_group_igazolas(request, eleje: datetime, vege: datetime, tipus: int, 
                         additional_student_ids: List[int] = Body(...),
                         megjegyzes_diak: Optional[str] = None,
                         imgDriveURL: Optional[str] = None,
                         bkk_verification: Optional[dict] = None):
    """
    Create group igazolás for multiple students.
    
    Creates linked igazolás records for the current user and all specified classmates.
    The current user is marked as the group leader.
    
    Requires authentication.
    """
    # Validate dates
    if eleje >= vege:
        return 400, {
            'error': 'Validation error',
            'detail': 'End time must be after start time'
        }
    
    # Get or create profile for current user
    profile, _ = Profile.objects.get_or_create(user=request.auth)
    
    # Verify tipus exists and supports group absences
    try:
        igazolas_type = IgazolasTipus.objects.get(id=tipus)
        if not igazolas_type.supports_group_absence:
            return 400, {
                'error': 'Invalid type',
                'detail': 'This igazolás type does not support group absences'
            }
        
        # Check if type requires studios
        if igazolas_type.requires_studios and not profile.is_studios:
            return 400, {
                'error': 'Permission denied',
                'detail': 'This igazolás type requires stúdiós status'
            }
    except IgazolasTipus.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'IgazolasTipus with id {tipus} does not exist'
        }
    
    # Get current user's class to verify classmates
    osztaly = profile.osztalyom()
    if not osztaly:
        return 400, {
            'error': 'Invalid request',
            'detail': 'You must be in a class to create group igazolás'
        }
    
    # Verify all additional students are classmates
    classmate_ids = set(osztaly.tanulok.all().values_list('id', flat=True))
    for student_id in additional_student_ids:
        if student_id not in classmate_ids:
            return 400, {
                'error': 'Invalid students',
                'detail': f'Student {student_id} is not your classmate'
            }
    
    # Generate group ID
    import uuid
    group_id = uuid.uuid4()
    
    # Create igazolások in a transaction
    created_igazolasok = []
    
    with transaction.atomic():
        # Create igazolás for group leader (current user)
        leader_igazolas = Igazolas.objects.create(
            profile=profile,
            eleje=eleje,
            vege=vege,
            tipus=igazolas_type,
            megjegyzes_diak=megjegyzes_diak,
            imgDriveURL=imgDriveURL,
            bkk_verification=bkk_verification,
            sub_form_data=sub_form_data,  # New field
            diak=True,
            ftv=False,
            group_id=group_id,
            is_group_leader=True,
            group_member_count=len(additional_student_ids) + 1,
            created_by_group_leader=request.auth
        )
        created_igazolasok.append(leader_igazolas)
        
        # Create igazolások for other group members
        for student_id in additional_student_ids:
            student = User.objects.get(id=student_id)
            student_profile, _ = Profile.objects.get_or_create(user=student)
            
            member_igazolas = Igazolas.objects.create(
                profile=student_profile,
                eleje=eleje,
                vege=vege,
                tipus=igazolas_type,
                megjegyzes_diak=megjegyzes_diak,
                imgDriveURL=imgDriveURL,
                bkk_verification=bkk_verification,
                sub_form_data=sub_form_data,  # New field
                diak=False,  # Not created by student themselves
                ftv=False,
                group_id=group_id,
                is_group_leader=False,
                group_member_count=len(additional_student_ids) + 1,
                created_by_group_leader=request.auth
            )
            created_igazolasok.append(member_igazolas)
    
    logger.info(f"User {request.auth.username} created group igazolás with {len(created_igazolasok)} members (group_id: {group_id})")
    
    # Build response
    igazolasok_data = []
    for ig in created_igazolasok:
        igazolasok_data.append({
            'id': ig.id,
            'profile_id': ig.profile.id,
            'student_name': ig.profile.user.get_full_name() or ig.profile.user.username,
            'is_group_leader': ig.is_group_leader,
            'status': ig.allapot
        })
    
    return 201, {
        'created_count': len(created_igazolasok),
        'group_id': str(group_id),
        'igazolasok': igazolasok_data,
        'message': f'Successfully created {len(created_igazolasok)} group igazolások'
    }


@api.get("/igazolasok/{igazolas_id}/group-members", response={200: dict, 404: ErrorResponse}, auth=jwt_auth, tags=["Group Absences"])
def get_group_members(request, igazolas_id: int):
    """
    Get all group members for a specific igazolás.
    
    Requires authentication.
    """
    try:
        igazolas = Igazolas.objects.get(id=igazolas_id)
        
        if not igazolas.group_id:
            return 200, {
                'group_leader': None,
                'members': [],
                'group_id': None,
                'total_members': 1,
                'message': 'This is not a group igazolás'
            }
        
        # Get all igazolások in the same group
        group_igazolasok = Igazolas.objects.filter(
            group_id=igazolas.group_id
        ).select_related('profile', 'profile__user')
        
        group_leader = None
        members = []
        
        for ig in group_igazolasok:
            member_info = {
                'id': ig.id,
                'profile_id': ig.profile.id,
                'student_name': ig.profile.user.get_full_name() or ig.profile.user.username,
                'status': ig.allapot,
                'is_group_leader': ig.is_group_leader
            }
            
            if ig.is_group_leader:
                group_leader = member_info
            
            members.append(member_info)
        
        return 200, {
            'group_leader': group_leader,
            'members': members,
            'group_id': str(igazolas.group_id),
            'total_members': len(members)
        }
        
    except Igazolas.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Igazolas with id {igazolas_id} does not exist'
        }


# Feature #27: Class Period Configuration

@api.get("/classes/{class_id}/period-config", response={200: dict, 401: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Period Configuration"])
def get_class_period_config(request, class_id: int):
    """
    Get period configuration for a specific class.
    
    Requires authentication.
    """
    try:
        osztaly = Osztaly.objects.get(id=class_id)
        
        # Default periods if not configured
        enabled = osztaly.enabled_periods if osztaly.enabled_periods else [1, 2, 3, 4, 5, 6, 7, 8, 9]
        all_periods = [1, 2, 3, 4, 5, 6, 7, 8, 9]
        disabled = [p for p in all_periods if p not in enabled]
        
        return 200, {
            'class_id': osztaly.id,
            'class_name': str(osztaly),
            'enabled_periods': enabled,
            'disabled_periods': disabled
        }
        
    except Osztaly.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Class with id {class_id} does not exist'
        }


@api.put("/classes/{class_id}/period-config", response={200: dict, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Period Configuration"])
def update_class_period_config(request, class_id: int, enabled_periods: List[int] = Body(...)):
    """
    Update period configuration for a specific class.
    
    Only class teachers (osztályfőnök) can update their class configuration.
    Requires authentication.
    """
    # Check if user is osztályfőnök
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers can modify period configuration'
        }
    
    # Get teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class or teacher_class.id != class_id:
        return 403, {
            'error': 'Forbidden',
            'detail': 'You can only modify your own class configuration'
        }
    
    # Validate enabled_periods
    if not enabled_periods:
        return 400, {
            'error': 'Invalid request',
            'detail': 'enabled_periods cannot be empty'
        }
    
    valid_periods = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    for period in enabled_periods:
        if period not in valid_periods:
            return 400, {
                'error': 'Invalid period',
                'detail': f'Period {period} is not valid. Must be between 1-9'
            }
    
    # Update configuration
    teacher_class.enabled_periods = sorted(enabled_periods)
    teacher_class.save(update_fields=['enabled_periods'])
    
    all_periods = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    disabled = [p for p in all_periods if p not in enabled_periods]
    
    logger.info(f"User {request.auth.username} updated period config for class {teacher_class}: enabled={enabled_periods}")
    
    return 200, {
        'class_id': teacher_class.id,
        'class_name': str(teacher_class),
        'enabled_periods': sorted(enabled_periods),
        'disabled_periods': disabled,
        'message': 'Period configuration updated successfully'
    }


@api.get("/classes/{class_id}/period-usage-analysis", response={200: dict, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Period Configuration"])
def analyze_class_period_usage(request, class_id: int):
    """
    Analyze actual period usage from FTV data to help configure periods.
    
    Only class teachers can access this for their own class.
    Requires authentication.
    """
    # Check if user is osztályfőnők
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers can view period usage analysis'
        }
    
    # Get teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class or teacher_class.id != class_id:
        return 403, {
            'error': 'Forbidden',
            'detail': 'You can only analyze your own class'
        }
    
    # Analyze mulasztások to see which periods are actually used
    from django.db.models import Count
    
    student_ids = teacher_class.tanulok.values_list('id', flat=True)
    
    # Count mulasztások per period (ora field)
    period_usage = {}
    for period in range(0, 10):  # 0-9
        count = Mulasztas.objects.filter(
            uploaded_by_student_id__in=student_ids,
            ora=period
        ).count()
        
        period_usage[period] = {
            'ora': period,
            'usage_count': count,
            'has_lessons': count > 0
        }
    
    # Generate recommendations (periods with 0 usage could be disabled)
    recommendations = [p for p, data in period_usage.items() if not data['has_lessons'] and p > 0]
    
    periods_list = [period_usage[p] for p in sorted(period_usage.keys()) if p > 0]  # Exclude period 0
    
    logger.info(f"User {request.auth.username} analyzed period usage for class {teacher_class}")
    
    return 200, {
        'class_id': teacher_class.id,
        'class_name': str(teacher_class),
        'periods': periods_list,
        'recommendations': recommendations
    }


# ============================================================================
# Academic Year & Bulk Operations
# ============================================================================

# Feature #14: Academic Year Archival

@api.post("/admin/academic-year/archive", response={200: dict, 400: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Academic Year"])
def archive_academic_year(request, year_start: int, archive_classes: bool = True, archive_igazolasok: bool = True):
    """
    Archive an academic year (mark as archived, don't delete).
    
    Archives classes, students, and igazolások for a specific academic year.
    This preserves data but marks it as historical.
    
    Requires superuser permission.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can archive academic years'
        }
    
    academic_year = f"{year_start}/{year_start + 1}"
    archived_counts = {
        'classes': 0,
        'students': 0,
        'igazolasok': 0
    }
    
    with transaction.atomic():
        # Archive classes that started in this year
        if archive_classes:
            classes_to_archive = Osztaly.objects.filter(
                kezdes_eve=year_start,
                archived=False
            )
            
            for osztaly in classes_to_archive:
                osztaly.archived = True
                osztaly.archive_date = timezone.now()
                osztaly.academic_year = academic_year
                osztaly.save()
                archived_counts['classes'] += 1
                
                # Archive students in these classes
                for student in osztaly.tanulok.all():
                    try:
                        profile = Profile.objects.get(user=student)
                        if not profile.archived:
                            profile.archived = True
                            profile.archive_date = timezone.now()
                            profile.academic_year = academic_year
                            profile.save()
                            archived_counts['students'] += 1
                    except Profile.DoesNotExist:
                        continue
        
        # Archive igazolások from this academic year
        if archive_igazolasok:
            # Academic year typically runs Sep year_start to Jun year_start+1
            start_date = datetime(year_start, 9, 1).date()
            end_date = datetime(year_start + 1, 6, 30).date()
            
            igazolasok_to_archive = Igazolas.objects.filter(
                rogzites_datuma__gte=start_date,
                rogzites_datuma__lte=end_date,
                archived=False
            )
            
            for igazolas in igazolasok_to_archive:
                igazolas.archived = True
                igazolas.academic_year = academic_year
                igazolas.save()
                archived_counts['igazolasok'] += 1
    
    logger.info(f"User {request.auth.username} archived academic year {academic_year}: {archived_counts}")
    
    return 200, {
        'archived_count': archived_counts,
        'message': f"Successfully archived academic year {academic_year}"
    }


@api.get("/admin/academic-year/archived", response={200: List[dict], 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Academic Year"])
def get_archived_years(request):
    """
    Get list of all archived academic years with statistics.
    
    Requires superuser permission.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view archived years'
        }
    
    # Get distinct academic years from archived data
    archived_years = set()
    
    # From classes
    for year in Osztaly.objects.filter(archived=True).exclude(academic_year__isnull=True).values_list('academic_year', flat=True).distinct():
        archived_years.add(year)
    
    # From igazolások
    for year in Igazolas.objects.filter(archived=True).exclude(academic_year__isnull=True).values_list('academic_year', flat=True).distinct():
        archived_years.add(year)
    
    # Build response
    years_data = []
    for year in sorted(archived_years, reverse=True):
        class_count = Osztaly.objects.filter(academic_year=year, archived=True).count()
        student_count = Profile.objects.filter(academic_year=year, archived=True).count()
        igazolasok_count = Igazolas.objects.filter(academic_year=year, archived=True).count()
        
        years_data.append({
            'year': year,
            'class_count': class_count,
            'student_count': student_count,
            'igazolasok_count': igazolasok_count
        })
    
    return 200, years_data


@api.get("/admin/academic-year/{year}/data", response={200: dict, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Academic Year"])
def get_archived_year_data(request, year: str):
    """
    Get full archived data for a specific academic year.
    
    Returns classes, students, and igazolások for read-only viewing.
    Requires superuser permission.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can view archived year data'
        }
    
    # Get archived classes
    classes = Osztaly.objects.filter(academic_year=year, archived=True)
    if not classes.exists():
        return 404, {
            'error': 'Not found',
            'detail': f'No archived data found for academic year {year}'
        }
    
    classes_data = []
    for osztaly in classes:
        classes_data.append({
            'id': osztaly.id,
            'name': str(osztaly),
            'tagozat': osztaly.tagozat,
            'kezdes_eve': osztaly.kezdes_eve,
            'student_count': osztaly.tanulok.count(),
            'archive_date': osztaly.archive_date
        })
    
    # Get archived students
    students_data = []
    profiles = Profile.objects.filter(academic_year=year, archived=True).select_related('user')
    for profile in profiles:
        students_data.append({
            'id': profile.user.id,
            'username': profile.user.username,
            'full_name': profile.user.get_full_name() or profile.user.username,
            'email': profile.user.email,
            'archive_date': profile.archive_date
        })
    
    # Get archived igazolások
    igazolasok_data = []
    igazolasok = Igazolas.objects.filter(academic_year=year, archived=True).select_related('profile', 'tipus')[:100]  # Limit to 100
    for igazolas in igazolasok:
        igazolasok_data.append({
            'id': igazolas.id,
            'student': igazolas.profile.user.get_full_name() or igazolas.profile.user.username,
            'tipus': igazolas.tipus.nev,
            'eleje': igazolas.eleje,
            'vege': igazolas.vege,
            'allapot': igazolas.allapot,
            'rogzites_datuma': igazolas.rogzites_datuma
        })
    
    logger.info(f"User {request.auth.username} viewed archived year data for {year}")
    
    return 200, {
        'year': year,
        'classes': classes_data,
        'students': students_data,
        'igazolasok': igazolasok_data[:100],  # Return max 100 for performance
        'total_igazolasok': Igazolas.objects.filter(academic_year=year, archived=True).count()
    }


# Feature #9: Bulk Assignment Tool

@api.post("/admin/bulk/create-students-with-passwords", response={201: dict, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Bulk Operations"])
def bulk_create_students(request, emails: List[str] = Body(...), class_id: int = Body(...)):
    """
    Bulk create students with auto-generated passwords.
    
    Creates user accounts, profiles, and assigns to specified class.
    Returns generated credentials for distribution.
    
    Requires superuser permission.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can bulk create students'
        }
    
    # Verify class exists
    try:
        osztaly = Osztaly.objects.get(id=class_id)
    except Osztaly.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Class with id {class_id} does not exist'
        }
    
    if not emails:
        return 400, {
            'error': 'Invalid request',
            'detail': 'No emails provided'
        }
    
    created = []
    failed = []
    
    for email in emails:
        try:
            # Validate email
            if not email or '@' not in email:
                failed.append(f"{email} - Invalid email format")
                continue
            
            # Generate username from email
            username = email.split('@')[0]
            
            # Check if user exists
            if User.objects.filter(username=username).exists():
                failed.append(f"{email} - Username '{username}' already exists")
                continue
            
            if User.objects.filter(email=email).exists():
                failed.append(f"{email} - Email already exists")
                continue
            
            # Generate strong password
            password = generate_strong_password()
            
            # Create user and profile
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password
                )
                
                Profile.objects.create(user=user)
                osztaly.tanulok.add(user)
                
                first_login_url = f"{settings.FRONTEND_URL}/login?username={username}"
                
                created.append({
                    'id': user.id,
                    'email': email,
                    'username': username,
                    'password': password,
                    'first_login_url': first_login_url
                })
                
        except Exception as e:
            failed.append(f"{email} - {str(e)}")
            logger.error(f"Failed to create user for {email}: {str(e)}")
    
    logger.info(f"User {request.auth.username} bulk created {len(created)} students for class {osztaly}")
    
    return 201, {
        'created': created,
        'failed': failed,
        'message': f'Created {len(created)} students, {len(failed)} failed'
    }


@api.post("/admin/academic-year/create-class", response={201: dict, 400: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Bulk Operations"])
def create_class_with_students(request, tagozat: str = Body(...), kezdes_eve: int = Body(...), 
                               teacher_email: str = Body(...), student_emails: List[str] = Body(...)):
    """
    Create a new class with teacher and students in one operation.
    
    Useful for setting up a new academic year.
    Requires superuser permission.
    """
    if not request.auth.is_superuser:
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only superusers can create classes'
        }
    
    # Validate inputs
    if not tagozat or len(tagozat) != 1:
        return 400, {
            'error': 'Invalid input',
            'detail': 'Tagozat must be a single letter (e.g., A, B, C)'
        }
    
    # Find teacher
    try:
        teacher = User.objects.get(email=teacher_email, is_active=True)
    except User.DoesNotExist:
        return 400, {
            'error': 'Teacher not found',
            'detail': f'No active user found with email {teacher_email}'
        }
    
    created_students = []
    
    with transaction.atomic():
        # Create class
        osztaly = Osztaly.objects.create(
            tagozat=tagozat.upper(),
            kezdes_eve=kezdes_eve
        )
        
        # Assign teacher
        osztaly.osztalyfonokok.add(teacher)
        
        # Create students
        for email in student_emails:
            try:
                username = email.split('@')[0]
                
                if User.objects.filter(username=username).exists() or User.objects.filter(email=email).exists():
                    continue
                
                password = generate_strong_password()
                
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password
                )
                
                Profile.objects.create(user=user)
                osztaly.tanulok.add(user)
                
                created_students.append({
                    'id': user.id,
                    'email': email,
                    'username': username,
                    'password': password,
                    'first_login_url': f"{settings.FRONTEND_URL}/login?username={username}"
                })
                
            except Exception as e:
                logger.error(f"Failed to create student {email}: {str(e)}")
    
    logger.info(f"User {request.auth.username} created class {osztaly} with {len(created_students)} students")
    
    return 201, {
        'class_id': osztaly.id,
        'created_students': created_students,
        'teacher_assigned': True,
        'message': f'Successfully created class {osztaly} with {len(created_students)} students'
    }


# Feature #26: Teacher-Created Igazolások

@api.get("/teachers/students/eligible-for-igazolas", response={200: List[dict], 401: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Teacher - Create Igazolas"])
def get_eligible_students_for_teacher(request):
    """
    Get list of students eligible for teacher-created igazolás.
    
    Returns students from teacher's class(es).
    Requires teacher (osztályfőnök) authentication.
    """
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers can create igazolások for students'
        }
    
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'No class found for this teacher'
        }
    
    # Get students from teacher's class
    students = teacher_class.tanulok.all().select_related('profile')
    
    students_data = []
    for student in students:
        # Count recent igazolások (last 30 days)
        recent_count = Igazolas.objects.filter(
            profile__user=student,
            rogzites_datuma__gte=(timezone.now() - timedelta(days=30)).date()
        ).count()
        
        full_name = f"{student.last_name} {student.first_name}" if student.last_name and student.first_name else student.username
        
        students_data.append({
            'id': student.id,
            'username': student.username,
            'full_name': full_name,
            'class_name': str(teacher_class),
            'recent_absences': recent_count
        })
    
    return 200, students_data


@api.post("/teachers/igazolasok/create-for-student", response={201: dict, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Teacher - Create Igazolas"])
def teacher_create_igazolas_for_student(request, student_id: int = Body(...), eleje: datetime = Body(...), 
                                       vege: datetime = Body(...), tipus: int = Body(...),
                                       megjegyzes_diak: Optional[str] = Body(None)):
    """
    Teacher creates igazolás on behalf of a student.
    
    Marked as teacher-created (diak=False, ftv=False).
    Requires teacher (osztályfőnök) authentication.
    """
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers can create igazolások for students'
        }
    
    # Validate dates
    if eleje >= vege:
        return 400, {
            'error': 'Validation error',
            'detail': 'End time must be after start time'
        }
    
    # Get teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'No class found for this teacher'
        }
    
    # Verify student is in teacher's class
    try:
        student = User.objects.get(id=student_id)
        if student not in teacher_class.tanulok.all():
            return 403, {
                'error': 'Forbidden',
                'detail': 'Student is not in your class'
            }
    except User.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'Student with id {student_id} does not exist'
        }
    
    # Get student profile
    student_profile, _ = Profile.objects.get_or_create(user=student)
    
    # Verify tipus exists
    try:
        igazolas_type = IgazolasTipus.objects.get(id=tipus)
    except IgazolasTipus.DoesNotExist:
        return 404, {
            'error': 'Not found',
            'detail': f'IgazolasTipus with id {tipus} does not exist'
        }
    
    # Create igazolás (teacher-created)
    igazolas = Igazolas.objects.create(
        profile=student_profile,
        eleje=eleje,
        vege=vege,
        tipus=igazolas_type,
        megjegyzes_diak=megjegyzes_diak,
        diak=False,  # Teacher-created
        ftv=False,
        korrigalt=False,
        allapot='Elfogadva'  # Auto-approve teacher-created
    )
    
    logger.info(f"Teacher {request.auth.username} created igazolás #{igazolas.id} for student {student.username}")
    
    return 201, {
        'id': igazolas.id,
        'student': student.get_full_name() or student.username,
        'message': f'Successfully created igazolás for {student.get_full_name() or student.username}'
    }


@api.post("/teachers/igazolasok/create-bulk", response={201: dict, 400: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Teacher - Create Igazolas"])
def teacher_bulk_create_igazolas(request, student_ids: List[int] = Body(...), eleje: datetime = Body(...),
                                 vege: datetime = Body(...), tipus: int = Body(...),
                                 megjegyzes_diak: Optional[str] = Body(None)):
    """
    Teacher creates igazolások for multiple students (e.g., entire class at event).
    
    All igazolások marked as teacher-created and auto-approved.
    Requires teacher (osztályfőnök) authentication.
    """
    if not is_class_teacher(request.auth):
        return 403, {
            'error': 'Forbidden',
            'detail': 'Only class teachers can create igazolások for students'
        }
    
    if not student_ids:
        return 400, {
            'error': 'Invalid request',
            'detail': 'No students provided'
        }
    
    # Get teacher's class
    teacher_class = get_teacher_class(request.auth)
    if not teacher_class:
        return 403, {
            'error': 'Forbidden',
            'detail': 'No class found for this teacher'
        }
    
    # Verify tipus exists
    try:
        igazolas_type = IgazolasTipus.objects.get(id=tipus)
    except IgazolasTipus.DoesNotExist:
        return 400, {
            'error': 'Not found',
            'detail': f'IgazolasTipus with id {tipus} does not exist'
        }
    
    created_igazolasok = []
    failed = []
    
    classmate_ids = set(teacher_class.tanulok.values_list('id', flat=True))
    
    for student_id in student_ids:
        try:
            if student_id not in classmate_ids:
                failed.append({'id': student_id, 'reason': 'Not in your class'})
                continue
            
            student = User.objects.get(id=student_id)
            student_profile, _ = Profile.objects.get_or_create(user=student)
            
            igazolas = Igazolas.objects.create(
                profile=student_profile,
                eleje=eleje,
                vege=vege,
                tipus=igazolas_type,
                megjegyzes_diak=megjegyzes_diak,
                diak=False,
                ftv=False,
                korrigalt=False,
                allapot='Elfogadva'
            )
            
            created_igazolasok.append({
                'id': igazolas.id,
                'student_id': student.id,
                'student_name': student.get_full_name() or student.username
            })
            
        except Exception as e:
            failed.append({'id': student_id, 'reason': str(e)})
            logger.error(f"Failed to create igazolás for student {student_id}: {str(e)}")
    
    logger.info(f"Teacher {request.auth.username} bulk created {len(created_igazolasok)} igazolások")
    
    return 201, {
        'created_count': len(created_igazolasok),
        'igazolasok': created_igazolasok,
        'failed': failed,
        'message': f'Created {len(created_igazolasok)} igazolások, {len(failed)} failed'
    }


# =======================
# Feature #11: API Performance Metrics
# =======================

@api.get("/admin/system/api-metrics", response={200: dict, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - System Management"])
def get_api_metrics(request, from_date: Optional[str] = None, to_date: Optional[str] = None):
    """
    Get API performance metrics including average response time, request count, and error rates.
    
    Query params:
    - from_date: Start date (YYYY-MM-DD)
    - to_date: End date (YYYY-MM-DD)
    """
    from .models import APIMetrics
    from .schemas import APIMetricsResponse, APIMetricsEndpoint
    
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can view API metrics'}
    
    # Parse dates
    queryset = APIMetrics.objects.all()
    
    if from_date:
        try:
            from_dt = datetime.strptime(from_date, '%Y-%m-%d')
            queryset = queryset.filter(recorded_at__gte=from_dt)
        except ValueError:
            pass
    
    if to_date:
        try:
            to_dt = datetime.strptime(to_date, '%Y-%m-%d')
            queryset = queryset.filter(recorded_at__lte=to_dt)
        except ValueError:
            pass
    
    # Aggregate metrics by endpoint
    from django.db.models import Avg, Sum
    
    endpoint_metrics = {}
    for metric in queryset:
        key = f"{metric.http_method}:{metric.endpoint_path}"
        
        if key not in endpoint_metrics:
            endpoint_metrics[key] = {
                'path': metric.endpoint_path,
                'method': metric.http_method,
                'total_requests': 0,
                'total_errors': 0,
                'response_times': [],
                'p95_times': []
            }
        
        endpoint_metrics[key]['total_requests'] += metric.request_count
        endpoint_metrics[key]['total_errors'] += metric.error_count
        endpoint_metrics[key]['response_times'].append(metric.avg_response_ms)
        if metric.p95_response_ms:
            endpoint_metrics[key]['p95_times'].append(metric.p95_response_ms)
    
    # Calculate aggregated metrics
    endpoints = []
    for key, data in endpoint_metrics.items():
        avg_response = sum(data['response_times']) / len(data['response_times']) if data['response_times'] else 0
        avg_p95 = sum(data['p95_times']) / len(data['p95_times']) if data['p95_times'] else None
        error_rate = (data['total_errors'] / data['total_requests'] * 100) if data['total_requests'] > 0 else 0
        
        endpoints.append({
            'path': data['path'],
            'method': data['method'],
            'avg_response_ms': round(avg_response, 2),
            'request_count': data['total_requests'],
            'error_count': data['total_errors'],
            'p95_response_ms': round(avg_p95, 2) if avg_p95 else None,
            'error_rate': round(error_rate, 2)
        })
    
    # Sort for slowest and most used
    slowest = sorted(endpoints, key=lambda x: x['avg_response_ms'], reverse=True)[:10]
    most_used = sorted(endpoints, key=lambda x: x['request_count'], reverse=True)[:10]
    
    total_requests = sum(e['request_count'] for e in endpoints)
    avg_response_time = sum(e['avg_response_ms'] * e['request_count'] for e in endpoints) / total_requests if total_requests > 0 else 0
    
    return 200, {
        'endpoints': endpoints,
        'slowest_endpoints': slowest,
        'most_used': most_used,
        'total_requests': total_requests,
        'average_response_time': round(avg_response_time, 2),
        'from_date': from_date,
        'to_date': to_date
    }


@api.post("/admin/system/api-metrics/refresh", response={200: dict, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - System Management"])
def refresh_api_metrics(request):
    """
    Manually trigger API metrics refresh.
    This endpoint is called by frontend tests to populate metrics data.
    """
    from .models import APIMetrics
    
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can refresh API metrics'}
    
    # This is a placeholder - in production, this would aggregate real metrics
    # For now, we just acknowledge the request
    logger.info(f"API metrics refresh requested by {request.auth.username}")
    
    return 200, {
        'message': 'API metrics refresh initiated',
        'recorded_count': 0
    }


# =======================
# Feature #15: Manual Attendance Management
# =======================

@api.post("/admin/attendance/create", response={201: dict, 400: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Attendance"])
def create_attendance(request, payload: dict):
    """
    Manually create an attendance record.
    """
    from .schemas import AttendanceCreateRequest, AttendanceResponse
    
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can create attendance records'}
    
    try:
        student = User.objects.get(id=payload['student_id'])
        student_profile, _ = Profile.objects.get_or_create(user=student)
    except User.DoesNotExist:
        return 400, {'error': 'Bad request', 'detail': 'Student not found'}
    
    # Parse date
    try:
        datum = datetime.strptime(payload['datum'], '%Y-%m-%d').date()
    except ValueError:
        return 400, {'error': 'Bad request', 'detail': 'Invalid date format. Use YYYY-MM-DD'}
    
    # Check for igazolas if provided
    igazolas = None
    if payload.get('igazolas_id'):
        try:
            igazolas = Igazolas.objects.get(id=payload['igazolas_id'])
        except Igazolas.DoesNotExist:
            return 400, {'error': 'Bad request', 'detail': 'Igazolás not found'}
    
    # Create attendance record
    mulasztas = Mulasztas.objects.create(
        datum=datum,
        ora=payload['ora'],
        tantargy=payload['tantargy'],
        tema=payload['tema'],
        tipus=payload['tipus'],
        igazolt=payload['igazolt'],
        igazolas_tipusa=igazolas.tipus.nev if igazolas else None,
        matched_igazolas=igazolas
    )
    
    # Add to igazolas if provided
    if igazolas:
        igazolas.mulasztasok.add(mulasztas)
    
    # Add to student profile
    student_profile.mulasztasok.add(mulasztas)
    
    logger.info(f"Superuser {request.auth.username} created attendance record {mulasztas.id} for student {student.username}")
    
    return 201, {
        'id': mulasztas.id,
        'student_id': student.id,
        'student_name': student.get_full_name() or student.username,
        'datum': str(mulasztas.datum),
        'ora': mulasztas.ora,
        'tantargy': mulasztas.tantargy,
        'tema': mulasztas.tema,
        'tipus': mulasztas.tipus,
        'igazolt': mulasztas.igazolt,
        'igazolas_id': igazolas.id if igazolas else None,
        'igazolas_tipusa': mulasztas.igazolas_tipusa,
        'rogzites_datuma': str(mulasztas.rogzites_datuma),
        'message': 'Attendance record created successfully'
    }


@api.put("/admin/attendance/{attendance_id}", response={200: dict, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Attendance"])
def update_attendance(request, attendance_id: int, payload: dict):
    """
    Update an existing attendance record.
    """
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can update attendance records'}
    
    try:
        mulasztas = Mulasztas.objects.get(id=attendance_id)
    except Mulasztas.DoesNotExist:
        return 404, {'error': 'Not found', 'detail': 'Attendance record not found'}
    
    # Update fields if provided
    if 'datum' in payload:
        try:
            mulasztas.datum = datetime.strptime(payload['datum'], '%Y-%m-%d').date()
        except ValueError:
            return 400, {'error': 'Bad request', 'detail': 'Invalid date format. Use YYYY-MM-DD'}
    
    if 'ora' in payload:
        mulasztas.ora = payload['ora']
    if 'tantargy' in payload:
        mulasztas.tantargy = payload['tantargy']
    if 'tema' in payload:
        mulasztas.tema = payload['tema']
    if 'tipus' in payload:
        mulasztas.tipus = payload['tipus']
    if 'igazolt' in payload:
        mulasztas.igazolt = payload['igazolt']
    
    # Update igazolas link if provided
    if 'igazolas_id' in payload:
        if payload['igazolas_id']:
            try:
                igazolas = Igazolas.objects.get(id=payload['igazolas_id'])
                mulasztas.matched_igazolas = igazolas
                mulasztas.igazolas_tipusa = igazolas.tipus.nev
                igazolas.mulasztasok.add(mulasztas)
            except Igazolas.DoesNotExist:
                return 400, {'error': 'Bad request', 'detail': 'Igazolás not found'}
        else:
            # Remove igazolas link
            if mulasztas.matched_igazolas:
                mulasztas.matched_igazolas.mulasztasok.remove(mulasztas)
            mulasztas.matched_igazolas = None
            mulasztas.igazolas_tipusa = None
    
    mulasztas.save()
    
    logger.info(f"Superuser {request.auth.username} updated attendance record {mulasztas.id}")
    
    # Get student info
    student_profile = mulasztas.profile_set.first()
    student = student_profile.user if student_profile else None
    
    return 200, {
        'id': mulasztas.id,
        'student_id': student.id if student else None,
        'student_name': student.get_full_name() or student.username if student else 'Unknown',
        'datum': str(mulasztas.datum),
        'ora': mulasztas.ora,
        'tantargy': mulasztas.tantargy,
        'tema': mulasztas.tema,
        'tipus': mulasztas.tipus,
        'igazolt': mulasztas.igazolt,
        'igazolas_id': mulasztas.matched_igazolas.id if mulasztas.matched_igazolas else None,
        'igazolas_tipusa': mulasztas.igazolas_tipusa,
        'rogzites_datuma': str(mulasztas.rogzites_datuma),
        'message': 'Attendance record updated successfully'
    }


@api.delete("/admin/attendance/{attendance_id}", response={200: dict, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Attendance"])
def delete_attendance(request, attendance_id: int):
    """
    Delete an attendance record.
    """
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can delete attendance records'}
    
    try:
        mulasztas = Mulasztas.objects.get(id=attendance_id)
    except Mulasztas.DoesNotExist:
        return 404, {'error': 'Not found', 'detail': 'Attendance record not found'}
    
    mulasztas_id = mulasztas.id
    mulasztas.delete()
    
    logger.info(f"Superuser {request.auth.username} deleted attendance record {mulasztas_id}")
    
    return 200, {
        'message': f'Attendance record {mulasztas_id} deleted successfully'
    }


@api.get("/admin/attendance/student/{student_id}", response={200: dict, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Attendance"])
def get_student_attendance(request, student_id: int, from_date: Optional[str] = None, to_date: Optional[str] = None):
    """
    Get all attendance records for a specific student.
    """
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can view student attendance'}
    
    try:
        student = User.objects.get(id=student_id)
        student_profile, _ = Profile.objects.get_or_create(user=student)
    except User.DoesNotExist:
        return 404, {'error': 'Not found', 'detail': 'Student not found'}
    
    # Get attendance records
    mulasztasok = student_profile.mulasztasok.all()
    
    # Filter by date range if provided
    if from_date:
        try:
            from_dt = datetime.strptime(from_date, '%Y-%m-%d').date()
            mulasztasok = mulasztasok.filter(datum__gte=from_dt)
        except ValueError:
            return 400, {'error': 'Bad request', 'detail': 'Invalid from_date format. Use YYYY-MM-DD'}
    
    if to_date:
        try:
            to_dt = datetime.strptime(to_date, '%Y-%m-%d').date()
            mulasztasok = mulasztasok.filter(datum__lte=to_dt)
        except ValueError:
            return 400, {'error': 'Bad request', 'detail': 'Invalid to_date format. Use YYYY-MM-DD'}
    
    # Build response
    attendance_records = []
    for m in mulasztasok:
        attendance_records.append({
            'id': m.id,
            'student_id': student.id,
            'student_name': student.get_full_name() or student.username,
            'datum': str(m.datum),
            'ora': m.ora,
            'tantargy': m.tantargy,
            'tema': m.tema,
            'tipus': m.tipus,
            'igazolt': m.igazolt,
            'igazolas_id': m.matched_igazolas.id if m.matched_igazolas else None,
            'igazolas_tipusa': m.igazolas_tipusa,
            'rogzites_datuma': str(m.rogzites_datuma)
        })
    
    igazolt_count = sum(1 for m in mulasztasok if m.igazolt)
    igazolatlan_count = len(mulasztasok) - igazolt_count
    
    return 200, {
        'student_id': student.id,
        'student_name': student.get_full_name() or student.username,
        'attendance_records': attendance_records,
        'total_count': len(attendance_records),
        'igazolt_count': igazolt_count,
        'igazolatlan_count': igazolatlan_count
    }


# =======================
# Feature #28: Permission Matrix
# =======================

@api.get("/admin/igazolas-types/permission-matrix", response={200: dict, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Permissions"])
def get_permission_matrix(request):
    """
    Get the complete permission matrix showing which classes can use which igazolás types.
    """
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can view permission matrix'}
    
    # Get all classes and types
    classes = Osztaly.objects.filter(archived=False).prefetch_related('nem_fogadott_igazolas_tipusok')
    types = IgazolasTipus.objects.all().prefetch_related('nem_fogado_osztalyok')
    
    # Build matrix
    matrix = {}
    for osztaly in classes:
        matrix[osztaly.id] = {}
        blocked_types = set(osztaly.nem_fogadott_igazolas_tipusok.values_list('id', flat=True))
        
        for tipus in types:
            # If type is in blocked list, permission is False
            matrix[osztaly.id][tipus.id] = tipus.id not in blocked_types
    
    # Format response
    classes_data = []
    for osztaly in classes:
        classes_data.append({
            'id': osztaly.id,
            'tagozat': osztaly.tagozat,
            'kezdes_eve': osztaly.kezdes_eve,
            'nev': str(osztaly)
        })
    
    types_data = []
    for tipus in types:
        types_data.append({
            'id': tipus.id,
            'nev': tipus.nev,
            'leiras': tipus.leiras,
            'beleszamit': tipus.beleszamit,
            'iskolaerdeku': tipus.iskolaerdeku
        })
    
    return 200, {
        'classes': classes_data,
        'types': types_data,
        'matrix': matrix
    }


@api.post("/admin/igazolas-types/update-permission", response={200: dict, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Permissions"])
def update_permission(request, payload: dict):
    """
    Update a single permission in the matrix.
    """
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can update permissions'}
    
    class_id = payload.get('class_id')
    type_id = payload.get('type_id')
    allowed = payload.get('allowed')
    
    if class_id is None or type_id is None or allowed is None:
        return 400, {'error': 'Bad request', 'detail': 'class_id, type_id, and allowed are required'}
    
    try:
        osztaly = Osztaly.objects.get(id=class_id)
        tipus = IgazolasTipus.objects.get(id=type_id)
    except Osztaly.DoesNotExist:
        return 404, {'error': 'Not found', 'detail': 'Class not found'}
    except IgazolasTipus.DoesNotExist:
        return 404, {'error': 'Not found', 'detail': 'IgazolásTipus not found'}
    
    # Update permission
    if allowed:
        # Remove from blocked list (allow)
        osztaly.nem_fogadott_igazolas_tipusok.remove(tipus)
        message = f'Permission granted: {str(osztaly)} can now use {tipus.nev}'
    else:
        # Add to blocked list (block)
        osztaly.nem_fogadott_igazolas_tipusok.add(tipus)
        message = f'Permission revoked: {str(osztaly)} cannot use {tipus.nev}'
    
    logger.info(f"Superuser {request.auth.username} updated permission: {message}")
    
    return 200, {
        'updated': True,
        'message': message
    }


@api.post("/admin/igazolas-types/bulk-update-permissions", response={200: dict, 400: ErrorResponse, 403: ErrorResponse}, auth=jwt_auth, tags=["Admin - Permissions"])
def bulk_update_permissions(request, payload: dict):
    """
    Update multiple permissions at once.
    """
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can update permissions'}
    
    updates = payload.get('updates', [])
    if not updates:
        return 400, {'error': 'Bad request', 'detail': 'updates list is required'}
    
    updated_count = 0
    failed_count = 0
    
    for update in updates:
        try:
            class_id = update.get('class_id')
            type_id = update.get('type_id')
            allowed = update.get('allowed')
            
            if class_id is None or type_id is None or allowed is None:
                failed_count += 1
                continue
            
            osztaly = Osztaly.objects.get(id=class_id)
            tipus = IgazolasTipus.objects.get(id=type_id)
            
            if allowed:
                osztaly.nem_fogadott_igazolas_tipusok.remove(tipus)
            else:
                osztaly.nem_fogadott_igazolas_tipusok.add(tipus)
            
            updated_count += 1
            
        except (Osztaly.DoesNotExist, IgazolasTipus.DoesNotExist):
            failed_count += 1
            continue
    
    logger.info(f"Superuser {request.auth.username} bulk updated {updated_count} permissions ({failed_count} failed)")
    
    return 200, {
        'updated_count': updated_count,
        'failed_count': failed_count,
        'message': f'Updated {updated_count} permissions, {failed_count} failed'
    }


# =======================
# Feature #8: Multiple Class Support
# =======================

@api.post("/admin/teachers/{teacher_id}/assign-classes", response={200: dict, 400: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Teachers"])
def assign_classes_to_teacher(request, teacher_id: int, payload: dict):
    """
    Assign multiple classes to a teacher.
    Supports primary assignment and temporary delegation.
    """
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can assign classes to teachers'}
    
    try:
        teacher = User.objects.get(id=teacher_id, is_active=True)
    except User.DoesNotExist:
        return 404, {'error': 'Not found', 'detail': 'Teacher not found'}
    
    class_ids = payload.get('class_ids', [])
    is_primary = payload.get('is_primary', False)
    delegation_end_date = payload.get('delegation_end_date')
    
    if not class_ids:
        return 400, {'error': 'Bad request', 'detail': 'class_ids is required'}
    
    # Validate classes exist
    classes = Osztaly.objects.filter(id__in=class_ids, archived=False)
    if classes.count() != len(class_ids):
        return 400, {'error': 'Bad request', 'detail': 'One or more classes not found'}
    
    # Add teacher to all classes
    assigned_classes = []
    for osztaly in classes:
        if teacher not in osztaly.osztalyfonokok.all():
            osztaly.osztalyfonokok.add(teacher)
            assigned_classes.append({
                'id': osztaly.id,
                'class_id': osztaly.id,
                'class_name': str(osztaly),
                'is_primary': is_primary,
                'assigned_date': timezone.now().isoformat(),
                'delegation_end_date': delegation_end_date
            })
    
    logger.info(f"Superuser {request.auth.username} assigned {len(assigned_classes)} classes to teacher {teacher.username}")
    
    return 200, {
        'teacher_id': teacher.id,
        'teacher_name': teacher.get_full_name() or teacher.username,
        'assigned_classes': assigned_classes,
        'message': f'Assigned {len(assigned_classes)} classes to {teacher.username}'
    }


@api.get("/admin/teachers/{teacher_id}/classes", response={200: dict, 403: ErrorResponse, 404: ErrorResponse}, auth=jwt_auth, tags=["Admin - Teachers"])
def get_teacher_classes(request, teacher_id: int):
    """
    Get all classes assigned to a teacher.
    """
    if not request.auth.is_superuser:
        return 403, {'error': 'Forbidden', 'detail': 'Only superusers can view teacher assignments'}
    
    try:
        teacher = User.objects.get(id=teacher_id, is_active=True)
    except User.DoesNotExist:
        return 404, {'error': 'Not found', 'detail': 'Teacher not found'}
    
    # Get all classes where this teacher is osztalyfonok
    classes = Osztaly.objects.filter(osztalyfonokok=teacher, archived=False)
    
    classes_data = []
    for osztaly in classes:
        # For now, we don't have delegation tracking in the model
        # This is a simplified response
        classes_data.append({
            'id': osztaly.id,
            'class_id': osztaly.id,
            'class_name': osztaly.nev,
            'is_primary': True,  # Default assumption
            'assigned_date': 'N/A',  # Would need tracking table
            'delegation_end_date': None
        })
    
    return 200, {
        'teacher_id': teacher.id,
        'teacher_name': teacher.get_full_name() or teacher.username,
        'classes': classes_data
    }

