"""
FTV Sync Utility Functions

Handles synchronization with FTV Forgatásszervező platform
"""
import logging
import requests
from datetime import datetime
from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from typing import Dict, List, Optional

from .models import Osztaly, Profile, Igazolas, IgazolasTipus, FTVSyncMetadata

logger = logging.getLogger(__name__)

FTV_BASE_URL = "https://ftvapi.szlg.info/api/sync"


class FTVSyncError(Exception):
    """Custom exception for FTV sync errors"""
    pass


def get_cache_metadata(sync_type: str = 'base') -> Dict:
    """
    Get FTV sync metadata for responses.
    Uses database model for persistent tracking.
    
    Args:
        sync_type: Type of sync ('base', 'user_{user_id}', 'class_{osztaly_id}')
    
    Returns dict with last_sync_time, status, and age information.
    """
    return FTVSyncMetadata.get_metadata(sync_type)


def update_cache_metadata(sync_type: str, status: str, stats: dict = None):
    """
    Update sync metadata in database.
    
    Args:
        sync_type: Type of sync ('base', 'user_{user_id}', 'class_{osztaly_id}')
        status: 'success' or 'failed'
        stats: Optional statistics dictionary
    """
    try:
        print(f"\n{'='*60}")
        print(f"→ UPDATING metadata for '{sync_type}': status={status}")
        print(f"{'='*60}\n")
        logger.info(f"→ UPDATING metadata for '{sync_type}': status={status}")
        
        FTVSyncMetadata.update_sync(sync_type, status, stats)
        
        print(f"\n{'='*60}")
        print(f"✓ Metadata SUCCESSFULLY updated for '{sync_type}'")
        print(f"{'='*60}\n")
        logger.info(f"✓ Metadata SUCCESSFULLY updated for '{sync_type}'")
        
        result = FTVSyncMetadata.get_metadata(sync_type)
        print(f"Verification read: {result}\n")
        logger.info(f"✓ Verification read: {result}")
        return result
    except Exception as e:
        print(f"\n{'='*60}")
        print(f"✗ FAILED to update metadata for '{sync_type}': {e}")
        print(f"{'='*60}\n")
        logger.error(f"✗ FAILED to update metadata for '{sync_type}': {e}")
        import traceback
        traceback.print_exc()
        logger.error(traceback.format_exc())
        raise


def get_ftv_headers() -> Dict[str, str]:
    """Get headers for FTV API requests"""
    token = settings.FTV_EXTERNAL_ACCESS_TOKEN
    if not token:
        raise FTVSyncError("FTV_EXTERNAL_ACCESS_TOKEN not configured")
    
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }


