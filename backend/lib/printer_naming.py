import logging
from typing import Dict, Optional

from django.db import transaction
from django.utils import timezone

from app.models import Printer, User

LOGGER = logging.getLogger(__name__)

# Printer.name is max_length=256. Leave room for a " (123)" suffix.
MAX_HOSTNAME_LENGTH = 240


def hostname_from_settings(printer_settings: Dict) -> Optional[str]:
    """
    Agents (OctoPrint-Obico, moonraker-obico) report `platform_uname` as the
    list form of Python's platform.uname(): [system, node, release, version, machine, processor].
    `node` is the host's network name.
    """
    uname = (printer_settings or {}).get('platform_uname')
    if not isinstance(uname, (list, tuple)) or len(uname) < 2:
        return None

    node = uname[1]
    if not isinstance(node, str):
        return None

    node = node.strip()[:MAX_HOSTNAME_LENGTH].strip()
    return node or None


def unique_printer_name(user_id: int, base_name: str, exclude_printer_id: Optional[int] = None) -> str:
    """
    Returns `base_name` if none of the user's printers uses it, otherwise
    `base_name (n)` with the smallest n >= 1 that is not taken.
    Archived (i.e. "deleted" in the UI) printers don't count.
    """
    taken = set(
        Printer.objects.filter(user_id=user_id)
        .exclude(id=exclude_printer_id)
        .values_list('name', flat=True)
    )
    if base_name not in taken:
        return base_name

    n = 1
    while f'{base_name} ({n})' in taken:
        n += 1
    return f'{base_name} ({n})'


def name_printer_after_hostname_if_needed(printer: Printer, printer_settings: Dict) -> None:
    """
    Renames a printer that still has the placeholder name to its hostname.
    A name the user has chosen is never overwritten.
    """
    if printer.name != Printer.DEFAULT_NAME:
        return

    hostname = hostname_from_settings(printer_settings)
    if not hostname:
        return

    with transaction.atomic():
        # Serialize naming per user so that two printers with the same hostname
        # linked at the same time don't both get the same name.
        User.objects.select_for_update().filter(id=printer.user_id).first()
        new_name = unique_printer_name(printer.user_id, hostname, exclude_printer_id=printer.id)
        # Conditional update: only rename if the user hasn't renamed the printer in the meantime.
        renamed = Printer.with_archived.filter(id=printer.id, name=Printer.DEFAULT_NAME).update(
            name=new_name, updated_at=timezone.now())

    if renamed:
        printer.name = new_name
        LOGGER.info(f'Printer {printer.id} named after its hostname: "{new_name}"')
    else:
        printer.refresh_from_db(fields=['name'])
