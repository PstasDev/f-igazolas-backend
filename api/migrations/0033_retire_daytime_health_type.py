import unicodedata

from django.db import migrations


def normalize_name(value):
    normalized = unicodedata.normalize('NFKD', value)
    normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
    return ' '.join(normalized.casefold().split())


def retire_daytime_health_type(apps, schema_editor):
    IgazolasTipus = apps.get_model('api', 'IgazolasTipus')
    Igazolas = apps.get_model('api', 'Igazolas')

    fallback, _ = IgazolasTipus.objects.get_or_create(
        nev='Egyéb',
        defaults={
            'leiras': 'Egyéb okok miatt volt távol a tanuló',
            'beleszamit': True,
            'iskolaerdeku': False,
            'category': 'egyeb',
            'category_emoji': '📝',
        },
    )

    retired_types = [
        tipus
        for tipus in IgazolasTipus.objects.all().only('id', 'nev')
        if normalize_name(tipus.nev) == 'egeszsegugyi okok (napkozben)'
    ]
    for tipus in retired_types:
        Igazolas.objects.filter(tipus_id=tipus.id).update(tipus_id=fallback.id)
        tipus.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0032_igazolastipus_available_tagozat'),
    ]

    operations = [
        migrations.RunPython(retire_daytime_health_type, migrations.RunPython.noop),
    ]
