"""GTK interface for the persistent seven-wallpaper queue."""
from __future__ import annotations

from datetime import datetime, timezone
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import threading
from typing import Callable, TypeVar

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from library import QUEUE_SIZE, QueueEntry, Snapshot, WallpaperLibrary
from layout import image_placement
from notifier import notify_wallpaper
from platform_support import is_windows

APPLICATION_ID = "io.github.KhuramMurad.daily-walls"

Result = TypeVar("Result")
CSS = b"""
window, window.background, dialog.background { background: #11191d; color: #e9f0ef; }
headerbar { background: #172328; color: #e9f0ef; border: none; }
.hero { font-size: 27px; font-weight: 700; color: #f5f9f8; }
.eyebrow { color: #75c9b3; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
.muted { color: #a3b5b9; }
.photo-title { font-size: 16px; font-weight: 600; }
.preview { background: #0b1114; }
button { background: #24363d; color: #eef5f3; border: 1px solid #3a5159;
         border-radius: 8px; padding: 9px 15px; text-shadow: none; box-shadow: none; }
button:hover { background: #314951; }
button.suggested-action { background: #83d3b8; color: #10251f; border-color: #83d3b8; font-weight: 700; }
button:disabled { opacity: 0.45; }
button.thumbnail { padding: 4px; border: 2px solid #24363d; }
button.selected { border-color: #83d3b8; }
.status { color: #b4c9ce; }
.error { color: #ffb5a7; }
.notice { color: #e9c790; }
checkbutton { color: #b4c9ce; }
dialog, dialog box { color: #e9f0ef; }
.dialog-vbox { background: #11191d; }
"""


def label(text: str, style: str = "", wrap: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0)
    if style:
        widget.get_style_context().add_class(style)
    widget.set_line_wrap(wrap)
    widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return widget


class PreviewLoader:
    """Decode off the GTK thread, with bounded memory and reused scaled images."""
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview")
        self.cache = OrderedDict()
        self.pending = {}
        self.closed = False

    def load(self, path, width, height, callback):
        key = (str(path), width, height)
        if key in self.cache:
            self.cache.move_to_end(key)
            callback(self.cache[key], None)
            return
        if key in self.pending:
            self.pending[key].append(callback)
            return
        self.pending[key] = [callback]

        def decode():
            try:
                result = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), width, height, True)
                error = None
            except Exception as exc:
                result, error = None, str(exc)
            GLib.idle_add(self._deliver, key, result, error)
        self.executor.submit(decode)

    def _deliver(self, key, result, error):
        callbacks = self.pending.pop(key, [])
        if self.closed:
            return False
        if result is not None:
            self.cache[key] = result
            while len(self.cache) > 21:
                self.cache.popitem(last=False)
        for callback in callbacks:
            callback(result, error)
        return False

    def close(self):
        self.closed = True
        self.pending.clear()
        self.cache.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)


