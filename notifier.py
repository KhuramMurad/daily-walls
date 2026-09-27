"""Optional desktop notifications; notification failures are nonfatal."""
from __future__ import annotations

from html import escape
import logging
import subprocess

from fetcher import Wallpaper
from platform_support import is_windows


def notify_removal(count: int) -> bool:
    """Announce permanent retention cleanup even when apply notices are off."""
    message = (f"{count} saved wallpaper(s) are being removed permanently because they are 10 days old. "
               "Your current desktop wallpaper will be kept until you change it.")
    logging.getLogger(__name__).info(message)
    if is_windows():
        from windows.integration import notify
        return notify("Wallpapers being removed permanently", message)
    try:
        subprocess.run(["notify-send", "--app-name=Daily Walls", "--urgency=normal",
                        "--expire-time=15000", "--", "Wallpapers being removed permanently", message],
                       check=True, capture_output=True, text=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logging.getLogger(__name__).warning("Could not show removal notification: %s", exc)
        return False


def notify_wallpaper(wallpaper: Wallpaper) -> bool:
    """Show title, photographer and license using notify-send."""
    if is_windows():
        from windows.integration import notify
        return notify("Wallpaper updated", f"{wallpaper.title}\n{wallpaper.photographer} · {wallpaper.license}")
    body = escape(f"{wallpaper.title}\n{wallpaper.photographer}\n{wallpaper.license}")
    try:
        subprocess.run(
            ["notify-send", "--app-name=Daily Walls", "--icon=preferences-desktop-wallpaper",
             "--", "Wallpaper updated", body],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logging.getLogger(__name__).warning("Could not show notification: %s", exc)
        return False
