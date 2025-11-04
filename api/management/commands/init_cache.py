"""
Management command to initialize cache metadata from existing data
"""
from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.utils import timezone
from api.models import Igazolas


class Command(BaseCommand):
    help = 'Initialize cache metadata from existing FTV data'

    def handle(self, *args, **options):
        # Count existing FTV igazolások
        ftv_count = Igazolas.objects.filter(ftv=True).count()
        
        if ftv_count > 0:
            # Set cache metadata for base sync with current time
            last_sync_time = timezone.now()
            
            metadata = {
                'last_sync_time': last_sync_time.isoformat(),
                'last_sync_status': 'success',
                'last_sync_stats': {
                    'note': f'Initialized from existing data ({ftv_count} FTV records found)',
                    'igazolasok_count': ftv_count
                }
            }
            
            cache.set('ftv_sync_metadata_base', metadata, timeout=None)  # No expiration
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully initialized cache metadata with {ftv_count} FTV records found.\n'
                    f'Last sync time set to: {last_sync_time}'
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    'No FTV igazolások found in database. Please run a manual sync via POST /sync/ftv'
                )
            )
