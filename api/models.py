from django.db import models
from django.contrib.auth.models import User, Group
from django.utils import timezone
import pyotp
import secrets
from datetime import timedelta, datetime

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
    nem_fogadott_igazolas_tipusok = models.ManyToManyField('IgazolasTipus', blank=True, related_name='nem_fogado_osztalyok')

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
    ftv_hianyzas_id = models.IntegerField(null=True, blank=True, unique=True)  # FTV hiányzás ID for sync tracking

    # Korrekció
    diak_extra_ido_elotte = models.IntegerField(null=True, blank=True)  
    diak_extra_ido_utana = models.IntegerField(null=True, blank=True)


    # URL amire feltölti a diák a fényképet, nem Image repsonse -> Google Drive
    imgDriveURL = models.URLField(max_length=300, null=True, blank=True)

    # BKK Verification - JSON field for BKK related data
    bkk_verification = models.JSONField(null=True, blank=True)

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


# Password Reset Models

class PasswordResetOTP(models.Model):
    """
    Model to store OTP codes for password reset functionality.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    secret_key = models.CharField(max_length=32)  # TOTP secret key
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)
    attempts = models.IntegerField(default=0)  # Track failed attempts
    
    def generate_otp(self):
        """Generate a new 6-digit OTP code"""
        totp = pyotp.TOTP(self.secret_key, interval=300)  # 5 minutes validity
        return totp.now()
    
    def verify_otp(self, otp_code):
        """Verify the OTP code"""
        totp = pyotp.TOTP(self.secret_key, interval=300)
        return totp.verify(otp_code, valid_window=1)  # Allow 1 window tolerance
    
    def is_expired(self):
        """Check if OTP has expired (15 minutes from creation)"""
        return timezone.now() > self.created_at + timedelta(minutes=15)
    
    def can_attempt(self):
        """Check if user can still attempt OTP verification (max 5 attempts)"""
        return self.attempts < 5
    
    @classmethod
    def create_for_user(cls, user):
        """Create a new OTP for user, invalidating previous ones"""
        # Invalidate existing OTPs
        cls.objects.filter(user=user, is_used=False).update(is_used=True)
        
        # Create new OTP
        secret_key = pyotp.random_base32()
        return cls.objects.create(user=user, secret_key=secret_key)
    
    def __str__(self):
        return f"OTP for {self.user.username} - {self.created_at}"
    
    class Meta:
        verbose_name = 'Password Reset OTP'
        verbose_name_plural = 'Password Reset OTPs'
        ordering = ['-created_at']


class ForgotPasswordToken(models.Model):
    """
    Model to store temporary tokens for password reset after OTP verification.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True)  # Secure random token
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)
    
    def is_expired(self):
        """Check if token has expired (10 minutes from creation)"""
        return timezone.now() > self.created_at + timedelta(minutes=10)
    
    @classmethod
    def create_for_user(cls, user):
        """Create a new token for user, invalidating previous ones"""
        # Invalidate existing tokens
        cls.objects.filter(user=user, is_used=False).update(is_used=True)
        
        # Create new token
        token = secrets.token_urlsafe(48)
        return cls.objects.create(user=user, token=token)
    
    def __str__(self):
        return f"Reset Token for {self.user.username} - {self.created_at}"
    
    class Meta:
        verbose_name = 'Forgot Password Token'
        verbose_name_plural = 'Forgot Password Tokens'
        ordering = ['-created_at']


