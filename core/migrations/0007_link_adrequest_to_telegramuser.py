# Data migration: link existing AdRequest rows to TelegramUser by telegram_user_id

from django.db import migrations


def link_adrequest_user(apps, schema_editor):
    AdRequest = apps.get_model('core', 'AdRequest')
    TelegramUser = apps.get_model('core', 'TelegramUser')
    for ad in AdRequest.objects.filter(user__isnull=True).exclude(telegram_user_id__isnull=True):
        user = TelegramUser.objects.filter(telegram_user_id=ad.telegram_user_id).first()
        if user:
            ad.user_id = user.pk
            ad.save(update_fields=['user_id'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_telegramuser_verificationcode_adrequest_user_contact_snapshot'),
    ]

    operations = [
        migrations.RunPython(link_adrequest_user, noop),
    ]
