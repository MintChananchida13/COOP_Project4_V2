import unittest
from app.main import siglip_result
from app.siglip_policy import POSITIVE, NEGATIVE


class NegativeToleranceApiTests(unittest.TestCase):
    def test_api_accepts_tolerance_boundary_and_rejects_above(self):
        categories = [{'value': name, 'prompt': 'test'} for name in (*POSITIVE, *NEGATIVE)]
        for negative, expected in ((0.8, 'portrait'), (0.8001, 'other')):
            logits = [-30.0] * len(categories)
            logits[POSITIVE.index('portrait')] = 0.0
            logits[len(POSITIVE) + NEGATIVE.index('background')] = negative
            result = siglip_result(logits, categories, None)
            self.assertEqual(result['decision']['final_predicted_category'], expected)
            self.assertEqual(result['decision']['negative_tolerance'], 0.8)
            self.assertEqual(result['logits'], logits)


if __name__ == '__main__':
    unittest.main()
