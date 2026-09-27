"""Safe packaged-app smoke test: no network, wallpaper changes, or real library edits."""
import json
from pathlib import Path
import ssl
import tempfile


def run():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, Gtk
    from PIL import Image, features
    from app import WallpaperApplication
    from library import WallpaperLibrary
    from platform_support import exclusive_lock
    from windows.desktop import prepare_image

    if not features.check('webp'):
        raise RuntimeError('Pillow WebP decoder is missing')
    context = ssl.create_default_context()
    if not context.get_ca_certs():
        raise RuntimeError('No TLS trust certificates are available')
    app = WallpaperApplication()
    app.register(None)
    window = Gtk.ApplicationWindow(application=app, title='Daily Walls package test')
    window.set_default_size(320, 180)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for suffix in ('jpg', 'png', 'webp'):
            path = root / ('image.' + suffix)
            Image.new('RGB', (1920, 1080), '#31575b').save(path)
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 320, 180, True)
            if pixbuf.get_width() != 320 or pixbuf.get_height() != 180:
                raise RuntimeError('Preview decoder produced incorrect dimensions')
        library = WallpaperLibrary(root / 'data', root / 'cache', removal_notice=lambda _: None)
        library.select_source('local')
        entry = library.import_local(root / 'image.webp')
        converted = prepare_image(entry.wallpaper.path)
        if not converted.is_file():
            raise RuntimeError('WebP desktop conversion failed')
        with exclusive_lock(root / 'probe.lock'):
            try:
                with exclusive_lock(root / 'probe.lock'):
                    raise RuntimeError('Concurrent lock was incorrectly allowed')
            except BlockingIOError:
                pass
    window.destroy()
    print(json.dumps({'status': 'ok', 'checks': ['GTK', 'JPEG', 'PNG', 'WebP', 'TLS trust',
                                               'SQLite imports', 'Windows conversion', 'file locking']}))
