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
        for width, height in [(510, 320), (500, 350), (350, 500), (500, 460), (460, 500)]:
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

    def test_public_crop_writes_passport_image_and_blank_falls_back(self):
        image, _ = self.page(350, 500)
        with patch('cv2.imread', return_value=image), patch('cv2.imwrite', return_value=True) as write:
            result = self.service.detect_pdf_subdocument_boundary('source.png', 'crop.png')
            self.assertTrue(result['passed'])
            cropped = write.call_args.args[1]
            self.assertAlmostEqual(cropped.shape[1] / cropped.shape[0], 0.7, delta=0.04)
        with patch('cv2.imread', return_value=np.full_like(image, 255)):
            self.assertFalse(self.service.detect_pdf_subdocument_boundary('blank.png')['passed'])



if __name__ == '__main__':
    unittest.main()
