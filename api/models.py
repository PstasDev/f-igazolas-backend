from django.db import models
from django.contrib.auth.models import User, Group

# User modellek

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    def osztalyom(self):
        return Osztaly.objects.filter(tanulok=self.user).first() or Osztaly.objects.filter(osztalyfonokok=self.user).first()
    
    def osztalyom_igazolasai(self):
        osztaly = Osztaly.objects.filter(osztalyfonokok=self.user).first()
        if osztaly:
            return osztaly.osztaly_igazolasai()
        return Igazolas.objects.none()

    def __str__(self):
        return self.user.username
    
    class Meta:
        verbose_name = 'Profil'
        verbose_name_plural = 'Profilok'

class Osztaly(models.Model):
    tagozat = models.CharField(max_length=1) # PL. A, B, C
    kezdes_eve = models.IntegerField() # PL. 22, 23, 24

    tanulok = models.ManyToManyField(User, blank=True)
    osztalyfonokok = models.ManyToManyField(User, related_name='osztalyfonokok', blank=True)

    def osztaly_igazolasai(self):
        return Igazolas.objects.filter(profile__user__in=self.tanulok.all())

    def __str__(self):
        return f"{self.kezdes_eve}{self.tagozat}" # 23A, 22B
    
    class Meta:
        verbose_name = 'Osztály'
        verbose_name_plural = 'Osztályok'

# Hiányzás modellek

# Mulasztás - Krétából importált hiányzás
class Mulasztas(models.Model):
    datum = models.DateField()
    ora = models.IntegerField() # 0-8
    tantargy = models.CharField(max_length=100)
    tema = models.CharField(max_length=200)
    
    tipusok = [
        ('KE', 'Késés'),
        ('HI', 'Hiányzás'),
    ]

    tipus = models.CharField(max_length=50) # Hiányzás, késés
    igazolt = models.BooleanField(default=False)
    igazolas_tipusa = models.CharField(max_length=100, null=True, blank=True) # Pl. orvosi igazolás
    rogzites_datuma = models.DateField() # Mikor lett a KRÉTÁBA rögzítve a mulasztás?

    class Meta:
        verbose_name = 'Mulasztás'
        verbose_name_plural = 'Mulasztások'

class IgazolasTipus(models.Model):
    nev = models.CharField(max_length=100) # Pl. orvosi igazolás
    # beleszámít-e a bizonyítványba?
    leiras = models.TextField(null=True, blank=True, max_length=500)
    beleszamit = models.BooleanField(default=True)

    # Iskolaérdekű-e? - Ha a tanár el akarná utasítani az igazolást, de iskolaérdekű, akkor popup, hogy biztosan elutasítja-e
    iskolaerdeku = models.BooleanField(default=False)

    def __str__(self):
        return self.nev
    
    class Meta:
        verbose_name = 'Igazolás Típus'
        verbose_name_plural = 'Igazolás Típusok'

# Igazolás - Új Igazolás form response
class Igazolas(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    mulasztasok = models.ManyToManyField(Mulasztas, blank=True)

    eleje = models.DateTimeField()
    vege = models.DateTimeField()

    tipus = models.ForeignKey(IgazolasTipus, on_delete=models.CASCADE)

    rogzites_datuma = models.DateField(auto_now_add=True)

    megjegyzes_diak = models.TextField(max_length=500, null=True, blank=True)

    # Forrás
    diak = models.BooleanField(default=True)  # True ha Form repsonse
    ftv = models.BooleanField(default=False)  # True ha FTV-ből lett importálva
    korrigalt = models.BooleanField(default=False)  # FTVből importált igazolás, diák által korrigált változata

    # Korrekció
    diak_extra_ido_elotte = models.IntegerField(null=True, blank=True)  
    diak_extra_ido_utana = models.IntegerField(null=True, blank=True)


    # URL amire feltölti a diák a fényképet, nem Image repsonse -> Google Drive
    imgDriveURL = models.URLField(max_length=300, null=True, blank=True)

    # Tanár tölti ki

    allapotok = [
        ('Függőben', 'Függőben'),
        ('Elfogadva', 'Elfogadva'),
        ('Elutasítva', 'Elutasítva'),
    ]

    allapot = models.CharField(max_length=50, default='Függőben', choices=allapotok)

    megjegyzes_tanar = models.TextField(max_length=500, null=True, blank=True) # diák nem láthatja

    kretaban_rogzitettem = models.BooleanField(default=False)

    def __str__(self):
        return f"Igazolás #{self.id} - {self.profile.user.username}"
    
    class Meta:
        verbose_name = 'Igazolás'
        verbose_name_plural = 'Igazolások'