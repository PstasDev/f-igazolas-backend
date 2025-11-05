from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import Profile, Osztaly, Mulasztas, IgazolasTipus, Igazolas, SystemMessage, TanitasiSzunet, Override


# Custom filter for last_login that excludes nulls
class HasLoggedInFilter(admin.SimpleListFilter):
    title = 'bejelentkezés státusz'
    parameter_name = 'has_logged_in'
    
    def lookups(self, request, model_admin):
        return (
            ('yes', 'Bejelentkezett már'),
            ('no', 'Még nem jelentkezett be'),
        )
    
    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(last_login__isnull=False)
        if self.value() == 'no':
            return queryset.filter(last_login__isnull=True)
        return queryset


# Custom User Admin
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'last_login', 'is_staff', 'is_active']
    list_filter = BaseUserAdmin.list_filter + (HasLoggedInFilter,)
    actions = ['flip_first_last_name']
    
    @admin.action(description='Keresztnév és vezetéknév felcserélése')
    def flip_first_last_name(self, request, queryset):
        """Flip first_name and last_name for selected users"""
        updated_count = 0
        for user in queryset:
            user.first_name, user.last_name = user.last_name, user.first_name
            user.save()
            updated_count += 1
        self.message_user(request, f'{updated_count} felhasználó neve felcserélve.')


# Unregister the default User admin and register the custom one
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


# Profile Admin
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'get_osztaly']
    search_fields = ['user__username', 'user__first_name', 'user__last_name']
    list_filter = []
    raw_id_fields = ['user']
    
    def get_osztaly(self, obj):
        osztaly = obj.osztalyom()
        return str(osztaly) if osztaly else '-'
    get_osztaly.short_description = 'Osztály'


# Osztaly Admin
@admin.register(Osztaly)
class OsztalyAdmin(admin.ModelAdmin):
    list_display = ['id', '__str__', 'tagozat', 'kezdes_eve', 'get_tanulok_count', 'get_osztalyfonokok_count']
    list_filter = ['tagozat', 'kezdes_eve']
    search_fields = ['tagozat']
    filter_horizontal = ['tanulok', 'osztalyfonokok', 'nem_fogadott_igazolas_tipusok']
    
    def get_tanulok_count(self, obj):
        return obj.tanulok.count()
    get_tanulok_count.short_description = 'Tanulók száma'
    
    def get_osztalyfonokok_count(self, obj):
        return obj.osztalyfonokok.count()
    get_osztalyfonokok_count.short_description = 'Osztályfőnökök száma'


# Mulasztas Admin
@admin.register(Mulasztas)
class MulasztasAdmin(admin.ModelAdmin):
    list_display = ['id', 'datum', 'ora', 'tantargy', 'tipus', 'igazolt', 'igazolas_tipusa', 'rogzites_datuma']
    list_filter = ['tipus', 'igazolt', 'datum', 'rogzites_datuma', 'tantargy']
    search_fields = ['tantargy', 'tema', 'igazolas_tipusa']
    date_hierarchy = 'datum'
    ordering = ['-datum', 'ora']


# IgazolasTipus Admin
@admin.register(IgazolasTipus)
class IgazolasTipusAdmin(admin.ModelAdmin):
    list_display = ['id', 'nev', 'beleszamit', 'iskolaerdeku']
    list_filter = ['beleszamit', 'iskolaerdeku']
    search_fields = ['nev', 'leiras']


