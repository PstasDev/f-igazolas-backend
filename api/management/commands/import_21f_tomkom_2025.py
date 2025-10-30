
import re
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from api.models import Profile, Osztaly

SZAMTECH_DATA = '''
Bogár    Panna        bogar.panna.21f@szlgbp.hu
Dankó    Csenge        danko.csenge.21f@szlgbp.hu
Dittrich    Hanna        dittrich.hanna.21f@szlgbp.hu
Gál    Nikolasz        gal.nikolasz.21f@szlgbp.hu
Hacsek    Anikó        hacsek.aniko.21f@szlgbp.hu
Hlatki    Boglárka        hlatki.boglarka.21f@szlgbp.hu
Kardos    Áron        kardos.aron.21f@szlgbp.hu
Katona    Bianka        katona.bianka.21f@szlgbp.hu
Koncz    Máté        koncz.mate.21f@szlgbp.hu
Konnerth    Anna        konnerth.anna.21f@szlgbp.hu
Lakos    Dorka        lakos.dorka.21f@szlgbp.hu
Lénárt    Olívia        lenart.olivia.21f@szlgbp.hu
Pálinkás    Petra        palinkas.petra.21f@szlgbp.hu
Rusznyák    Orsolya        rusznyak.orsolya.21f@szlgbp.hu
Szőke    Nikolett        szoke.nikolett.21f@szlgbp.hu
Török    Barnabás        torok.barnabas.21f@szlgbp.hu
Ugrai    Kata        ugrai.kata.21f@szlgbp.hu
'''

class Command(BaseCommand):
    help = 'Import users from szamtechesek data and create profiles linked to their class.'

    def handle(self, *args, **options):
        User = get_user_model()
        
        for line in SZAMTECH_DATA.strip().split('\n'):
            if not line.strip():
                continue
            
            # Split by multiple spaces to get fields
            parts = re.split(r'\s{2,}', line.strip())
            if len(parts) < 3:
                self.stdout.write(self.style.WARNING(f'Skipped malformed line: {line}'))
                continue
            
            veznev = parts[0].strip()
            kernev = parts[1].strip()
            email = parts[2].strip()
            
            if not email or not veznev or not kernev:
                self.stdout.write(self.style.WARNING(f'Skipped line due to missing data: {line}'))
                continue
            # Extract class from email (e.g., 21f)
            try:
                osztalynev = email.split('@')[0].split('.')[-1]  # e.g., 21f
                kezdes_eve = int(osztalynev[:2])
                tagozat = osztalynev[2].upper()
            except Exception:
                self.stdout.write(self.style.ERROR(f'Could not parse class from email: {email}'))
                continue
            # Get or create Osztaly
            osztaly_obj, _ = Osztaly.objects.get_or_create(kezdes_eve=kezdes_eve, tagozat=tagozat)
            # Get or create user
            username = email.split('@')[0]
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': veznev,
                    'last_name': kernev,
                    'email': email,
                }
            )
            # Get or create profile
            profile, _ = Profile.objects.get_or_create(user=user)
            # Add user to Osztaly tanulok
            osztaly_obj.tanulok.add(user)
            self.stdout.write(self.style.SUCCESS(f'Imported: {veznev} {kernev} ({email})'))
