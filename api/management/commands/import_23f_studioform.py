from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from api.models import Profile, Igazolas, IgazolasTipus
from datetime import datetime, time

# Lesson schedule with start and end times
ORAK = [
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

# Raw data from the form
DATA = """szabo.reka.23f@szlgbp.hu	Szabó Réka Hanna 	Toborzás	2025.09.12.	3. óra	3. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Stúdiós toborzás 	2025.09.11.	1. óra	1. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Stúdió toborzás 	2025.09.12.	3. óra	3. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Mamma Mia Musical alapok	2025.09.15.	1. óra	1. óra
balla.botond.23f@szlgbp.hu	Balla Botond 	NYF (Kalóczkai) - Stúdiós toborzás 	2025.09.15.	2. óra	2. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Kőrösi színházat próbáltuk elérni, lenyúlták a végfokunkat	2025.09.15.	4. óra	4. óra
szabo.reka.23f@szlgbp.hu	Szabó Réka Hanna 	Toborzas, musical	2025.09.15.	1. óra	2. óra
balla.botond.23f@szlgbp.hu	Balla Botond 	Stúdiós Toborzás - NYE	2025.09.18.	6. óra	6. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Elég kiállítás 	2025.09.22.	0. óra	6. óra
balla.botond.23f@szlgbp.hu	Balla Botond 	Elég kiállítás 	2025.09.23.	0. óra	6. óra
balla.botond.23f@szlgbp.hu	Balla Botond	DMX Interfész teszt	2025.09.25.	0. óra	2. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Olasz színjátszókör próba 	2025.09.30.	3. óra	7. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Olasz színjátszókör próba - kipakolás, hangosítás, rendrakás	2025.10.01.	1. óra	7. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Olasz színjátszókör előadása 	2025.10.02.	0. óra	6. óra
szabo.reka.23f@szlgbp.hu	Szabó Réka Hanna 	Toborzás	2025.09.18.	0. óra	1. óra
szabo.reka.23f@szlgbp.hu	Szabó Réka Hanna 	Toborzas	2025.09.15.	1. óra	2. óra
balla.botond.23f@szlgbp.hu	Balla Botond 	Freestyler DMX Kontroller felprogramozás	2025.10.03.	3. óra	3. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Olasz Cserediákprogram Hangosítás - Díszterem	2025.09.29.	5. óra	5. óra
balla.botond.23f@szlgbp.hu	Balla Botond	MagiQ DMX Kontroller felprogramozás	2025.10.06.	4. óra	5. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Kölcsönzött felszerelés visszavitele a színházba	2025.10.06.	7. óra	7. óra
balla.botond.23f@szlgbp.hu	Balla Botond	Stúdiós megbeszélés a Tanárnővel	2025.10.09.	5. óra	5. óra
szabo.reka.23f@szlgbp.hu	Szabó Réka Hanna 	Studios tendszerkarbantartas	2025.10.22.	2. óra	3. óra
szabo.reka.23f@szlgbp.hu	Szabó Réka Hanna 	Nyíltnap	2025.11.04.	4. óra	4. óra"""


class Command(BaseCommand):
    help = 'Import 23F studio form igazolás records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Perform a dry run without saving to database',
        )

    def parse_ora_string(self, ora_str):
        """Parse hour string like '3. óra' or '0. óra' to integer"""
        try:
            return int(ora_str.split('.')[0].strip())
        except (ValueError, IndexError):
            raise ValueError(f"Cannot parse hour string: {ora_str}")

    def create_datetime_for_ora(self, date_str, ora_num, is_start=True):
        """
        Create datetime from date string and hour number using actual lesson times.
        
        Args:
            date_str: Date string like "2025.09.12."
            ora_num: Lesson number (0-8)
            is_start: True for lesson start time, False for lesson end time
        """
        # Parse date string like "2025.09.12."
        date_parts = date_str.strip().rstrip('.').split('.')
        year = int(date_parts[0])
        month = int(date_parts[1])
        day = int(date_parts[2])
        
        # Get the lesson time
        if ora_num < 0 or ora_num >= len(ORAK):
            raise ValueError(f"Invalid lesson number: {ora_num}")
        
        kezdes_str, veg_str = ORAK[ora_num]
        time_str = kezdes_str if is_start else veg_str
        
        # Parse time string
        time_obj = datetime.strptime(time_str, "%H:%M").time()
        
        return datetime(year, month, day, time_obj.hour, time_obj.minute, 0)

    def handle(self, *args, **options):
        User = get_user_model()
        dry_run = options.get('dry_run', False)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved'))
        
        # Get or create the "Stúdió" igazolás típus
        studio_tipus = None
        try:
            studio_tipus = IgazolasTipus.objects.get(nev='Stúdiós Távollét')
            self.stdout.write(self.style.SUCCESS(f'Found igazolás típus: Stúdiós Távollét (id={studio_tipus.id})'))
        except IgazolasTipus.DoesNotExist:
            if dry_run:
                self.stdout.write(self.style.WARNING('Would create igazolás típus: Stúdiós Távollét (dry run)'))
                self.stdout.write(self.style.WARNING('Note: Duplicate checking is disabled in dry-run mode when tipus doesn\'t exist'))
            else:
                studio_tipus = IgazolasTipus.objects.create(
                    nev='Stúdiós Távollét',
                    beleszamit=False,  # Studio work doesn't count towards absences
                    iskolaerdeku=True,  # It's school-related activity
                    leiras='Igazolás stúdiós tevékenység miatt',
                )
                self.stdout.write(self.style.SUCCESS(f'Created igazolás típus: Stúdiós Távollét (id={studio_tipus.id})'))
        
        # Parse and process data
        created_count = 0
        skipped_count = 0
        error_count = 0
        
        for line_num, line in enumerate(DATA.strip().split('\n'), start=1):
            if not line.strip():
                continue
            
            try:
                # Split by tab character
                parts = line.split('\t')
                if len(parts) != 6:
                    self.stdout.write(self.style.ERROR(
                        f'Line {line_num}: Expected 6 fields, got {len(parts)}: {line}'
                    ))
                    error_count += 1
                    continue
                
                email = parts[0].strip()
                nev = parts[1].strip()
                megjegyzes = parts[2].strip()
                datum = parts[3].strip()
                ora_tol = parts[4].strip()
                ora_ig = parts[5].strip()
                
                # Get user by email
                try:
                    user = User.objects.get(email=email)
                except User.DoesNotExist:
                    self.stdout.write(self.style.ERROR(
                        f'Line {line_num}: User not found with email: {email}'
                    ))
                    error_count += 1
                    continue
                
                # Get or create profile
                profile, _ = Profile.objects.get_or_create(user=user)
                
                # Parse hours
                ora_tol_num = self.parse_ora_string(ora_tol)
                ora_ig_num = self.parse_ora_string(ora_ig)
                
                # Create datetime objects using actual lesson times
                eleje = self.create_datetime_for_ora(datum, ora_tol_num, is_start=True)
                vege = self.create_datetime_for_ora(datum, ora_ig_num, is_start=False)
                
                # Check if similar igazolás already exists (only if tipus exists in DB)
                if studio_tipus is not None:
                    existing = Igazolas.objects.filter(
                        profile=profile,
                        eleje=eleje,
                        vege=vege,
                        tipus=studio_tipus
                    ).first()
                    
                    if existing:
                        self.stdout.write(self.style.WARNING(
                            f'Line {line_num}: Skipped duplicate for {user.username} on {datum} ({ora_tol} - {ora_ig})'
                        ))
                        skipped_count += 1
                        continue
                
                # Create igazolás
                if dry_run:
                    self.stdout.write(self.style.SUCCESS(
                        f'Line {line_num}: Would create igazolás for {user.username} ({nev}) on {datum} '
                        f'from {ora_tol} to {ora_ig}: {megjegyzes}'
                    ))
                    created_count += 1
                else:
                    igazolas = Igazolas.objects.create(
                        profile=profile,
                        eleje=eleje,
                        vege=vege,
                        tipus=studio_tipus,
                        megjegyzes_diak=megjegyzes,
                        diak=True,  # Created by/for student
                        ftv=False,  # Not from FTV
                        korrigalt=False,
                        allapot='Elfogadva'  # Pre-approved studio work
                    )
                    self.stdout.write(self.style.SUCCESS(
                        f'Line {line_num}: Created igazolás #{igazolas.id} for {user.username} ({nev}) '
                        f'on {datum} from {ora_tol} to {ora_ig}: {megjegyzes}'
                    ))
                    created_count += 1
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'Line {line_num}: Error processing line: {str(e)}'
                ))
                self.stdout.write(self.style.ERROR(f'Line content: {line}'))
                error_count += 1
                continue
        
        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('IMPORT SUMMARY'))
        self.stdout.write(self.style.SUCCESS('='*60))
        if dry_run:
            self.stdout.write(self.style.WARNING(f'Mode: DRY RUN (no changes saved)'))
        self.stdout.write(self.style.SUCCESS(f'Created: {created_count}'))
        self.stdout.write(self.style.WARNING(f'Skipped (duplicates): {skipped_count}'))
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f'Errors: {error_count}'))
        self.stdout.write(self.style.SUCCESS('='*60))