class FTVSyncMetadata(models.Model):
    """
    Model to store FTV sync metadata for tracking sync status.
    Each sync type (base, user_{id}, class_{id}) has its own record.
    """
    sync_type = models.CharField(max_length=100, unique=True)  # 'base', 'user_{id}', 'class_{id}'
    last_sync_time = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=20, default='never')  # 'success', 'failed', 'never'
    last_sync_stats = models.JSONField(null=True, blank=True)  # Store sync statistics
    
    @classmethod
    def get_or_create_metadata(cls, sync_type: str):
        """Get or create metadata for a sync type"""
        obj, created = cls.objects.get_or_create(sync_type=sync_type)
        return obj
    
    @classmethod
    def update_sync(cls, sync_type: str, status: str, stats: dict = None):
        """Update sync metadata"""
        obj = cls.get_or_create_metadata(sync_type)
        obj.last_sync_time = timezone.now()
        obj.last_sync_status = status
        if stats:
            obj.last_sync_stats = stats
        obj.save()
        return obj
    
    @classmethod
    def get_metadata(cls, sync_type: str) -> dict:
        """Get metadata as dictionary"""
        obj = cls.get_or_create_metadata(sync_type)
        result = {
            'last_sync_time': obj.last_sync_time.isoformat() if obj.last_sync_time else None,
            'last_sync_status': obj.last_sync_status,
            'last_sync_stats': obj.last_sync_stats
        }
        
        # Calculate age
        if obj.last_sync_time:
            age_seconds = (timezone.now() - obj.last_sync_time).total_seconds()
            result['sync_age_seconds'] = int(age_seconds)
            result['sync_age_minutes'] = round(age_seconds / 60, 1)
        else:
            result['sync_age_seconds'] = None
            result['sync_age_minutes'] = None
        
        return result
    
    def __str__(self):
        return f"FTV Sync Metadata [{self.sync_type}] - Last sync: {self.last_sync_time or 'Never'}"
    
    class Meta:
        verbose_name = 'FTV Sync Metadata'
        verbose_name_plural = 'FTV Sync Metadata'


class SystemMessage(models.Model):
    """
    Model to store system messages for displaying announcements, warnings, and errors to users.
    """
    # Severity choices
    SEVERITY_INFO = 'info'
    SEVERITY_WARNING = 'warning'
    SEVERITY_ERROR = 'error'
    
    SEVERITY_CHOICES = [
        (SEVERITY_INFO, 'Információ'),
        (SEVERITY_WARNING, 'Figyelmeztetés'),
        (SEVERITY_ERROR, 'Hiba'),
    ]
    
    # Message type choices
    MESSAGE_TYPE_USER = 'user'
    MESSAGE_TYPE_DEVELOPER = 'developer'
    MESSAGE_TYPE_OPERATOR = 'operator'
    MESSAGE_TYPE_SUPPORT = 'support'
    
    MESSAGE_TYPE_CHOICES = [
        (MESSAGE_TYPE_USER, 'Felhasználó'),
        (MESSAGE_TYPE_DEVELOPER, 'Fejlesztő'),
        (MESSAGE_TYPE_OPERATOR, 'Operátor'),
        (MESSAGE_TYPE_SUPPORT, 'Támogatás'),
    ]
    
    title = models.CharField(max_length=200, blank=False, null=False, verbose_name='Cím', 
                            help_text='A rendszerüzenet címe (maximum 200 karakter)')
    message = models.TextField(max_length=2000, blank=False, null=False, verbose_name='Üzenet', 
                              help_text='A rendszerüzenet tartalma (maximum 2000 karakter)')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default=SEVERITY_INFO, 
                               verbose_name='Súlyosság', 
                               help_text='Az üzenet súlyossága (info/warning/error)')
    messageType = models.CharField(max_length=20, choices=MESSAGE_TYPE_CHOICES, default=MESSAGE_TYPE_USER,
                                  verbose_name='Üzenet típusa',
                                  help_text='Az üzenet célközönsége (user/developer/operator/support)')
    showFrom = models.DateTimeField(blank=False, null=False, verbose_name='Megjelenítés kezdete', 
                                   help_text='Az üzenet megjelenítésének kezdő időpontja')
    showTo = models.DateTimeField(blank=False, null=False, verbose_name='Megjelenítés vége', 
                                 help_text='Az üzenet megjelenítésének záró időpontja')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Létrehozva', 
                                     help_text='A rendszerüzenet létrehozásának időpontja')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Módosítva', 
                                     help_text='A rendszerüzenet utolsó módosításának időpontja')

    def __str__(self):
        return self.title
    
    @classmethod
    def get_active_messages(cls, check_datetime=None):
        """Get all system messages that should be displayed at the given datetime (default: now)"""
        if check_datetime is None:
            check_datetime = datetime.now()
        
        return cls.objects.filter(
            showFrom__lte=check_datetime,
            showTo__gte=check_datetime
        ).order_by('showFrom')
    
    def is_active(self, check_datetime=None):
        """Check if this message should be displayed at the given datetime (default: now)"""
        if check_datetime is None:
            check_datetime = datetime.now()
        
        return self.showFrom <= check_datetime <= self.showTo
    
    class Meta:
        verbose_name = "Rendszerüzenet"
        verbose_name_plural = "Rendszerüzenetek"
        ordering = ['-showFrom']