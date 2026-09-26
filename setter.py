"""Native Linux wallpaper setters. Run inside the target graphical session."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile

LOG = logging.getLogger(__name__)
TIMEOUT = 15.0


class WallpaperError(RuntimeError):
    """The desktop could not be detected or its wallpaper could not be changed."""


def _run(arguments: list[str]) -> str:
    if shutil.which(arguments[0]) is None:
        raise WallpaperError(f"Required utility is not installed: {arguments[0]}")
    try:
        result = subprocess.run(arguments, check=True, capture_output=True,
                                text=True, timeout=TIMEOUT)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "no diagnostic output").strip()
        raise WallpaperError(f"{arguments[0]} failed: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WallpaperError(f"Cannot run {arguments[0]}: {exc}") from exc
    return result.stdout.strip()


def detect_desktop() -> str:
    """Return a backend name, prioritizing explicit desktop session identifiers."""
    desktops = set(filter(None, re.split(r"[:;\s]+", os.environ.get("XDG_CURRENT_DESKTOP", "").lower())))
    for aliases, backend in (
        ({"hyprland"}, "hyprland"), ({"sway"}, "sway"),
        ({"cinnamon", "x-cinnamon"}, "cinnamon"), ({"gnome", "unity", "ubuntu"}, "gnome"),
        ({"kde", "plasma"}, "kde"), ({"xfce", "xfce4"}, "xfce"),
    ):
        if desktops & aliases:
            return backend
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    # DISPLAY can also exist under Wayland (XWayland); do not use feh there.
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland" and not os.environ.get("WAYLAND_DISPLAY"):
        if os.environ.get("DISPLAY"):
            return "x11"
    raise WallpaperError("Unsupported desktop session; run inside GNOME, Cinnamon, KDE, XFCE, Sway, Hyprland or X11")


def _gsettings(path: Path, desktop: str) -> None:
    schema = "org.cinnamon.desktop.background" if desktop == "cinnamon" else "org.gnome.desktop.background"
    keys = set(_run(["gsettings", "list-keys", schema]).splitlines())
    if "picture-uri" not in keys:
        raise WallpaperError(f"{schema} has no picture-uri key")
    # JSON string quoting also produces a valid GVariant string here.
    uri = json.dumps(path.as_uri())
    _run(["gsettings", "set", schema, "picture-uri", uri])
    if "picture-uri-dark" in keys:
        _run(["gsettings", "set", schema, "picture-uri-dark", uri])


def _xfce(path: Path) -> None:
    properties = _run(["xfconf-query", "-c", "xfce4-desktop", "-l"]).splitlines()
    targets = [p for p in properties if p.startswith("/backdrop/")
               and p.endswith(("/last-image", "/image-path"))]
    if not targets:
        raise WallpaperError("No XFCE wallpaper properties found; initialize a wallpaper in Desktop Settings first")
    for prop in targets:
        _run(["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-s", str(path)])
        style = prop.rsplit("/", 1)[0] + "/image-style"
        if style in properties:
            _run(["xfconf-query", "-c", "xfce4-desktop", "-p", style, "-s", "5"])


def _hyprland(path: Path) -> None:
    if any(c in str(path) for c in ",\n\r"):
        raise WallpaperError("Hyprpaper IPC cannot safely represent commas or newlines in this path")
    try:
        monitors = json.loads(_run(["hyprctl", "-j", "monitors"]))
        names = [monitor["name"] for monitor in monitors]
    except (ValueError, KeyError, TypeError) as exc:
        raise WallpaperError("Cannot decode Hyprland monitor list") from exc
    if not names:
        raise WallpaperError("Hyprland reports no active monitors")
    help_text = _run(["hyprctl", "hyprpaper", "--help"])
    if re.search(r"\bpreload\b", help_text):
        response = _run(["hyprctl", "hyprpaper", "preload", str(path)])
        if response.lower() != "ok" and "already" not in response.lower():
            raise WallpaperError(f"Hyprpaper preload failed: {response}; start hyprpaper with IPC enabled")
    for name in names:
        response = _run(["hyprctl", "hyprpaper", "wallpaper", f"{name},{path}"])
        if response.lower() != "ok":
            raise WallpaperError(f"Hyprpaper failed: {response}; start hyprpaper with IPC enabled")


def _process_token(pid: int) -> str:
    # starttime follows the parenthesized comm field in /proc/PID/stat.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def _sway(path: Path) -> None:
    executable = shutil.which("swaybg")
    if executable is None:
        raise WallpaperError("Required utility is not installed: swaybg")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", str(Path.home() / ".cache"))) / "wiki-wallpaper"
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    session = os.environ.get("SWAYSOCK") or os.environ.get("WAYLAND_DISPLAY", "default")
    key = hashlib.sha256(session.encode()).hexdigest()[:16]
    state = runtime / f"swaybg-{key}.json"
    with (runtime / f"swaybg-{key}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous: dict[str, object] = {}
        try:
            previous = json.loads(state.read_text())
        except (OSError, ValueError):
            pass
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen([executable, "-i", str(path), "-m", "fill"],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=errors, start_new_session=True)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            else:
                errors.seek(0)
                raise WallpaperError(f"swaybg exited during startup: {errors.read().decode(errors='replace')}")
            try:
                token = _process_token(process.pid)
                state.write_text(json.dumps({"pid": process.pid, "token": token}))
            except OSError:
                process.terminate()
                process.wait(timeout=TIMEOUT)
                raise
        # Only stop our prior process, never unrelated swaybg instances.
        try:
            old_pid = int(str(previous["pid"]))
            if old_pid > 1 and _process_token(old_pid) == previous["token"]:
                cmdline = Path(f"/proc/{old_pid}/cmdline").read_bytes().split(b"\0")
                if cmdline and Path(os.fsdecode(cmdline[0])).name == "swaybg":
                    os.kill(old_pid, signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            pass


def set_wallpaper(image: str | Path) -> str:
    """Apply a local image, returning its backend name or raising WallpaperError.

    Sway's owned swaybg process remains running; repeated calls replace it.
    Hyprland requires an existing hyprpaper daemon with IPC enabled. Desktop
    commands may partially succeed before an error on another monitor/key.
    """
    try:
        path = Path(image).expanduser().resolve(strict=True)
        if not path.is_file() or not os.access(path, os.R_OK):
            raise WallpaperError(f"Wallpaper is not a readable file: {path}")
        desktop = detect_desktop()
        if desktop in {"gnome", "cinnamon"}:
            _gsettings(path, desktop)
        elif desktop == "kde":
            _run(["plasma-apply-wallpaperimage", str(path)])
        elif desktop == "xfce":
            _xfce(path)
        elif desktop == "hyprland":
            _hyprland(path)
        elif desktop == "sway":
            _sway(path)
        else:
            _run(["feh", "--bg-fill", str(path)])
        LOG.info("Applied wallpaper through %s: %s", desktop, path)
        return desktop
    except (OSError, subprocess.SubprocessError) as exc:
        raise WallpaperError(f"Could not apply wallpaper: {exc}") from exc
