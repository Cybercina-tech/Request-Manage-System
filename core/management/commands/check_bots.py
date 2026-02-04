"""
Iranio — Management command: check bot health (getMe, webhook, status).
Run: python manage.py check_bots
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from core.models import TelegramBot
from core.services import test_telegram_connection, get_webhook_info


class Command(BaseCommand):
    help = 'Run health checks on all bots: getMe, webhook status; update last_heartbeat and status. Mark offline if >5 min no heartbeat.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--heartbeat-threshold-minutes',
            type=int,
            default=5,
            help='Minutes after last_heartbeat to mark bot offline (default: 5)',
        )

    def handle(self, *args, **options):
        threshold_minutes = options['heartbeat_threshold_minutes']
        now = timezone.now()
        cutoff = now - timedelta(minutes=threshold_minutes)

        # Mark ONLINE bots with stale heartbeat as OFFLINE
        TelegramBot.objects.filter(status=TelegramBot.Status.ONLINE).filter(
            Q(last_heartbeat__isnull=True) | Q(last_heartbeat__lt=cutoff)
        ).update(status=TelegramBot.Status.OFFLINE)

        for bot in TelegramBot.objects.all():
            self._check_bot(bot, now, cutoff)

    def _check_bot(self, bot, now, cutoff):
        token = bot.get_decrypted_token()
        if not token:
            bot.status = TelegramBot.Status.ERROR
            bot.save(update_fields=['status'])
            self.stdout.write(self.style.WARNING(f'Bot {bot.name} (pk={bot.pk}): no token'))
            return

        # getMe
        ok, msg = test_telegram_connection(token)
        if ok:
            bot.last_heartbeat = now
            bot.status = TelegramBot.Status.ONLINE
            bot.save(update_fields=['last_heartbeat', 'status'])
            self.stdout.write(self.style.SUCCESS(f'Bot {bot.name} (pk={bot.pk}): online'))
        else:
            bot.status = TelegramBot.Status.ERROR
            bot.save(update_fields=['status'])
            self.stdout.write(self.style.ERROR(f'Bot {bot.name} (pk={bot.pk}): error — {msg}'))
            return

        # Webhook status (informational)
        ok_wh, wh_result = get_webhook_info(token)
        if ok_wh and isinstance(wh_result, dict):
            url = wh_result.get('url') or '(none)'
            self.stdout.write(f'  Webhook: {url}')

        # Mark offline if last_heartbeat too old (e.g. no recent check_bots run)
        if bot.last_heartbeat and bot.last_heartbeat < cutoff:
            bot.status = TelegramBot.Status.OFFLINE
            bot.save(update_fields=['status'])
            self.stdout.write(self.style.WARNING(f'Bot {bot.name}: marked offline (heartbeat > threshold)'))
