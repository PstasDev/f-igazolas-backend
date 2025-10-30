import re
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from api.models import Profile, Osztaly

STUDENT_DATA = '''
Bartha    Dóra Zsuzsanna        bartha.dora.22f@szlgbp.hu
Botos    Csenge        botos.csenge.22f@szlgbp.hu
Faludy    Flóra Panna        faludy.flora.22f@szlgbp.hu
Hancz    Johanna        hancz.johanna.22f@szlgbp.hu
Ivák-Tokody    Dávid        ivak-tokody.david.22f@szlgbp.hu
Kapta    Zétény        kapta.zeteny.22f@szlgbp.hu
Konok    Flóra Sára        konok.flora.22f@szlgbp.hu
Marton    Ádám        marton.adam.22f@szlgbp.hu
Németh    Lola Dalma        nemeth.lola.22f@szlgbp.hu
Pacsay    Levente        pacsay.levente.22f@szlgbp.hu
Pásztor    Lilien        pasztor.lilien.22f@szlgbp.hu
Semperger    Nóra        semperger.nora.22f@szlgbp.hu
Szántai    Csanád        szantai.csanad.22f@szlgbp.hu
Tatai    Ádám        tatai.adam.22f@szlgbp.hu
Töreky    Gergő Gábor        toreky.gergo.22f@szlgbp.hu
Tőrincsi    Lilla        torincsi.lilla.22f@szlgbp.hu
Vartik    Petra Lilla        vartik.petra.22f@szlgbp.hu
'''

class Command(BaseCommand):
    help = 'Import 22F class students and create profiles linked to their class.'

    def handle(self, *args, **options):
        User = get_user_model()
        
        # Get or create Osztaly for 22F
        osztaly_obj, created = Osztaly.objects.get_or_create(kezdes_eve=22, tagozat='F')
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created class: 22F'))
        
        for line in STUDENT_DATA.strip().split('\n'):
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
            
            # Extract username from email
            username = email.split('@')[0]
            
            # Get or create user based on email
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'username': username,
                    'first_name': kernev,
                    'last_name': veznev,
                }
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created user: {veznev} {kernev} ({email})'))
            else:
                self.stdout.write(self.style.SUCCESS(f'Found existing user: {veznev} {kernev} ({email})'))
            
            # Get or create profile
            profile, _ = Profile.objects.get_or_create(user=user)
            
            # Add user to Osztaly tanulok
            osztaly_obj.tanulok.add(user)
        
        self.stdout.write(self.style.SUCCESS(f'Import complete for 22F class'))
