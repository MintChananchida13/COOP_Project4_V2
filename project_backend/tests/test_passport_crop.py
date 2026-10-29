import sys
import unittest
from unittest.mock import patch
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.processing.image_normalization import ImageNormalizationService


class PassportCropTests(unittest.TestCase):
    def setUp(self):
        self.service = ImageNormalizationService()

    def page(self, width, height):
        image = np.full((1400, 1000, 3), 255, dtype=np.uint8)
        x, y = (1000 - width) // 2, (1400 - height) // 2
        cv2.rectangle(image, (x, y), (x + width, y + height), (30, 30, 30), 4)
        return image, [x, y, x + width, y + height]

    def test_card_and_passport_boundaries(self):
        for width, height in [(510, 320), (500, 350), (350, 500)]:
            with self.subTest(size=(width, height)):
                image, expected = self.page(width, height)
                result = self.service._detect_pdf_physical_card_crop(image)
                self.assertTrue(result['passed'], result)
                for actual, target in zip(result['crop_box'], expected):
                    self.assertLess(abs(actual - target), 12)

    def test_blank_and_long_rectangle_are_rejected(self):
        blank = np.full((1400, 1000, 3), 255, dtype=np.uint8)
        long_rectangle, _ = self.page(700, 200)
        for image in (blank, long_rectangle):
            self.assertFalse(self.service._detect_pdf_physical_card_crop(image)['passed'])

    def test_open_passport_spread_is_rejected(self):
        for width, height in [(500, 460), (460, 500)]:
            with self.subTest(size=(width, height)):
                image, _ = self.page(width, height)
                self.assertFalse(self.service._detect_pdf_physical_card_crop(image)['passed'])
                with patch('cv2.imread', return_value=image):
                    self.assertFalse(self.service.detect_pdf_subdocument_boundary('spread.png')['passed'])

    def test_faint_card_edges(self):
        image = np.full((1400, 1000, 3), 255, dtype=np.uint8)
        cv2.rectangle(image, (245, 540), (755, 860), (235, 235, 235), 3)
        result = self.service._detect_pdf_physical_card_crop(image)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['debug']['edge_mode'], 'enhanced_faint_edges')
        for actual, expected in zip(result['crop_box'], [245, 540, 755, 860]):
            self.assertLess(abs(actual - expected), 12)

    def test_original_card_with_three_strong_sides_is_still_accepted(self):
        image, _ = self.page(510, 320)
        # Valid under the original rules: mean >= .66, three sides >= .55.
        # Its score is below .80 and must not be rejected by new guards.
        with patch.object(self.service, '_pdf_card_side_support', return_value=[0.56, 0.56, 1.0, 0.54]):
            result = self.service._detect_pdf_physical_card_crop(image)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['debug']['edge_mode'], 'standard')
        self.assertLess(result['score'], 0.8)

    def test_faint_pass_accepts_two_visible_sides(self):
        image, _ = self.page(510, 320)
        with patch.object(self.service, '_pdf_card_side_support', return_value=[0.8, 0.7, 0.2, 0.2]):
            result = self.service._detect_pdf_physical_card_crop(image)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['debug']['edge_mode'], 'enhanced_faint_edges')

    def test_background_noise_is_rejected(self):
        rng = np.random.default_rng(42)
        image = np.clip(245 + rng.normal(0, 2, (1400, 1000, 1)), 0, 255).astype(np.uint8)
        image = np.repeat(image, 3, axis=2)
        self.assertFalse(self.service._detect_pdf_physical_card_crop(image)['passed'])

    def test_public_v8_crop_writes_landscape_card_and_blank_falls_back(self):
        image, _ = self.page(510, 320)
        with patch('cv2.imread', return_value=image), patch('cv2.imwrite', return_value=True) as write:
            result = self.service.detect_pdf_subdocument_boundary('source.png', 'crop.png')
            self.assertTrue(result['passed'])
            cropped = write.call_args.args[1]
            self.assertAlmostEqual(cropped.shape[1] / cropped.shape[0], 510 / 320, delta=0.04)
        with patch('cv2.imread', return_value=np.full_like(image, 255)):
            self.assertFalse(self.service.detect_pdf_subdocument_boundary('blank.png')['passed'])



if __name__ == '__main__':
    unittest.main()
