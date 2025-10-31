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


def get_cache_metadata() -> Dict:
    """
    Get FTV sync cache metadata for responses.
    
    Returns dict with last_sync_time, status, and age information.
    """
    metadata = FTVSyncMetadata.get_instance()
    
    result = {
        'last_sync_time': metadata.last_sync_time.isoformat() if metadata.last_sync_time else None,
        'last_sync_status': metadata.last_sync_status,
        'last_sync_stats': metadata.last_sync_stats
    }
    
    # Calculate time since last sync
    if metadata.last_sync_time:
        from datetime import datetime
        from django.utils import timezone as django_timezone
        age_seconds = (django_timezone.now() - metadata.last_sync_time).total_seconds()
        result['sync_age_seconds'] = int(age_seconds)
        result['sync_age_minutes'] = round(age_seconds / 60, 1)
    else:
        result['sync_age_seconds'] = None
        result['sync_age_minutes'] = None
    
    return result


def get_ftv_headers() -> Dict[str, str]:
    """Get headers for FTV API requests"""
    token = settings.FTV_EXTERNAL_ACCESS_TOKEN
    if not token:
        raise FTVSyncError("FTV_EXTERNAL_ACCESS_TOKEN not configured")
    
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }


def fetch_ftv_classes() -> List[Dict]:
    """Fetch all classes from FTV"""
    try:
        response = requests.get(
            f"{FTV_BASE_URL}/osztalyok",
            headers=get_ftv_headers(),
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching FTV classes: {str(e)}")
        raise FTVSyncError(f"Failed to fetch FTV classes: {str(e)}")


def fetch_ftv_absences_by_class(osztaly_id: int) -> List[Dict]:
    """Fetch all absences for a specific class from FTV"""
    try:
        response = requests.get(
            f"{FTV_BASE_URL}/hianyzasok/osztaly/{osztaly_id}",
            headers=get_ftv_headers(),
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching FTV absences for class {osztaly_id}: {str(e)}")
        raise FTVSyncError(f"Failed to fetch FTV absences: {str(e)}")


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


def delete_obsolete_ftv_records(synced_ids: List[int]):
    """
    Delete FTV igazolások that were not in the sync (beosztás changed).
    
    These represent students who were removed from the filming session.
    """
    if not synced_ids:
        # Delete all FTV records if none were synced
        obsolete = Igazolas.objects.filter(ftv=True)
    else:
        obsolete = Igazolas.objects.filter(ftv=True).exclude(ftv_hianyzas_id__in=synced_ids)
    
    count = obsolete.count()
    if count > 0:
        obsolete.delete()
        logger.info(f"Deleted {count} obsolete FTV igazolások (students removed from filming)")


def sync_with_ftv() -> Dict[str, int]:
    """
    Main sync function - syncs all data from FTV.
    
    Returns dictionary with sync statistics.
    Note: NOT using @transaction.atomic to allow partial success - each record syncs independently.
    """
    stats = {
        'classes_synced': 0,
        'users_created': 0,
        'users_updated': 0,
        'igazolasok_created': 0,
        'igazolasok_updated': 0,
        'igazolasok_deleted': 0,
        'errors': 0
    }
    
    synced_ftv_ids = []
    
    try:
        # Get FTV igazolás tipus
        ftv_tipus = get_or_create_ftv_igazolas_tipus()
        
        # Fetch all classes from FTV
        logger.info("Fetching classes from FTV...")
        ftv_classes = fetch_ftv_classes()
        
        for ftv_osztaly in ftv_classes:
            # Skip non-media classes if needed (optional filter)
            # For now, sync all classes
            
            try:
                # Sync or create osztaly
                osztaly = sync_or_create_osztaly(ftv_osztaly)
                stats['classes_synced'] += 1
                
                # Fetch absences for this class
                logger.info(f"Fetching absences for class {osztaly}...")
                try:
                    ftv_absences = fetch_ftv_absences_by_class(ftv_osztaly['id'])
                except FTVSyncError:
                    logger.warning(f"Failed to fetch absences for class {osztaly}, skipping...")
                    continue
                
                for ftv_absence in ftv_absences:
                    try:
                        # Use transaction for each individual record
                        with transaction.atomic():
                            # Sync or create user
                            user = sync_or_create_user(ftv_absence, osztaly)
                            if not user:
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
                        logger.error(f"Error syncing absence {ftv_absence.get('id')}: {str(e)}")
                        logger.error(f"Full error details: {repr(e)}")
                        logger.debug(f"Problematic absence data: {ftv_absence}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        continue
                        
            except Exception as e:
                logger.error(f"Error syncing class {ftv_osztaly.get('id')}: {str(e)}")
                continue
        
        # Delete obsolete records (use transaction for cleanup)
        try:
            with transaction.atomic():
                logger.info("Cleaning up obsolete FTV records...")
                obsolete_count = Igazolas.objects.filter(ftv=True).exclude(
                    ftv_hianyzas_id__in=synced_ftv_ids
                ).count()
                delete_obsolete_ftv_records(synced_ftv_ids)
                stats['igazolasok_deleted'] = obsolete_count
        except Exception as e:
            logger.error(f"Error cleaning up obsolete records: {str(e)}")
        
        logger.info(f"FTV sync completed: {stats}")
        
        # Update sync metadata
        FTVSyncMetadata.update_sync('success', stats)
        
        return stats
        
    except FTVSyncError as e:
        logger.error(f"FTV sync failed: {str(e)}")
        # Update metadata with failed status
        FTVSyncMetadata.update_sync('failed', stats)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during FTV sync: {str(e)}")
        # Update metadata with failed status
        FTVSyncMetadata.update_sync('failed', stats)
        raise FTVSyncError(f"Sync failed: {str(e)}")