# Igazolas Admin
@admin.register(Igazolas)
class IgazolasAdmin(admin.ModelAdmin):
    list_display = ['id', 'get_student', 'get_osztaly', 'eleje', 'vege', 'tipus', 'allapot', 'get_megjegyzes_diak', 'diak', 'ftv', 'korrigalt', 'rogzites_datuma']
    list_filter = ['allapot', 'diak', 'ftv', 'korrigalt', 'kretaban_rogzitettem', 'tipus', 'rogzites_datuma']
    search_fields = ['profile__user__username', 'profile__user__first_name', 'profile__user__last_name', 'megjegyzes_diak', 'megjegyzes_tanar']
    date_hierarchy = 'rogzites_datuma'
    raw_id_fields = ['profile']
    filter_horizontal = ['mulasztasok']
    readonly_fields = ['rogzites_datuma']
    ordering = ['-rogzites_datuma']
    
    fieldsets = (
        ('Alapadatok', {
            'fields': ('profile', 'eleje', 'vege', 'tipus')
        }),
        ('Mulasztások', {
            'fields': ('mulasztasok',)
        }),
        ('Diák adatok', {
            'fields': ('megjegyzes_diak', 'diak_extra_ido_elotte', 'diak_extra_ido_utana', 'imgDriveURL')
        }),
        ('Forrás és típus', {
            'fields': ('diak', 'ftv', 'korrigalt', 'bkk_verification')
        }),
        ('Tanári kezelés', {
            'fields': ('allapot', 'megjegyzes_tanar', 'kretaban_rogzitettem')
        }),
        ('Egyéb', {
            'fields': ('rogzites_datuma',)
        }),
    )
    
    def get_student(self, obj):
        return obj.profile.user.get_full_name() or obj.profile.user.username
    get_student.short_description = 'Diák'
    get_student.admin_order_field = 'profile__user__last_name'
    
    def get_osztaly(self, obj):
        osztaly = obj.profile.osztalyom()
        return str(osztaly) if osztaly else '-'
    get_osztaly.short_description = 'Osztály'
    
    def get_megjegyzes_diak(self, obj):
        if obj.megjegyzes_diak:
            # Truncate to 50 chars for table display
            return obj.megjegyzes_diak[:50] + '...' if len(obj.megjegyzes_diak) > 50 else obj.megjegyzes_diak
        return '-'
    get_megjegyzes_diak.short_description = 'Indoklás'


# SystemMessage Admin
@admin.register(SystemMessage)
class SystemMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'severity', 'messageType', 'showFrom', 'showTo', 'is_currently_active', 'created_at']
    list_filter = ['severity', 'messageType', 'showFrom', 'showTo', 'created_at']
    search_fields = ['title', 'message']
    date_hierarchy = 'showFrom'
    ordering = ['-showFrom']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Üzenet tartalma', {
            'fields': ('title', 'message', 'severity', 'messageType')
        }),
        ('Megjelenítés időzítése', {
            'fields': ('showFrom', 'showTo')
        }),
        ('Metaadatok', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def is_currently_active(self, obj):
        return obj.is_active()
    is_currently_active.boolean = True
    is_currently_active.short_description = 'Aktív'


# TanitasiSzunet Admin
@admin.register(TanitasiSzunet)
class TanitasiSzunetAdmin(admin.ModelAdmin):
    list_display = ['id', 'get_display_name', 'type', 'from_date', 'to_date', 'get_duration_days']
    list_filter = ['type', 'from_date', 'to_date']
    search_fields = ['name', 'description', 'type']
    date_hierarchy = 'from_date'
    ordering = ['from_date']
    
    fieldsets = (
        ('Alapadatok', {
            'fields': ('type', 'name', 'from_date', 'to_date')
        }),
        ('További információk', {
            'fields': ('description',)
        }),
    )
    
    def get_display_name(self, obj):
        return obj.name if obj.name else obj.get_type_display()
    get_display_name.short_description = 'Név'
    
    def get_duration_days(self, obj):
        duration = (obj.to_date - obj.from_date).days + 1
        return f"{duration} nap"
    get_duration_days.short_description = 'Időtartam'


# Override Admin
@admin.register(Override)
class OverrideAdmin(admin.ModelAdmin):
    list_display = ['id', 'date', 'is_required', 'get_scope', 'get_reason_short']
    list_filter = ['is_required', 'date', 'class_id']
    search_fields = ['reason', 'class_id__tagozat']
    date_hierarchy = 'date'
    ordering = ['date']
    raw_id_fields = ['class_id']
    
    fieldsets = (
        ('Kivétel részletei', {
            'fields': ('date', 'is_required', 'class_id')
        }),
        ('Indoklás', {
            'fields': ('reason',)
        }),
    )
    
    def get_scope(self, obj):
        return str(obj.class_id) if obj.class_id else 'Minden osztály'
    get_scope.short_description = 'Hatókör'
    
    def get_reason_short(self, obj):
        if obj.reason:
            return obj.reason[:50] + '...' if len(obj.reason) > 50 else obj.reason
        return '-'
    get_reason_short.short_description = 'Indoklás'

