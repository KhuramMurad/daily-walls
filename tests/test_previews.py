"""Preview decoding must not block GTK or deliver into closed windows."""
import threading
import time
import unittest
from unittest.mock import patch
from gi.repository import GLib
from app import PreviewLoader


class PreviewTests(unittest.TestCase):
    def pump_until(self, predicate):
        deadline = time.monotonic() + 3
        context = GLib.MainContext.default()
        while not predicate() and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_decode_is_background_coalesced_and_cached(self):
        loader = PreviewLoader()
        release = threading.Event()
        started = threading.Event()
        results = []
        worker_ids = []
        image = object()
        def decode(*args):
            worker_ids.append(threading.get_ident())
            started.set()
            release.wait(2)
            return image
        try:
            with patch('app.GdkPixbuf.Pixbuf.new_from_file_at_scale', side_effect=decode) as mock:
                loader.load('large.jpg', 280, 160, lambda *args: results.append(args))
                self.assertTrue(started.wait(1))
                loader.load('large.jpg', 280, 160, lambda *args: results.append(args))
                heartbeat = []
                GLib.idle_add(lambda: heartbeat.append(True) and False)
                self.pump_until(lambda: bool(heartbeat))
                self.assertFalse(results)
                self.assertNotEqual(worker_ids[0], threading.get_ident())
                release.set()
                self.pump_until(lambda: len(results) == 2)
                loader.load('large.jpg', 280, 160, lambda *args: results.append(args))
                self.assertEqual(len(results), 3)
                self.assertEqual(mock.call_count, 1)
        finally:
            release.set()
            loader.close()

    def test_close_discards_pending_results(self):
        loader = PreviewLoader()
        received = []
        key = ('image.jpg', 280, 160)
        loader.pending[key] = [lambda *args: received.append(args)]
        loader.close()
        loader._deliver(key, object(), None)
        self.assertFalse(received)
        self.assertFalse(loader.cache)
