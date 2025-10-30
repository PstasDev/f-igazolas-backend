
import csv
from io import StringIO
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from api.models import Profile, Osztaly

SZAMTECH_TSV = '''veznev	kernev	harnev	email
Ács	Péter	Levente	acs.peter.21f@szlgbp.hu
Balogh	Márton		balogh.marton.21f@szlgbp.hu
Ekker	Máté		ekker.mate.21f@szlgbp.hu
Farkas	Péter		farkas.peter.21f@szlgbp.hu
Fodor	Péter		fodor.peter.21f@szlgbp.hu
Gaál	Csaba		gaal.csaba.21f@szlgbp.hu
Jakab	Zalán		jakab.zalan.21f@szlgbp.hu
Kállai	László	Mátyás	kallai.laszlo.21f@szlgbp.hu
Kovács	Dóra		kovacs.dora.21f@szlgbp.hu
Lelkes	Tünde		lelkes.tunde.21f@szlgbp.hu
Mamira	Máté	Márk	mamira.mate.21f@szlgbp.hu
Nagy	Gergely		nagy.gergely.21f@szlgbp.hu
Novák	Simon		novak.simon.21f@szlgbp.hu
Sárközi	Róbert	Fülöp	sarkozi.robert.21f@szlgbp.hu
Stefán	Boldizsár		stefan.boldizsar.21f@szlgbp.hu
Szabó	Kinga		szabo.kinga.21f@szlgbp.hu
Szatmári	Gréta		szatmari.greta.21f@szlgbp.hu
Széplaki	Zsófia		szeplaki.zsofia.21f@szlgbp.hu
Török	Emese		torok.emese.21f@szlgbp.hu
Viniczei	Viktor		viniczei.viktor.21f@szlgbp.hu
Balázs	Ádám	Gábor	balazs.adam.22f@szlgbp.hu
Bánszki	Zsombor		banszki.zsombor.22f@szlgbp.hu
Császár	Gábor		csaszar.gabor.22f@szlgbp.hu
Csenki	Ákos		csenki.akos.22f@szlgbp.hu
Eigner	Krisztián	János	eigner.krisztian.22f@szlgbp.hu
Gere	Lukács		gere.lukacs.22f@szlgbp.hu
Menyhárt	Zsombor		menyhart.zsombor.22f@szlgbp.hu
Paszlavszki	László	István	paszlavszki.laszlo.22f@szlgbp.hu
Pereszlényi	Sebestyén		pereszlenyi.sebestyen.22f@szlgbp.hu
Previák	Richárd	Áron	previak.richard.22f@szlgbp.hu
Sebestyén	Levente		sebestyen.levente.22f@szlgbp.hu
Söphen	Zsombor	Alex	sophen.zsombor.22f@szlgbp.hu
Székely-Sipos	Csanád		szekely-sipos.csanad.22f@szlgbp.hu
Szőke	Mátyás		szoke.matyas.22f@szlgbp.hu
Tóth	Levente		toth.levente.22f@szlgbp.hu
Vajda	Zsombor	Sándor	vajda.zsombor.22f@szlgbp.hu
Varga	Zénó	Zoltán	varga.zeno.22f@szlgbp.hu
Vincze	Dániel		vincze.daniel.22f@szlgbp.hu
Visnyei	László		visnyei.laszlo.22f@szlgbp.hu
Magyar	Kende	Mihály	magyar.kende.22f@szlgbp.hu
Antal	Cintia		antal.cintia.23f@szlgbp.hu
Balla	Botond		balla.botond.23f@szlgbp.hu
Balla	Letícia		balla.leticia.23f@szlgbp.hu
Balogh	Mátyás		balogh.matyas.23f@szlgbp.hu
Bereczki	Lehel		bereczki.lehel.23f@szlgbp.hu
Bildhauer	Barna	Viktor	bildhauer.barna.23f@szlgbp.hu
Bogdán-Bordi	Szilárd		bogdan-bordi.szilard.23f@szlgbp.hu
Ertinger-Szukk	Péter		ertinger-szukk.peter.23f@szlgbp.hu
Káli	Hunor		kali.hunor.23f@szlgbp.hu
Kecskeméti	Mátyás		kecskemeti.matyas.23f@szlgbp.hu
Kocsis	Ferenc	Bálint	kocsis.ferenc.23f@szlgbp.hu
Mátyás	Ákos		matyas.akos.23f@szlgbp.hu
Mechler	Dénes		mechler.denes.23f@szlgbp.hu
Molnár	Zsófia	Bianka	molnar.zsofia.23f@szlgbp.hu
Orosz	Dorka		orosz.dorka.23f@szlgbp.hu
Pálvölgyi	Viola		palvolgyi.viola.23f@szlgbp.hu
Sipos	Ádám		sipos.adam.23f@szlgbp.hu
Szabó	Réka	Hanna	szabo.reka.23f@szlgbp.hu
Tóth	Liliána		toth.liliana.23f@szlgbp.hu
Alich	Vilmos	Gergely	alich.vilmos.24f@szlgbp.hu
Bocsi	Mátyás		bocsi.matyas.24f@szlgbp.hu
Bozsó-Andrássy	Áron		bozso-andrassy.aron.24f@szlgbp.hu
Cséfai	Vilmos		csefai.vilmos.24f@szlgbp.hu
Geréb	Lili		gereb.lili.24f@szlgbp.hu
Görömbei	Ervin		gorombei.ervin.24f@szlgbp.hu
Hauber	Levente	György	hauber.levente.24f@szlgbp.hu
Kordás	Dávid		kordas.david.24f@szlgbp.hu
Kosztolni	Réka		kosztolni.reka.24f@szlgbp.hu
Kovács	Ádám	Lőrinc	kovacs.adam.24f@szlgbp.hu
Körmendi	Patrik		kormendi.patrik.24f@szlgbp.hu
Lippényi	Anna		lippenyi.anna.24f@szlgbp.hu
Pavlicsek	Huba		pavlicsek.huba.24f@szlgbp.hu
Péterfi	Dénes		peterfi.denes.24f@szlgbp.hu
Slonszki	Linett		slonszki.linett.24f@szlgbp.hu
Stépán	Sámuel	Zsolt	stepan.samuel.24f@szlgbp.hu
Tóth	Soma	Boldizsár	toth.soma.24f@szlgbp.hu
Zenzerov	Maxim		zenzerov.maxim.24f@szlgbp.hu
Bagoly-Kis	Ákos		bagoly-kis.akos.25f@szlgbp.hu
Bánóczi	Áron		banoczi.aron.25f@szlgbp.hu
Boros	Levente		boros.levente.25f@szlgbp.hu
Deák-Kántor	Dániel	István	deak-kantor.daniel.25f@szlgbp.hu
Farnady	Botond		farnady.botond.25f@szlgbp.hu
Gál	Bercel		gal.bercel.25f@szlgbp.hu
Kaldau	Sebestyén		kaldau.sebestyen.25f@szlgbp.hu
Kiss	Benedek	Sándor	kiss.benedek.25f@szlgbp.hu
Lengyel	Ákos		lengyel.akos.25f@szlgbp.hu
Mikuska	Botond	Márk	mikuska.botond.25f@szlgbp.hu
Pusztai	Zsombor		pusztai.zsombor.25f@szlgbp.hu
Simák	Boldizsár		simak.boldizsar.25f@szlgbp.hu
Szabari	Menyhért		szabari.menyhert.25f@szlgbp.hu
Szilágyi	Attila		szilagyi.attila.25f@szlgbp.hu
Tóth	László	Barnabás	toth.laszlo.25f@szlgbp.hu
Vincze	Zoltán		vincze.zoltan.25f@szlgbp.hu
'''

class Command(BaseCommand):
    help = 'Import users from szamtechesek TSV and create profiles linked to their class.'

    def handle(self, *args, **options):
        User = get_user_model()
        reader = csv.DictReader(StringIO(SZAMTECH_TSV), delimiter='\t')
        for row in reader:
            veznev = row['veznev'].strip()
            kernev = row['kernev'].strip()
            harnev = row.get('harnev', '').strip()
            email = row['email'].strip()
            if not email:
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
