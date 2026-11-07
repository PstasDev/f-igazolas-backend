import unicodedata

from django.db import migrations, models


def reconcile_frontend_type_metadata(apps, schema_editor):
    IgazolasTipus = apps.get_model('api', 'IgazolasTipus')
    categories = {
        'orvosi igazolas': 'egeszsegugy',
        'rosszullet': 'egeszsegugy',
        'csaladi okok': 'csaladi',
        'kozlekedes': 'kozlekedes',
        'oktv': 'verseny',
        'verseny': 'verseny',
        'nyelvvizsga': 'tanulmanyi',
        'erettsegi': 'tanulmanyi',
    }
    for tipus in IgazolasTipus.objects.all().only(
        'id', 'nev', 'available_tagozat', 'requires_studios', 'category'
    ):
        normalized = unicodedata.normalize('NFKD', tipus.nev)
        normalized = ''.join(char for char in normalized if not unicodedata.combining(char)).casefold()
        normalized = normalized.strip()
        update_fields = []
        if normalized == 'medias tavollet' and tipus.available_tagozat != 'F':
            tipus.available_tagozat = 'F'
            update_fields.append('available_tagozat')
        if normalized == 'studios tavollet' and not tipus.requires_studios:
            tipus.requires_studios = True
            update_fields.append('requires_studios')
        if normalized in categories and tipus.category != categories[normalized]:
            tipus.category = categories[normalized]
            update_fields.append('category')
        if update_fields:
            tipus.save(update_fields=update_fields)


def ensure_fallback_type(apps, schema_editor):
    IgazolasTipus = apps.get_model('api', 'IgazolasTipus')
    fallback_type = None
    for tipus in IgazolasTipus.objects.all().only('id', 'nev'):
        normalized = ''.join(
            char for char in unicodedata.normalize('NFKD', tipus.nev)
            if not unicodedata.combining(char)
        ).casefold().strip()
        if normalized == 'egyeb':
            fallback_type = tipus
            break
    if fallback_type is None:
        IgazolasTipus.objects.create(
            nev='Egyéb',
            leiras='Egyéb okok miatt volt távol a tanuló',
            beleszamit=True,
            iskolaerdeku=False,
            category='egyeb',
            category_emoji='📝',
            display_order=0,
        )
    elif fallback_type.nev != 'Egyéb':
        fallback_type.nev = 'Egyéb'
        fallback_type.save(update_fields=['nev'])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0031_ssologinflow'),
    ]

    operations = [
        migrations.AddField(
            model_name='igazolastipus',
            name='available_tagozat',
            field=models.CharField(
                blank=True,
                help_text='Ha meg van adva, az igazolástípust csak ez a tagozat használhatja (pl. F).',
                max_length=1,
                null=True,
                verbose_name='Elérhető tagozat',
            ),
        ),
        migrations.RunPython(ensure_fallback_type, migrations.RunPython.noop),
        migrations.RunPython(reconcile_frontend_type_metadata, migrations.RunPython.noop),
    ]
