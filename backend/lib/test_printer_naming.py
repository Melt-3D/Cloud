from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from api.tests import setup_syndicate
from app.models import Printer, User
from lib.printer_naming import (
    hostname_from_settings,
    name_printer_after_hostname_if_needed,
    unique_printer_name,
)


def settings_with_hostname(hostname):
    return {'platform_uname': ['Linux', hostname, '6.1.21-v8+', '#1 SMP PREEMPT', 'aarch64', '']}


class HostnameFromSettingsTestCase(TestCase):
    def test_extracts_node_from_platform_uname(self):
        self.assertEqual(hostname_from_settings(settings_with_hostname('voron')), 'voron')

    def test_strips_whitespace(self):
        self.assertEqual(hostname_from_settings(settings_with_hostname('  voron \n')), 'voron')

    def test_missing_or_malformed_uname(self):
        self.assertIsNone(hostname_from_settings({}))
        self.assertIsNone(hostname_from_settings(None))
        self.assertIsNone(hostname_from_settings({'platform_uname': []}))
        self.assertIsNone(hostname_from_settings({'platform_uname': ['Linux']}))
        self.assertIsNone(hostname_from_settings({'platform_uname': ['Linux', None]}))
        self.assertIsNone(hostname_from_settings({'platform_uname': ['Linux', '   ']}))
        self.assertIsNone(hostname_from_settings({'platform_uname': 'Linux voron'}))

    def test_long_hostname_is_truncated(self):
        hostname = hostname_from_settings(settings_with_hostname('a' * 300))
        self.assertEqual(len(hostname), 240)


class PrinterNamingTestCase(TestCase):
    def setUp(self):
        patch('config.celery.celery_app.send_task').start()
        self.addCleanup(patch.stopall)
        syndicate = setup_syndicate()
        self.user = User.objects.create(email='naming@example.invalid', syndicate=syndicate)
        self.other_user = User.objects.create(email='naming-other@example.invalid', syndicate=syndicate)
        self.token_seq = 0

    def make_printer(self, name=Printer.DEFAULT_NAME, user=None, **attrs):
        self.token_seq += 1
        return Printer.objects.create(
            name=name, user=user or self.user, auth_token=f'naming-test-{self.token_seq}', **attrs)

    def test_unique_name_when_free(self):
        self.assertEqual(unique_printer_name(self.user.id, 'voron'), 'voron')

    def test_unique_name_appends_next_free_number_from_one(self):
        self.make_printer('voron')
        self.assertEqual(unique_printer_name(self.user.id, 'voron'), 'voron (1)')

        self.make_printer('voron (1)')
        self.assertEqual(unique_printer_name(self.user.id, 'voron'), 'voron (2)')

    def test_unique_name_fills_gaps(self):
        self.make_printer('voron')
        self.make_printer('voron (2)')
        self.assertEqual(unique_printer_name(self.user.id, 'voron'), 'voron (1)')

    def test_unique_name_ignores_other_users_and_archived_printers(self):
        self.make_printer('voron', user=self.other_user)
        self.make_printer('voron', archived_at=timezone.now())
        self.assertEqual(unique_printer_name(self.user.id, 'voron'), 'voron')

    def test_renames_placeholder_printer_to_hostname(self):
        printer = self.make_printer()
        name_printer_after_hostname_if_needed(printer, settings_with_hostname('voron'))

        printer.refresh_from_db()
        self.assertEqual(printer.name, 'voron')

    def test_renames_with_suffix_when_hostname_taken(self):
        self.make_printer('voron')
        printer = self.make_printer()
        name_printer_after_hostname_if_needed(printer, settings_with_hostname('voron'))

        printer.refresh_from_db()
        self.assertEqual(printer.name, 'voron (1)')

    def test_two_printers_with_same_hostname(self):
        first = self.make_printer()
        second = self.make_printer()
        name_printer_after_hostname_if_needed(first, settings_with_hostname('mainsailos'))
        name_printer_after_hostname_if_needed(second, settings_with_hostname('mainsailos'))

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.name, 'mainsailos')
        self.assertEqual(second.name, 'mainsailos (1)')

    def test_does_not_rename_user_named_printer(self):
        printer = self.make_printer('Workshop Ender')
        name_printer_after_hostname_if_needed(printer, settings_with_hostname('voron'))

        printer.refresh_from_db()
        self.assertEqual(printer.name, 'Workshop Ender')

    def test_does_not_overwrite_rename_made_after_printer_was_loaded(self):
        printer = self.make_printer()
        Printer.objects.filter(id=printer.id).update(name='Renamed in the browser')

        # `printer` still holds the stale placeholder name in memory
        name_printer_after_hostname_if_needed(printer, settings_with_hostname('voron'))

        self.assertEqual(printer.name, 'Renamed in the browser')
        printer.refresh_from_db()
        self.assertEqual(printer.name, 'Renamed in the browser')

    def test_keeps_placeholder_without_hostname(self):
        printer = self.make_printer()
        name_printer_after_hostname_if_needed(printer, {})

        printer.refresh_from_db()
        self.assertEqual(printer.name, Printer.DEFAULT_NAME)

    def test_status_message_triggers_rename(self):
        from api.octoprint_messages import process_printer_status

        printer = self.make_printer()
        with patch('api.octoprint_messages.cache'), patch('api.octoprint_messages.channels'):
            process_printer_status(printer, {'settings': settings_with_hostname('voron')})

        printer.refresh_from_db()
        self.assertEqual(printer.name, 'voron')