class ImagePreview(Gtk.DrawingArea):
    """Responsive preview: fit by default, optional cropped fill."""
    def __init__(self, *, thumbnail: bool = False) -> None:
        super().__init__()
        self.fill = False
        self.pixbuf: GdkPixbuf.Pixbuf | None = None
        self.set_size_request(48 if thumbnail else 200, 58 if thumbnail else 180)
        self.set_hexpand(True)
        self.set_vexpand(not thumbnail)
        self.connect("draw", self._draw)

    def set_image(self, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        self.pixbuf = pixbuf
        self.queue_draw()

    def _draw(self, _widget: Gtk.Widget, context: object) -> bool:
        width, height = self.get_allocated_width(), self.get_allocated_height()
        context.set_source_rgb(0.04, 0.07, 0.08)
        context.paint()
        if self.pixbuf is not None:
            scale, x, y = image_placement(self.pixbuf.get_width(), self.pixbuf.get_height(),
                                          width, height, fill=self.fill)
            context.save()
            context.rectangle(0, 0, width, height)
            context.clip()
            context.translate(x, y)
            context.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(context, self.pixbuf, 0, 0)
            context.paint()
            context.restore()
        return False


class WallpaperWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, *, featured: bool = False,
                 force: bool = False, library: WallpaperLibrary | None = None) -> None:
        super().__init__(application=application, title="Daily Walls")
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        display = Gdk.Display.get_default()
        if display is None:
            raise RuntimeError("No graphical desktop session is available")
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        workarea = monitor.get_workarea()
        self.set_default_size(min(1160, workarea.width - 80), min(820, workarea.height - 100))
        self.set_position(Gtk.WindowPosition.CENTER)
        self.library = library or WallpaperLibrary()
        self.entries: tuple[QueueEntry, ...] = ()
        self.selected_id: str | None = None
        self.busy = False
        self.closed = False
        self.preview_loader = PreviewLoader()
        self.thumbnail_generation = 0
        self.preview_generation = 0
        self.thumbnails: list[tuple[str, Gtk.Button]] = []
        self.connect("destroy", self._closed)
        header = Gtk.HeaderBar(title="Daily Walls", show_close_button=True)
        header.set_subtitle("Seven fresh views, ready for your desktop")
        self.set_titlebar(header)
        saved = Gtk.Button(label="Saved wallpapers")
        saved.set_tooltip_text("Browse all downloaded wallpapers, including replaced images")
        saved.connect("clicked", self._saved_wallpapers)
        header.pack_end(saved)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(16)
        self.add(root)
        heading = Gtk.Box(spacing=12)
        heading_label = label("4K+ · FEATURED & QUALITY SCENERY", "eyebrow")
        self.heading_label = heading_label
        self.source_picker = Gtk.ComboBoxText()
        self.source_picker.append("commons", "Commons · Premium scenery")
        self.source_picker.append("bing", "Bing · Daily wallpapers")
        self.source_picker.append("firefox", "Firefox · Picture of the day")
        self.source_picker.append("local", "Locally saved wallpapers")
        self.source_picker.set_active_id(self.library.source)
        self.source_picker.connect("changed", self._source_changed)
        heading.pack_start(self.source_picker, False, False, 0)
        heading_label.set_ellipsize(Pango.EllipsizeMode.END)
        heading.pack_start(heading_label, True, True, 0)
        root.pack_start(heading, False, False, 0)
        viewer = Gtk.Box(spacing=16)
        viewer.set_vexpand(True)
        self.preview = ImagePreview()
        viewer.pack_start(self.preview, True, True, 0)
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_size_request(230, -1)
        sidebar_scroll.set_hexpand(False)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar.set_margin_end(4)
        sidebar_scroll.add(sidebar)
        viewer.pack_start(sidebar_scroll, False, False, 0)
        self.theme_label = label("YOUR NEXT VIEW", "eyebrow")
        sidebar.pack_start(self.theme_label, False, False, 0)
        self.title_label = label("Preparing your wallpaper queue…", "photo-title", True)
        self.title_label.set_max_width_chars(24)
        self.title_label.set_lines(4)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        sidebar.pack_start(self.title_label, False, False, 0)
        self.credit = label("", "muted", True)
        self.credit.set_max_width_chars(25)
        self.credit.set_lines(2)
        self.credit.set_ellipsize(Pango.EllipsizeMode.END)
        sidebar.pack_start(self.credit, False, False, 0)
        self.details = label("At least 3840 × 2160", "muted", True)
        self.details.set_max_width_chars(25)
        sidebar.pack_start(self.details, False, False, 0)
        self.replace_image = Gtk.Button(label="Find another")
        self.replace_image.set_tooltip_text("Download an alternative, compare both images, and choose which to keep in the queue.")
        self.replace_image.connect("clicked", self._replace)
        sidebar.pack_start(self.replace_image, False, False, 0)
        self.local_actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.import_images = Gtk.Button(label="Import images…")
        self.import_images.connect("clicked", lambda _: self._import_local())
        self.local_actions.pack_start(self.import_images, False, False, 0)
        self.local_actions.set_no_show_all(True)
        self.import_images.show()
        sidebar.pack_start(self.local_actions, False, False, 0)
        sidebar.pack_start(Gtk.Separator(), False, False, 0)
        self.fit_mode = Gtk.ComboBoxText()
        self.fit_mode.append("fit", "Fit entire image")
        self.fit_mode.append("fill", "Fill frame (crop edges)")
        self.fit_mode.set_active_id("fit")
        self.fit_mode.set_tooltip_text("Preview sizing: Fit shows the whole image; Fill crops edges without stretching.")
        self.fit_mode.connect("changed", self._fit_changed)
        heading.pack_end(self.fit_mode, False, False, 0)
        self.source = Gtk.LinkButton.new_with_label("https://commons.wikimedia.org", "View on Commons ↗")
        sidebar.pack_start(self.source, False, False, 0)
        self.notifications = Gtk.CheckButton(label="Notify on change")
        self.notifications.set_active(True)
        sidebar.pack_start(self.notifications, False, False, 0)
        root.pack_start(viewer, True, True, 0)
        self.queue_label = label("YOUR QUEUE · 0 / 7 READY", "eyebrow")
        root.pack_start(self.queue_label, False, False, 2)
        self.strip = Gtk.Box(spacing=6, homogeneous=True)
        self.strip.set_vexpand(False)
        root.pack_start(self.strip, False, False, 0)
        actions = Gtk.Box(spacing=8)
        self.previous = Gtk.Button(label="← Previous")
        self.next = Gtk.Button(label="Next →")
        self.refresh = Gtk.Button(label="Refill queue")
        self.apply = Gtk.Button(label="Set as wallpaper")
        self.apply.get_style_context().add_class("suggested-action")
        self.previous.connect("clicked", lambda _: self._navigate(-1))
        self.next.connect("clicked", lambda _: self._navigate(1))
        self.refresh.connect("clicked", lambda _: self.refresh_queue())
        self.apply.connect("clicked", self._apply)
        for button in (self.previous, self.next, self.refresh):
            actions.pack_start(button, False, False, 0)
        actions.pack_end(self.apply, False, False, 0)
        root.pack_start(actions, False, False, 2)
        bottom = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner()
        bottom.pack_start(self.spinner, False, False, 0)
        self.status = label("Ready", "status")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        bottom.pack_start(self.status, True, True, 0)
        root.pack_start(bottom, False, False, 0)
        self.expiry = label("Saved for 10 days · Your current wallpaper is kept until you change it.", "muted")
        self.expiry.set_ellipsize(Pango.EllipsizeMode.END)
        root.pack_start(self.expiry, False, False, 0)
        self.notice = label("", "notice")
        self.notice.set_ellipsize(Pango.EllipsizeMode.END)
        root.pack_start(self.notice, False, False, 0)
        self._show_snapshot(self.library.snapshot())
        self.show_all()
        GLib.idle_add(self.refresh_queue)
        self.maintenance_timer = GLib.timeout_add_seconds(300, self._periodic)

    def _closed(self, *_args: object) -> None:
        self.closed = True
        self.preview_loader.close()
        if hasattr(self, "maintenance_timer"):
            GLib.source_remove(self.maintenance_timer)

    def _periodic(self) -> bool:
        if self.closed:
            return False
        if not self.busy:
            self.refresh_queue()
        return True

    def _selected(self) -> QueueEntry | None:
        return next((entry for entry in self.entries if entry.id == self.selected_id), None)

    def _controls(self) -> None:
        self.previous.set_sensitive(len(self.entries) > 1)
        self.next.set_sensitive(len(self.entries) > 1)
        self.source_picker.set_sensitive(not self.busy)
        self.import_images.set_sensitive(not self.busy)
        self.refresh.set_sensitive(not self.busy)
        self.apply.set_sensitive(not self.busy and self._selected() is not None)
        self.replace_image.set_sensitive(not self.busy and self._selected() is not None)
        self.source.set_sensitive(self._selected() is not None)

    def _busy(self, busy: bool, message: str, error: bool = False) -> None:
        self.busy = busy
        self._controls()
        self.status.set_text(message)
        context = self.status.get_style_context()
        context.remove_class("error")
        if error:
            context.add_class("error")
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _show_snapshot(self, snapshot: Snapshot) -> None:
        local = self.library.source == "local"
        self.local_actions.set_visible(local)
        self.replace_image.set_label("Compare imported image…" if local else "Find another")
        self.replace_image.set_tooltip_text("Import an alternative and compare it before changing your queue." if local else
                                          "Download an alternative and compare it before changing your queue.")
        self.refresh.set_label("Refresh library" if local else "Refill queue")
        if local:
            heading = "HD+ · LOCALLY SAVED LANDSCAPES"
        elif self.library.source == "bing":
            heading = "4K · BING DAILY WALLPAPERS"
        elif self.library.source == "firefox":
            heading = "HD+ · FIREFOX PICTURE OF THE DAY"
        else:
            heading = "4K+ · FEATURED & QUALITY SCENERY"
        self.heading_label.set_text(heading)
        old_ids = [entry.id for entry in self.entries]
        self.entries = snapshot.queue
        self.notice.set_text(snapshot.notice)
        self.queue_label.set_text(f"YOUR QUEUE · {len(self.entries)} / {QUEUE_SIZE} READY")
        if old_ids != [entry.id for entry in self.entries] or not self.thumbnails:
            self.thumbnail_generation += 1
            generation = self.thumbnail_generation
            for child in self.strip.get_children():
                self.strip.remove(child)
            self.thumbnails.clear()
            for position in range(QUEUE_SIZE):
                button = Gtk.Button()
                button.get_style_context().add_class("thumbnail")
                if position < len(self.entries):
                    entry = self.entries[position]
                    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                    thumbnail = ImagePreview(thumbnail=True)
                    box.pack_start(thumbnail, False, False, 0)
                    def show_thumbnail(pixbuf, error, widget=thumbnail, token=generation):
                        if not self.closed and token == self.thumbnail_generation:
                            widget.set_image(pixbuf)
                            if error:
                                widget.set_tooltip_text(error)
                    self.preview_loader.load(entry.wallpaper.path, 280, 160, show_thumbnail)
                    caption = Gtk.Label(label=self._theme_text(entry.wallpaper.theme) or str(position + 1))
                    caption.set_ellipsize(Pango.EllipsizeMode.END)
                    caption.set_max_width_chars(12)
                    box.pack_start(caption, False, False, 0)
                    button.add(box)
                    button.set_tooltip_text(entry.wallpaper.title)
                    button.connect("clicked", lambda _, image_id=entry.id: self._select(image_id))
                    self.thumbnails.append((entry.id, button))
                else:
                    button.set_label(f"{position + 1}\nWaiting")
                    button.set_sensitive(False)
                self.strip.pack_start(button, True, True, 0)
            self.strip.show_all()
        if self._selected() is None:
            self.selected_id = self.entries[0].id if self.entries else None
        if self.selected_id is not None:
            self._select(self.selected_id)
        else:
            self.preview_generation += 1
            self.preview.set_image(None)
            self.theme_label.set_text("YOUR NEXT VIEW")
            self.title_label.set_text("No wallpapers ready yet")
            self.credit.set_lines(3)
            self.credit.set_text("Import landscape images from any folder on your computer." if local else
                                 "Use Refill queue to download seven unique wallpapers.")
            self.details.set_text("Landscape images · At least 1920 × 1080" if local else "")
        self.source.set_visible(bool(self.entries) and not local)
        self._controls()

    def _select(self, image_id: str) -> None:
        self.selected_id = image_id
        entry = self._selected()
        if entry is None:
            return
        wallpaper = entry.wallpaper
        self.preview_generation += 1
        generation = self.preview_generation
        self.preview.set_image(None)
        def show_preview(pixbuf, error):
            if self.closed or generation != self.preview_generation:
                return
            self.preview.set_image(pixbuf)
            if error:
                self.status.set_text(f"Could not preview this image: {error}")
        self.preview_loader.load(wallpaper.path, 1920, 1200, show_preview)
        self.theme_label.set_text(self._theme_text(wallpaper.theme).upper())
        self.title_label.set_text(wallpaper.title)
        self.title_label.set_tooltip_text(wallpaper.title)
        self.credit.set_text(wallpaper.photographer)
        self.credit.set_lines(2)
        self.credit.set_tooltip_text(wallpaper.attribution or wallpaper.photographer)
        expires = datetime.fromtimestamp(entry.expires_at, timezone.utc).astimezone().strftime("%d %b, %H:%M")
        self.details.set_text(f"{wallpaper.width:,} × {wallpaper.height:,}\n{wallpaper.quality.title()} image · {wallpaper.license}\nSaved until {expires}")
        if wallpaper.source == "local":
            source_label = "Local image"
        elif wallpaper.source == "bing":
            source_label = "View on Bing ↗"
        elif wallpaper.source == "firefox":
            source_label = "View on Commons ↗" if "commons" in (wallpaper.description_url or "") else "View on Firefox ↗"
        else:
            source_label = "View on Commons ↗"
        self.source.set_label(source_label)
        self.source.set_uri(wallpaper.description_url or "https://commons.wikimedia.org")
        for selected_id, button in self.thumbnails:
            context = button.get_style_context()
            context.remove_class("selected")
            if selected_id == image_id:
                context.add_class("selected")
        self._controls()

    @staticmethod
    def _theme_text(theme: str) -> str:
        if len(theme) == 8 and theme.isdigit():
            try:
                return datetime.strptime(theme, "%Y%m%d").strftime("%d %b %Y")
            except ValueError:
                pass
        return theme

    def _source_changed(self, combo: Gtk.ComboBoxText) -> None:
        source = combo.get_active_id()
        if source is None or self.busy:
            return
        try:
            self.library.select_source(source)
        except Exception as exc:
            self._busy(False, str(exc), True)
            return
        self.selected_id = None
        self._show_snapshot(self.library.snapshot())
        self.refresh_queue()

    def _fit_changed(self, combo: Gtk.ComboBoxText) -> None:
        self.preview.fill = combo.get_active_id() == "fill"
        self.preview.queue_draw()

    def _navigate(self, offset: int) -> None:
        if not self.entries:
            return
        index = next((i for i, entry in enumerate(self.entries) if entry.id == self.selected_id), 0)
        self._select(self.entries[(index + offset) % len(self.entries)].id)

    def _work(self, operation: Callable[[], Result], complete: Callable[[Result], None]) -> None:
        def deliver(result: Result | None, error: str | None) -> bool:
            if self.closed:
                return False
            try:
                if error:
                    self._show_snapshot(self.library.snapshot())
                    self._busy(False, error, True)
                elif result is not None:
                    complete(result)
            except Exception as exc:
                logging.exception("Could not display queue")
                self._busy(False, str(exc), True)
            return False

        def worker() -> None:
            try:
                result = operation()
            except Exception as exc:
                logging.exception("Wallpaper operation failed")
                GLib.idle_add(deliver, None, str(exc))
            else:
                GLib.idle_add(deliver, result, None)
        threading.Thread(target=worker, daemon=True, name="wallpaper-worker").start()

    def _progress(self, message: str) -> bool:
        if not self.closed and self.busy:
            self.status.set_text(message)
            if message.endswith("wallpapers saved"):
                self._show_snapshot(self.library.snapshot())
        return False

    def refresh_queue(self) -> bool:
        if self.busy or self.closed:
            return False
        self._busy(True, "Checking saved wallpapers and filling the queue…")
        self._work(lambda: self.library.fill(progress=lambda message: GLib.idle_add(self._progress, message)),
                   self._ready)
        return False

    def _ready(self, snapshot: Snapshot) -> None:
        self._show_snapshot(snapshot)
        message = (f"{len(snapshot.queue)}/7 imported wallpapers ready. Import landscape images from your computer."
                   if self.library.source == "local" else
                   f"{len(snapshot.queue)}/7 wallpapers ready. Select a thumbnail to preview.")
        self._busy(False, snapshot.warning or message,
                   bool(snapshot.warning))

    def _apply(self, _button: Gtk.Button) -> None:
        entry = self._selected()
        if self.busy or entry is None:
            return
        notify = self.notifications.get_active()
        self._busy(True, "Applying wallpaper, then refilling its queue slot…")

        def operation() -> Snapshot:
            wallpaper = self.library.apply(entry.id)
            if notify:
                notify_wallpaper(wallpaper)
            return self.library.fill(progress=lambda message: GLib.idle_add(self._progress, message))

        def complete(snapshot: Snapshot) -> None:
            self._ready(snapshot)
            if not snapshot.warning:
                self.status.set_text(f"Wallpaper applied. {len(snapshot.queue)}/7 unused wallpapers ready."
                                     + (" Import more local images to refill." if self.library.source == "local" else ""))
        self._work(operation, complete)

    def _replace(self, _button: Gtk.Button) -> None:
        entry = self._selected()
        if self.busy or entry is None:
            return
        if self.library.source == "local":
            self._import_local(original=entry)
            return
        self._busy(True, f"Finding another {entry.wallpaper.theme.lower()} image…")

        self._work(lambda: self.library.prepare_replacement(entry.id),
                   lambda candidate: self._compare(entry, candidate))

    def _import_local(self, original: QueueEntry | None = None) -> None:
        if self.busy:
            return
        chooser = Gtk.FileChooserDialog(title="Import landscape images" if original is None else "Choose an alternative image",
                                        transient_for=self, action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Import", Gtk.ResponseType.ACCEPT)
        chooser.set_select_multiple(original is None)
        image_filter = Gtk.FileFilter()
        image_filter.set_name("Images (JPEG, PNG, WebP)")
        for mime in ("image/jpeg", "image/png", "image/webp"):
            image_filter.add_mime_type(mime)
        chooser.add_filter(image_filter)
        downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if downloads:
            chooser.set_current_folder(downloads)
        response = chooser.run()
        paths = [Path(name) for name in chooser.get_filenames()] if response == Gtk.ResponseType.ACCEPT else []
        chooser.destroy()
        if not paths:
            return
        self._busy(True, "Validating and importing landscape images…")
        if original is not None:
            self._work(lambda: self.library.import_local(paths[0], original=original.id),
                       lambda candidate: self._compare(original, candidate))
            return
        def operation():
            errors = []
            count = 0
            for path in paths:
                try:
                    self.library.import_local(path)
                    count += 1
                except Exception as exc:
                    errors.append(f"{path.name}: {exc}")
            return self.library.snapshot(), count, errors
        def complete(result):
            snapshot, count, errors = result
            self._ready(snapshot)
            self.status.set_text(f"Imported {count} image(s)." + (" Some files could not be imported." if errors else ""))
            if errors:
                dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                           buttons=Gtk.ButtonsType.CLOSE, text="Some images could not be imported")
                dialog.format_secondary_text("\n".join(errors))
                dialog.connect("response", lambda widget, _: widget.destroy())
                dialog.show_all()
        self._work(operation, complete)

    def _open_local(self, path) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(path.as_uri(), self.get_display().get_app_launch_context())
        except GLib.Error as exc:
            self.status.set_text(f"Could not open saved wallpapers: {exc.message}")

    def _saved_wallpapers(self, _button) -> None:
        dialog = Gtk.Dialog(title="Saved wallpapers", transient_for=self, modal=True)
        dialog.set_default_size(850, 580)
        dialog.add_button("Open folder", Gtk.ResponseType.APPLY)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        content = dialog.get_content_area()
        content.set_border_width(16)
        content.set_spacing(12)
        entries = self.library.saved_images()
        content.pack_start(label(f"{len(entries)} saved images · Includes used and replaced wallpapers", "photo-title"), False, False, 0)
        content.pack_start(label("Files expire 10 days after download. Your current wallpaper stays protected.", "muted", True), False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid = Gtk.FlowBox()
        grid.set_valign(Gtk.Align.START)
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_max_children_per_line(3)
        grid.set_min_children_per_line(1)
        grid.set_row_spacing(12)
        grid.set_column_spacing(12)
        scroll.add(grid)
        content.pack_start(scroll, True, True, 0)
        alive = [True]
        dialog.connect("destroy", lambda *_: alive.__setitem__(0, False))
        for entry in entries:
            button = Gtk.Button()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            preview = ImagePreview(thumbnail=True)
            preview.set_size_request(200, 120)
            box.pack_start(preview, False, False, 0)
            caption = label(entry.wallpaper.title)
            caption.set_max_width_chars(26)
            caption.set_ellipsize(Pango.EllipsizeMode.END)
            box.pack_start(caption, False, False, 0)
            expiry = datetime.fromtimestamp(entry.expires_at).strftime("%d %b, %H:%M")
            box.pack_start(label(f"{entry.wallpaper.source.title()} · Expires {expiry}", "muted"), False, False, 0)
            button.add(box)
            button.set_tooltip_text(entry.wallpaper.title + "\nOpen full image")
            button.connect("clicked", lambda _, path=entry.wallpaper.path: self._open_local(path))
            grid.add(button)
            def decoded(pixbuf, error, widget=preview):
                if alive[0] and not self.closed:
                    widget.set_image(pixbuf)
                    if error:
                        widget.set_tooltip_text(error)
            self.preview_loader.load(entry.wallpaper.path, 400, 240, decoded)
        if not entries:
            grid.add(label("Your downloaded wallpapers will appear here.", "muted"))
        def response(widget, response_id):
            if response_id == Gtk.ResponseType.APPLY:
                self._open_local(self.library.images_dir)
            else:
                widget.destroy()
        dialog.connect("response", response)
        dialog.show_all()

    def _compare(self, original: QueueEntry, candidate: QueueEntry) -> None:
        dialog = Gtk.Dialog(title="Choose your wallpaper", transient_for=self, modal=True)
        dialog.set_default_size(960, 550)
        dialog.add_button("Keep selected", Gtk.ResponseType.CANCEL)
        keep_new = dialog.add_button("Use new in queue", Gtk.ResponseType.ACCEPT)
        keep_new.get_style_context().add_class("suggested-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        content = dialog.get_content_area()
        content.set_border_width(16)
        content.set_spacing(12)
        content.pack_start(label("Choose which image stays in your queue", "photo-title"), False, False, 0)
        content.pack_start(label("Both remain in Saved wallpapers until their original expiry. Your desktop stays unchanged.", "muted", True), False, False, 0)
        columns = Gtk.Box(spacing=16, homogeneous=True)
        content.pack_start(columns, True, True, 0)
        alive = [True]
        dialog.connect("destroy", lambda *_: alive.__setitem__(0, False))
        for title, entry in (("SELECTED", original), ("NEW ALTERNATIVE", candidate)):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.pack_start(label(title, "eyebrow"), False, False, 0)
            preview = ImagePreview()
            box.pack_start(preview, True, True, 0)
            caption = label(entry.wallpaper.title, "photo-title", True)
            caption.set_max_width_chars(36)
            caption.set_lines(2)
            caption.set_ellipsize(Pango.EllipsizeMode.END)
            box.pack_start(caption, False, False, 0)
            credit = label(entry.wallpaper.photographer, "muted", True)
            credit.set_max_width_chars(36)
            credit.set_lines(2)
            credit.set_ellipsize(Pango.EllipsizeMode.END)
            box.pack_start(credit, False, False, 0)
            columns.pack_start(box, True, True, 0)
            def decoded(pixbuf, error, widget=preview):
                if alive[0] and not self.closed:
                    widget.set_image(pixbuf)
                    if error:
                        widget.set_tooltip_text(error)
            self.preview_loader.load(entry.wallpaper.path, 1200, 800, decoded)
        def response(widget, response_id):
            widget.destroy()
            keep = response_id == Gtk.ResponseType.ACCEPT
            def operation():
                self.library.choose_replacement(original.id, candidate.id, keep_new=keep)
                return self.library.snapshot()
            def complete(snapshot):
                self.selected_id = candidate.id if keep else original.id
                self._ready(snapshot)
                self.status.set_text("Choice saved. Both images remain in Saved wallpapers until expiry.")
            self._work(operation, complete)
        dialog.connect("response", response)
        self.status.set_text("Compare the two images and choose which to keep.")
        dialog.show_all()


class WallpaperApplication(Gtk.Application):
    def __init__(self, *, featured: bool = False, force: bool = False) -> None:
        GLib.set_application_name("Daily Walls")
        Gdk.set_program_class(APPLICATION_ID)
        if is_windows():
            from windows.desktop import set_application_identity
            import sys
            set_application_identity()
            base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
            Gtk.Window.set_default_icon_from_file(str(base / 'windows' / 'assets' / 'daily-walls.ico'))
        else:
            Gtk.Window.set_default_icon_name("daily-walls")
        super().__init__(application_id=APPLICATION_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            window = WallpaperWindow(self)
        window.present()
