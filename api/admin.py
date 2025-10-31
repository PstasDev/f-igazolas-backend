from django.contrib import admin
from .models import Profile, Osztaly, Mulasztas, IgazolasTipus, Igazolas


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
    list_display = ['id', 'get_student', 'get_osztaly', 'eleje', 'vege', 'tipus', 'allapot', 'diak', 'ftv', 'korrigalt', 'rogzites_datuma']
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
