"""
Iraniu — Management command: test Telegram connectivity.
Run: python manage.py check_telegram [--token TOKEN] [--bot-id BOT_ID]
"""

from django.core.management.base import BaseCommand
from django.core.management import CommandError

from core.models import TelegramBot
from core.services.telegram_client import check_telegram_health, TelegramStatus


class Command(BaseCommand):
    help = 'Test Telegram Bot API connectivity. Use --token to test a token directly, or --bot-id to test a saved bot.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--token',
            type=str,
            help='Bot token to test (if not provided, use --bot-id)',
        )
        parser.add_argument(
            '--bot-id',
            type=int,
            help='TelegramBot ID to test (if not provided, use --token)',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Test all active bots',
        )

    def handle(self, *args, **options):
        token = options.get('token')
        bot_id = options.get('bot_id')
        test_all = options.get('all')

        if test_all:
            self._test_all_bots()
        elif bot_id:
            self._test_bot_by_id(bot_id)
        elif token:
            self._test_token(token)
        else:
            raise CommandError('Provide --token, --bot-id, or --all')

    def _test_token(self, token: str):
        """Test a token directly."""
        self.stdout.write(f'Testing token: {token[:4]}...{token[-4:]}')
        status, message, bot_info = check_telegram_health(token)
        self._print_result(status, message, bot_info)

    def _test_bot_by_id(self, bot_id: int):
        """Test a bot by ID."""
        try:
            bot = TelegramBot.objects.get(pk=bot_id)
        except TelegramBot.DoesNotExist:
            raise CommandError(f'Bot with ID {bot_id} not found')

        self.stdout.write(f'Testing bot: {bot.name} (ID: {bot_id})')
        token = bot.get_decrypted_token()
        if not token:
            self.stdout.write(self.style.ERROR('Bot has no token'))
            return

        status, message, bot_info = check_telegram_health(token)
        self._print_result(status, message, bot_info)

    def _test_all_bots(self):
        """Test all active bots."""
        bots = TelegramBot.objects.filter(is_active=True)
        if not bots.exists():
            self.stdout.write(self.style.WARNING('No active bots found'))
            return

        self.stdout.write(f'Testing {bots.count()} active bot(s)...\n')
        for bot in bots:
            self.stdout.write(f'\nBot: {bot.name} (ID: {bot.pk})')
            token = bot.get_decrypted_token()
            if not token:
                self.stdout.write(self.style.ERROR('  No token'))
                continue

            status, message, bot_info = check_telegram_health(token)
            self._print_result(status, message, bot_info, indent='  ')

    def _print_result(self, status: TelegramStatus, message: str, bot_info: dict = None, indent: str = ''):
        """Print formatted result."""
        if status == TelegramStatus.OK:
            self.stdout.write(self.style.SUCCESS(f'{indent}✓ {message}'))
            if bot_info:
                self.stdout.write(f'{indent}  Bot ID: {bot_info.get("id")}')
                self.stdout.write(f'{indent}  Username: @{bot_info.get("username")}')
                self.stdout.write(f'{indent}  First name: {bot_info.get("first_name")}')
        elif status == TelegramStatus.SSL_ERROR:
            self.stdout.write(self.style.ERROR(f'{indent}✗ SSL Error: {message}'))
        elif status == TelegramStatus.NETWORK_ERROR:
            self.stdout.write(self.style.ERROR(f'{indent}✗ Network Error: {message}'))
        elif status == TelegramStatus.AUTH_ERROR:
            self.stdout.write(self.style.ERROR(f'{indent}✗ Auth Error: {message}'))
        elif status == TelegramStatus.TIMEOUT_ERROR:
            self.stdout.write(self.style.ERROR(f'{indent}✗ Timeout Error: {message}'))
        else:
            self.stdout.write(self.style.ERROR(f'{indent}✗ Error: {message}'))