def fetch_ftv_base_sync(debug_performance: bool = False) -> Dict:
    """
    Fetch base sync data from FTV (classes + student users).
    
    Args:
        debug_performance: Whether to request performance data from FTV
    
    Returns:
        Dict with 'osztalyok', 'students', and optionally 'performance'
    """
    try:
        params = {}
        if debug_performance:
            params['debug-performance'] = 'true'
            
        response = requests.get(
            f"{FTV_BASE_URL}/base",
            headers=get_ftv_headers(),
            params=params,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching FTV base sync: {str(e)}")
        raise FTVSyncError(f"Failed to fetch FTV base sync: {str(e)}")


def fetch_ftv_user_absences(user_id: int, debug_performance: bool = False) -> List[Dict]:
    """
    Fetch absences for a specific user from FTV.
    
    Args:
        user_id: The FTV user ID
        debug_performance: Whether to request performance data from FTV
    
    Returns:
        List of absence records, or dict with 'data' and 'performance' if debug enabled
    """
    try:
        params = {}
        if debug_performance:
            params['debug-performance'] = 'true'
            
        response = requests.get(
            f"{FTV_BASE_URL}/hianyzasok/user/{user_id}",
            headers=get_ftv_headers(),
            params=params,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching FTV absences for user {user_id}: {str(e)}")
        raise FTVSyncError(f"Failed to fetch FTV user absences: {str(e)}")


def fetch_ftv_class_absences(osztaly_id: int, debug_performance: bool = False) -> List[Dict]:
    """
    Fetch all absences for a specific class from FTV.
    
    Args:
        osztaly_id: The FTV osztaly ID
        debug_performance: Whether to request performance data from FTV
    
    Returns:
        List of absence records, or dict with 'data' and 'performance' if debug enabled
    """
    try:
        params = {}
        if debug_performance:
            params['debug-performance'] = 'true'
            
        response = requests.get(
            f"{FTV_BASE_URL}/hianyzasok/osztaly/{osztaly_id}",
            headers=get_ftv_headers(),
            params=params,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching FTV absences for class {osztaly_id}: {str(e)}")
        raise FTVSyncError(f"Failed to fetch FTV class absences: {str(e)}")


def fetch_ftv_profile_by_email(email: str) -> Optional[Dict]:
    """
    Fetch user profile from FTV by email address.
    
    Args:
        email: The user's email address
    
    Returns:
        User profile dict with user_id, or None if not found
    """
    try:
        response = requests.get(
            f"{FTV_BASE_URL}/profile/{email}",
            headers=get_ftv_headers(),
            timeout=30
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching FTV profile for {email}: {str(e)}")
        return None


def fetch_ftv_profile_by_email(email: str) -> Optional[Dict]:
    """Fetch user profile from FTV by email"""
    try:
        response = requests.get(
            f"{FTV_BASE_URL}/profile/{email}",
            headers=get_ftv_headers(),
            timeout=30
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching FTV profile for {email}: {str(e)}")
        return None


def sync_or_create_osztaly(ftv_osztaly: Dict) -> Osztaly:
    """
    Sync or create osztaly from FTV data.
    
    FTV uses startYear (e.g., 2023) and szekcio, we use kezdes_eve (e.g., 23) and tagozat.
    Convert 4-digit year to 2-digit format.
    """
    start_year = ftv_osztaly['startYear']
    # Convert 4-digit year to 2-digit (2023 -> 23, 2024 -> 24)
    kezdes_eve = start_year % 100
    tagozat = ftv_osztaly['szekcio']
    
    osztaly, created = Osztaly.objects.get_or_create(
        kezdes_eve=kezdes_eve,
        tagozat=tagozat
    )
    
    if created:
        logger.info(f"Created new osztaly: {osztaly}")
    
    return osztaly


def sync_or_create_user(ftv_absence: Dict, osztaly: Osztaly) -> Optional[User]:
    """
    Sync or create user from FTV absence data.
    
    Creates user, profile, and adds to class if not exists.
    """
    email = ftv_absence['diak_email']
    username = ftv_absence['diak_username']
    full_name = ftv_absence['diak_full_name']
    
    # Try to find user by email (most reliable)
    user = User.objects.filter(email=email).first()
    
    if not user:
        # Try by username
        user = User.objects.filter(username=username).first()
    
    if not user:
        # Create new user
        # Parse full name (assumes format: "Last First")
        name_parts = full_name.split(' ', 1)
        last_name = name_parts[0] if len(name_parts) > 0 else ''
        first_name = name_parts[1] if len(name_parts) > 1 else ''
        
        user = User.objects.create_user(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=f"{last_name.lower()}{first_name.lower()}123"  # Default password
        )
        logger.info(f"Created new user: {username} ({email})")
    
    # Ensure profile exists
    profile, created = Profile.objects.get_or_create(user=user)
    if created:
        logger.info(f"Created profile for user: {username}")
    
    # Add to class if not already there
    if not osztaly.tanulok.filter(id=user.id).exists():
        osztaly.tanulok.add(user)
        logger.info(f"Added {username} to class {osztaly}")
    
    return user


def parse_ftv_datetime(date_str: str, time_str: str) -> datetime:
    """Parse FTV date and time strings to datetime"""
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    time_obj = datetime.strptime(time_str, '%H:%M:%S').time()
    return datetime.combine(date_obj, time_obj)


def get_or_create_ftv_igazolas_tipus() -> IgazolasTipus:
    """Get or create the FTV igazolás tipus"""
    tipus, created = IgazolasTipus.objects.get_or_create(
        nev="Médiás Távollét",
        defaults={
            'leiras': 'Média tagozat FTV forgatáson való részvétel (automatikusan szinkronizálva)',
            'beleszamit': False,
            'iskolaerdeku': True
        }
    )
    if created:
        logger.info("Created 'Médiás Távollét' igazolás tipus")
    return tipus


def sync_ftv_absence(ftv_absence: Dict, user: User, ftv_tipus: IgazolasTipus) -> Igazolas:
    """
    Sync or update a single FTV absence record.
    
    Creates new Igazolas if not exists, updates if exists.
    """
    ftv_hianyzas_id = ftv_absence['id']
    profile = Profile.objects.get(user=user)
    
    # Parse times
    eleje = parse_ftv_datetime(ftv_absence['date'], ftv_absence['timeFrom'])
    vege = parse_ftv_datetime(ftv_absence['date'], ftv_absence['timeTo'])
    
    # Check if student edited the record - handle both boolean and string values
    student_edited = ftv_absence.get('student_edited', False)
    # Convert to boolean if it's a string or number
    if isinstance(student_edited, str):
        student_edited = student_edited.lower() in ('true', '1', 'yes')
    elif isinstance(student_edited, (int, float)):
        student_edited = bool(student_edited)
    
    diak_extra_ido_elotte = ftv_absence.get('student_extra_time_before')
    diak_extra_ido_utana = ftv_absence.get('student_extra_time_after')
    
    # Ensure these are integers or None
    if diak_extra_ido_elotte is not None:
        try:
            diak_extra_ido_elotte = int(diak_extra_ido_elotte)
        except (ValueError, TypeError):
            diak_extra_ido_elotte = None
    
    if diak_extra_ido_utana is not None:
        try:
            diak_extra_ido_utana = int(diak_extra_ido_utana)
        except (ValueError, TypeError):
            diak_extra_ido_utana = None
    
    # Ensure korrigalt is explicitly boolean
    korrigalt = bool(student_edited and (diak_extra_ido_elotte or diak_extra_ido_utana))
    
    # Prepare megjegyzes from FTV data
    forgatas_details = ftv_absence.get('forgatas_details', {})
    megjegyzes_parts = []
    
    # Format: <Forgatás type>: <Forgatás name>
    if forgatas_details.get('name'):
        # Determine forgatas type from FTV data
        forgatas_type_raw = forgatas_details.get('type', 'rendes')
        
        # Map FTV types to display names
        type_mapping = {
            'rendes': 'Forgatás',
            'kacsa': 'KaCsa',
            'egyeb': 'Egyéb Hivatalos Távollét',
            'esemeny': 'Esemény'
        }
        
        forgatas_type_display = type_mapping.get(forgatas_type_raw, 'Forgatás')
        megjegyzes_parts.append(f"{forgatas_type_display}: {forgatas_details['name']}")
    
    # if forgatas_details.get('description'):
        # megjegyzes_parts.append(f"Leírás: {forgatas_details['description']}")
    # if forgatas_details.get('location_name'):
    #     megjegyzes_parts.append(f"Helyszín: {forgatas_details['location_name']}")
    if ftv_absence.get('student_edit_note'):
        megjegyzes_parts.append(f"Diák megjegyzése: {ftv_absence['student_edit_note']}")
    
    megjegyzes = "\n".join(megjegyzes_parts) if megjegyzes_parts else None
    
    # Determine allapot from FTV data (for new records only)
    # Map FTV fields to our allapot - handle various data types
    ftv_excused = ftv_absence.get('excused', False)
    ftv_unexcused = ftv_absence.get('unexcused', False)
    
    # Convert to proper boolean - handle strings, numbers, and actual booleans
    def to_bool(value):
        """Convert various types to boolean safely"""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            # Empty string or "0" or "false" (case insensitive) = False
            if not value or value.strip() == '' or value.strip() == '0':
                return False
            return value.strip().lower() in ('true', '1', 'yes')
        if isinstance(value, (int, float)):
            return bool(value) and value != 0
        return False
    
    ftv_excused = to_bool(ftv_excused)
    ftv_unexcused = to_bool(ftv_unexcused)
    
    # Determine status based on FTV data (only used for NEW records)
    if ftv_excused:
        ftv_allapot = 'Elfogadva'
    elif ftv_unexcused:
        ftv_allapot = 'Elutasítva'
    else:
        ftv_allapot = 'Függőben'
    
    # Check if igazolas already exists
    try:
        igazolas = Igazolas.objects.get(ftv_hianyzas_id=ftv_hianyzas_id)
        
        # Update existing record - but DON'T change allapot if it already exists
        igazolas.eleje = eleje
        igazolas.vege = vege
        igazolas.korrigalt = bool(korrigalt)  # Ensure it's a boolean
        igazolas.diak_extra_ido_elotte = diak_extra_ido_elotte
        igazolas.diak_extra_ido_utana = diak_extra_ido_utana
        igazolas.megjegyzes_diak = megjegyzes
        # NOTE: allapot is NOT updated for existing records - teacher may have changed it locally
        igazolas.save()
        
        logger.info(f"Updated FTV igazolas #{igazolas.id} for {user.username} (status unchanged: {igazolas.allapot})")
        
    except Igazolas.DoesNotExist:
        # Create new record - set allapot from FTV data
        # Ensure all boolean fields are properly set
        try:
            igazolas = Igazolas.objects.create(
                profile=profile,
                eleje=eleje,
                vege=vege,
                tipus=ftv_tipus,
                megjegyzes_diak=megjegyzes,
                diak=False,  # Not created by student (explicitly False)
                ftv=True,  # From FTV (explicitly True)
                korrigalt=bool(korrigalt),  # Ensure it's a boolean
                diak_extra_ido_elotte=diak_extra_ido_elotte,
                diak_extra_ido_utana=diak_extra_ido_utana,
                ftv_hianyzas_id=ftv_hianyzas_id,
                allapot=ftv_allapot  # Sync status from FTV for new records only
            )
            
            logger.info(f"Created new FTV igazolas #{igazolas.id} for {user.username} with status: {ftv_allapot}")
        except Exception as create_error:
            logger.error(f"Failed to create igazolas for user {user.username}: {str(create_error)}")
            logger.error(f"Data being saved - eleje: {eleje}, vege: {vege}, korrigalt: {korrigalt} (type: {type(korrigalt)})")
            logger.error(f"Data being saved - diak_extra_ido_elotte: {diak_extra_ido_elotte} (type: {type(diak_extra_ido_elotte)})")
            logger.error(f"Data being saved - diak_extra_ido_utana: {diak_extra_ido_utana} (type: {type(diak_extra_ido_utana)})")
            raise
    
    return igazolas


def delete_obsolete_ftv_records(synced_ids: List[int], user: User = None):
    """
    Delete FTV igazolások that were not in the sync (beosztás changed).
    
    These represent students who were removed from the filming session.
    
    Args:
        synced_ids: List of FTV hiányzás IDs that were synced
        user: Optional user to limit deletion to specific user's records
    """
    if not synced_ids:
        # Delete all FTV records if none were synced
        obsolete = Igazolas.objects.filter(ftv=True)
        if user:
            obsolete = obsolete.filter(profile__user=user)
    else:
        obsolete = Igazolas.objects.filter(ftv=True).exclude(ftv_hianyzas_id__in=synced_ids)
        if user:
            obsolete = obsolete.filter(profile__user=user)
    
    count = obsolete.count()
    if count > 0:
        obsolete.delete()
        logger.info(f"Deleted {count} obsolete FTV igazolások (students removed from filming)")


def sync_user_absences_from_ftv(user: User, debug_performance: bool = False) -> Dict:
    """
    Sync absences for a specific user from FTV (optimized for /me endpoint).
    
    Args:
        user: The Django User instance to sync
        debug_performance: Whether to request and return performance data
    
    Returns:
        Dict with sync statistics and optional performance data
    """
    stats = {
        'igazolasok_created': 0,
        'igazolasok_updated': 0,
        'igazolasok_deleted': 0,
        'errors': 0
    }
    
    ftv_performance = None
    synced_ftv_ids = []
    
    try:
        # Get FTV igazolás tipus
        ftv_tipus = get_or_create_ftv_igazolas_tipus()
        
        # Get user's profile
        try:
            profile = Profile.objects.get(user=user)
        except Profile.DoesNotExist:
            logger.warning(f"No profile found for user {user.username}, creating one")
            profile = Profile.objects.create(user=user)
        
        # Check if user has email
        if not user.email:
            logger.error(f"User {user.username} has no email address - cannot sync with FTV")
            raise FTVSyncError(f"User {user.username} has no email address for FTV lookup")
        
        # Fetch user profile from FTV by email to get FTV user ID
        logger.info(f"Fetching FTV profile for user {user.username} ({user.email})...")
        try:
            ftv_profile = fetch_ftv_profile_by_email(user.email)
            if not ftv_profile:
                logger.warning(f"User {user.username} ({user.email}) not found in FTV system - no absences to sync")
                # No error, just return empty stats with flag (user might not be in FTV yet)
                result = {
                    'statistics': stats,
                    'ftv_registered': False,
                    'message': 'User not registered in FTV system'
                }
                update_cache_metadata(f'user_{user.id}', 'success', stats)
                return result
            
            ftv_user_id = ftv_profile['user_id']
            logger.info(f"Found FTV user ID {ftv_user_id} for {user.username}")
        except Exception as e:
            logger.error(f"Failed to fetch FTV profile for {user.email}: {str(e)}")
            raise FTVSyncError(f"Failed to fetch FTV profile: {str(e)}")
        
        # Fetch absences for this user
        logger.info(f"Fetching absences for user {user.username} (FTV ID: {ftv_user_id})...")
        response_data = fetch_ftv_user_absences(ftv_user_id, debug_performance)
        
        # Handle response format (could be list or dict with 'data' and 'performance')
        if isinstance(response_data, dict) and 'data' in response_data:
            ftv_absences = response_data['data']
            ftv_performance = response_data.get('performance')
        else:
            ftv_absences = response_data
        
        # Process each absence record
        for ftv_absence in ftv_absences:
            try:
                with transaction.atomic():
                    # Extract and sync class information from FTV absence data
                    if 'osztaly' in ftv_absence and ftv_absence['osztaly']:
                        # FTV sends class info in absence - sync it
                        ftv_osztaly_data = ftv_absence['osztaly']
                        osztaly = sync_or_create_osztaly(ftv_osztaly_data)
                        
                        # Ensure user is in the correct class
                        sync_or_create_user(ftv_absence, osztaly)
                    else:
                        # Fallback: use user's current class
                        osztaly = profile.osztalyom()
                        if not osztaly:
                            logger.warning(f"User {user.username} has no class and FTV absence {ftv_absence.get('id')} has no class data - skipping")
                            stats['errors'] += 1
                            continue
                    
                    # Sync absence record
                    igazolas = sync_ftv_absence(ftv_absence, user, ftv_tipus)
                    synced_ftv_ids.append(ftv_absence['id'])
                    
                    # Check if it was an update or create
                    if Igazolas.objects.filter(ftv_hianyzas_id=ftv_absence['id']).count() > 1:
                        stats['igazolasok_updated'] += 1
                    else:
                        stats['igazolasok_created'] += 1
                    
            except Exception as e:
                stats['errors'] += 1
                logger.error(f"Error syncing absence {ftv_absence.get('id')} for user {user.username}: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                continue
        
        # Delete obsolete records for this user
        try:
            with transaction.atomic():
                obsolete_count = Igazolas.objects.filter(
                    ftv=True, 
                    profile__user=user
                ).exclude(ftv_hianyzas_id__in=synced_ftv_ids).count()
                delete_obsolete_ftv_records(synced_ftv_ids, user=user)
                stats['igazolasok_deleted'] = obsolete_count
        except Exception as e:
            logger.error(f"Error cleaning up obsolete records for user {user.username}: {str(e)}")
        
        logger.info(f"User sync completed for {user.username}: {stats}")
        
        # Update cache metadata
        update_cache_metadata(f'user_{user.id}', 'success', stats)
        
        result = {
            'statistics': stats,
            'ftv_registered': True
        }
        if ftv_performance and debug_performance:
            result['ftv_performance'] = ftv_performance
        
        return result
        
    except FTVSyncError as e:
        logger.error(f"FTV user sync failed for {user.username}: {str(e)}")
        update_cache_metadata(f'user_{user.id}', 'failed', stats)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during FTV user sync for {user.username}: {str(e)}")
        update_cache_metadata(f'user_{user.id}', 'failed', stats)
        raise FTVSyncError(f"User sync failed: {str(e)}")


def sync_class_absences_from_ftv(osztaly: Osztaly, debug_performance: bool = False) -> Dict:
    """
    Sync absences for a specific class from FTV (optimized for /igazolas endpoint).
    
    Args:
        osztaly: The Osztaly instance to sync
        debug_performance: Whether to request and return performance data
    
    Returns:
        Dict with sync statistics and optional performance data
    """
    stats = {
        'users_synced': 0,
        'igazolasok_created': 0,
        'igazolasok_updated': 0,
        'igazolasok_deleted': 0,
        'errors': 0
    }
    
    ftv_performance = None
    synced_ftv_ids = []
    
    try:
        # Get FTV igazolás tipus
        ftv_tipus = get_or_create_ftv_igazolas_tipus()
        
        # Map local osztaly to FTV osztaly ID
        # FTV uses startYear (4-digit), we use kezdes_eve (2-digit)
        ftv_start_year = 2000 + osztaly.kezdes_eve  # Convert 23 -> 2023
        
        # TODO: Add proper FTV osztaly ID mapping if needed
        # For now, assume FTV osztaly ID can be found or we use a mapping
        # This is a placeholder - you may need to adjust based on your FTV data structure
        ftv_osztaly_id = osztaly.id
        
        # Fetch absences for this class
        logger.info(f"Fetching absences for class {osztaly}...")
        response_data = fetch_ftv_class_absences(ftv_osztaly_id, debug_performance)
        
        # Handle response format (could be list or dict with 'data' and 'performance')
        if isinstance(response_data, dict) and 'data' in response_data:
            ftv_absences = response_data['data']
            ftv_performance = response_data.get('performance')
        else:
            ftv_absences = response_data
        
        # Track which users were synced
        synced_users = set()
        
        for ftv_absence in ftv_absences:
            try:
                with transaction.atomic():
                    # Sync or create user
                    user = sync_or_create_user(ftv_absence, osztaly)
                    if not user:
                        continue
                    
                    synced_users.add(user.id)
                    
                    # Sync absence record
                    igazolas = sync_ftv_absence(ftv_absence, user, ftv_tipus)
                    synced_ftv_ids.append(ftv_absence['id'])
                    
                    # Check if it was an update or create
                    if Igazolas.objects.filter(ftv_hianyzas_id=ftv_absence['id']).count() > 1:
                        stats['igazolasok_updated'] += 1
                    else:
                        stats['igazolasok_created'] += 1
                    
            except Exception as e:
                stats['errors'] += 1
                logger.error(f"Error syncing absence {ftv_absence.get('id')}: {str(e)}")
                continue
        
        stats['users_synced'] = len(synced_users)
        
        # Delete obsolete records for this class
        # Only delete records for users in this class
        try:
            with transaction.atomic():
                class_users = osztaly.tanulok.all()
                obsolete_count = Igazolas.objects.filter(
                    ftv=True,
                    profile__user__in=class_users
                ).exclude(ftv_hianyzas_id__in=synced_ftv_ids).count()
                
                if obsolete_count > 0:
                    Igazolas.objects.filter(
                        ftv=True,
                        profile__user__in=class_users
                    ).exclude(ftv_hianyzas_id__in=synced_ftv_ids).delete()
                    logger.info(f"Deleted {obsolete_count} obsolete FTV igazolások for class {osztaly}")
                
                stats['igazolasok_deleted'] = obsolete_count
        except Exception as e:
            logger.error(f"Error cleaning up obsolete records for class {osztaly}: {str(e)}")
        
        logger.info(f"Class sync completed for {osztaly}: {stats}")
        
        # Update cache metadata
        update_cache_metadata(f'class_{osztaly.id}', 'success', stats)
        
        result = {'statistics': stats}
        if ftv_performance and debug_performance:
            result['ftv_performance'] = ftv_performance
        
        return result
        
    except FTVSyncError as e:
        logger.error(f"FTV class sync failed for {osztaly}: {str(e)}")
        update_cache_metadata(f'class_{osztaly.id}', 'failed', stats)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during FTV class sync for {osztaly}: {str(e)}")
        update_cache_metadata(f'class_{osztaly.id}', 'failed', stats)
        raise FTVSyncError(f"Class sync failed: {str(e)}")


def sync_base_from_ftv(debug_performance: bool = False) -> Dict:
    """
    Base sync function - syncs base data from FTV (classes + students).
    This is a lightweight sync that doesn't fetch absence data.
    
    Args:
        debug_performance: Whether to request and return performance data
    
    Returns:
        Dict with sync statistics and optional performance data
    """
    stats = {
        'classes_synced': 0,
        'students_synced': 0,
        'errors': 0
    }
    
    ftv_performance = None
    
    try:
        # Fetch base sync data
        logger.info("Fetching base sync data from FTV...")
        response_data = fetch_ftv_base_sync(debug_performance)
        
        # Handle response format
        if isinstance(response_data, dict):
            ftv_osztalyok = response_data.get('osztalyok', [])
            ftv_students = response_data.get('students', [])
            ftv_performance = response_data.get('performance')
        else:
            logger.error("Unexpected response format from FTV base sync")
            raise FTVSyncError("Invalid response format from FTV")
        
        # Sync classes
        for ftv_osztaly in ftv_osztalyok:
            try:
                osztaly = sync_or_create_osztaly(ftv_osztaly)
                stats['classes_synced'] += 1
            except Exception as e:
                stats['errors'] += 1
                logger.error(f"Error syncing osztaly {ftv_osztaly.get('id')}: {str(e)}")
        
        # Note: We don't sync students here as that would require absence data
        # This is intentionally lightweight
        stats['students_synced'] = len(ftv_students)
        
        logger.info(f"Base sync completed: {stats}")
        
        # Update cache metadata
        update_cache_metadata('base', 'success', stats)
        
        result = {'statistics': stats}
        if ftv_performance and debug_performance:
            result['ftv_performance'] = ftv_performance
        
        return result
        
    except FTVSyncError as e:
        logger.error(f"FTV base sync failed: {str(e)}")
        update_cache_metadata('base', 'failed', stats)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during FTV base sync: {str(e)}")
        update_cache_metadata('base', 'failed', stats)
        raise FTVSyncError(f"Base sync failed: {str(e)}")
