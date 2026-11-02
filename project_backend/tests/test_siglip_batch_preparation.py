import base64
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw
from app.business import services
from app.business.image_verification_category_service import ImageVerificationCategory
from app.core import model_runtime_client


def category(value, enabled=True):
    return ImageVerificationCategory(value=value, label=value.upper(), prompt='prompt', match_threshold=.5,
                                     margin_threshold=.05, evidence_temperature=1, enabled=enabled)


def field(field_id, roi, cat='portrait', page=1):
    return {'id': field_id, 'data_type': 'image', 'page_number': page, 'image_category': cat,
            'roi': roi, 'roi_padding': 6}


class SiglipBatchPreparationTests(unittest.TestCase):
    def test_reuses_page_and_category_snapshot_preserves_crops_and_admin_preview(self):
        image = Image.new('RGB', (80, 60), (30, 60, 90))
        roi1 = {'x_ratio': .25, 'y_ratio': .25, 'width_ratio': .25, 'height_ratio': .25}
        roi2 = {'x_ratio': .5, 'y_ratio': .3, 'width_ratio': .25, 'height_ratio': .3,
                'points': [{'x_ratio': .5, 'y_ratio': .3}, {'x_ratio': .75, 'y_ratio': .3},
                           {'x_ratio': .5, 'y_ratio': .6}]}
        fields = [field('one', roi1), field('two', roi2)]
        original = SimpleNamespace(passed=True, evidence_score=1)
        observed = []
        def verify(crops, targets, categories, timing):
            observed.extend(crops)
            self.assertEqual(targets, [['portrait'], ['portrait']])
            self.assertEqual([item['value'] for item in categories], ['portrait'])
            return [[original], [original]]
        svc = services.VerificationService()
        with patch.object(services, 'list_image_verification_categories', return_value=[category('portrait'), category('signature', False)]) as listing, \
             patch.object(services, 'get_image_verification_category') as individual, \
             patch.object(services, '_load_image_source', return_value=image) as load, \
             patch.object(services, '_crop_anchor_roi_to_temp') as temp, \
             patch.object(services, 'verify_image_categories_batch', side_effect=verify) as invoke, \
             patch.object(services, 'include_detection_diagnostics', return_value=True), \
             patch.object(svc, '_image_match_from_siglip_result', side_effect=lambda result, preview: {'passed': result.passed, 'preview': preview}):
            matches, debug = svc.score_image_anchors_batch(fields, {1: 'page.png'})
        listing.assert_called_once_with(enabled_only=False)
        individual.assert_not_called()
        load.assert_called_once_with('page.png')
        temp.assert_not_called()
        invoke.assert_called_once()
        self.assertEqual(debug['image_roi_count'], 2)
        self.assertEqual(list(matches), ['one', 'two'])
        self.assertEqual(matches['one']['preview'], observed[0])
        first_expected = image.crop((14, 9, 46, 36))
        second_box = (34, 12, 66, 42)
        second_crop = image.crop(second_box)
        mask = Image.new('L', second_crop.size, 0)
        ImageDraw.Draw(mask).polygon([(6, 6), (26, 6), (6, 24)], fill=255)
        second_expected = Image.new('RGB', second_crop.size, 'white')
        second_expected.paste(second_crop, mask=mask)
        for index, expected in enumerate((first_expected, second_expected)):
            data = base64.b64decode(observed[index].split(',', 1)[1])
            actual = Image.open(io.BytesIO(data)).convert('RGB')
            self.assertEqual(actual.size, expected.size)
            self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_disabled_or_missing_category_does_not_run_model(self):
        roi = {'x_ratio': 0, 'y_ratio': 0, 'width_ratio': .5, 'height_ratio': .5}
        with patch.object(services, 'list_image_verification_categories', return_value=[category('portrait', False)]), \
             patch.object(services, 'verify_image_categories_batch') as invoke:
            matches, debug = services.VerificationService().score_image_anchors_batch(
                [field('off', roi), field('missing', roi, 'unknown')], {1: 'page.png'})
        invoke.assert_not_called()
        self.assertEqual(matches['off']['failure_reason'], 'category_disabled')
        self.assertEqual(matches['missing']['failure_reason'], 'category_not_found')
        self.assertEqual(debug['image_roi_count'], 0)

    def test_user_preview_is_not_returned(self):
        roi = {'x_ratio': 0, 'y_ratio': 0, 'width_ratio': .5, 'height_ratio': .5}
        result = SimpleNamespace(passed=True, evidence_score=1)
        svc = services.VerificationService()
        with patch.object(services, 'list_image_verification_categories', return_value=[category('portrait')]), \
             patch.object(services, '_load_image_source', return_value=Image.new('RGB', (20, 20))), \
             patch.object(services, 'verify_image_categories_batch', return_value=[[result]]), \
             patch.object(services, 'include_detection_diagnostics', return_value=False), \
             patch.object(svc, '_image_match_from_siglip_result', side_effect=lambda r, preview: {'preview': preview}):
            matches, _ = svc.score_image_anchors_batch([field('one', roi)], {1: 'page.png'})
        self.assertIsNone(matches['one']['preview'])

    def test_no_image_anchors_does_not_load_categories_or_call_model(self):
        with patch.object(services, 'list_image_verification_categories') as listing, \
             patch.object(services, 'verify_image_categories_batch') as invoke:
            matches, debug = services.VerificationService().score_image_anchors_batch(
                [{'id': 'text', 'data_type': 'text'}], {})
        listing.assert_not_called()
        invoke.assert_not_called()
        self.assertEqual(matches, {})
        self.assertEqual(debug['image_roi_count'], 0)

    def test_runtime_client_accepts_existing_paths_and_memory_png(self):
        data_url = 'data:image/png;base64,AA=='
        with patch.object(model_runtime_client, 'is_runtime_configured', return_value=True), \
             patch.object(model_runtime_client, '_path_to_data_url', return_value='path-encoded') as path, \
             patch.object(model_runtime_client, '_post_predict', return_value={}) as post:
            model_runtime_client.remote_verify_image_logits_batch([data_url, 'legacy.png'])
        path.assert_called_once_with('legacy.png')
        self.assertEqual(post.call_args.args[1]['images'], [data_url, 'path-encoded'])


if __name__ == '__main__':
    unittest.main()
