import unittest
from layout import image_placement


class LayoutTests(unittest.TestCase):
    def test_fit_preserves_every_edge_at_different_window_sizes(self):
        for image in [(6000, 4000), (3840, 2160), (7680, 2160)]:
            for box in [(800, 500), (400, 600), (1280, 650)]:
                scale, x, y = image_placement(*image, *box)
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + image[0] * scale, box[0] + 0.001)
                self.assertLessEqual(y + image[1] * scale, box[1] + 0.001)
                self.assertTrue(abs(image[0] * scale - box[0]) < 0.001 or
                                abs(image[1] * scale - box[1]) < 0.001)

    def test_fill_is_explicit_and_covers_frame(self):
        scale, x, y = image_placement(6000, 4000, 800, 300, fill=True)
        self.assertLessEqual(x, 0)
        self.assertLess(y, 0)
        self.assertGreaterEqual(6000 * scale, 800)
        self.assertGreaterEqual(4000 * scale, 300)
