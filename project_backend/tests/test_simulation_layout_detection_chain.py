import base64
import io
import unittest
from unittest.mock import patch, Mock
from collections import OrderedDict
from PIL import Image
from app.business import services
from app.model_runtime import layout_analysis_service as layout
from app.core import model_runtime_client as client

class SimulationLayoutDetectionChainTests(unittest.TestCase):
    def test_real_simulation_to_http_request_uses_det_and_persists_combined_signature(self):
        buffer = io.BytesIO()
        Image.new("RGB", (100,100), "white").save(buffer,format="PNG")
        source = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        conn.execute.return_value.fetchall.return_value = [{"id":"page1", "page_number":1,
            "normalized_image_url":source,"sample_image_url":source}]
        raw = {"raw":{"layout":[{"boxes":[{"label":"image","coordinate":[5,5,35,70],"score":.9}]}],
            "text_detection":[{"dt_polys":[[[40,10],[90,10],[90,20],[40,20]]],"dt_scores":[.95]}]}}
        admin = services.AdminTemplateService()
        with patch.object(admin,"get_template",return_value={"id":"test", "pages":[{}]}), patch.object(services,"_connect",return_value=conn), patch.object(services,"_refresh_published_template_cache") as refresh, patch.object(layout,"_layout_raw_items_cache",OrderedDict()), patch.object(layout,"_require_runtime"), patch.object(client,"is_runtime_configured",return_value=True), patch.object(client,"_post_predict",return_value=raw) as http:
            result=admin.run_prepublish_simulation("test")
        self.assertFalse(http.call_args.args[1]["layout_only"])
        summary=result["layout_signature_pages"][0]
        self.assertEqual(summary["model"],"PP-DocLayoutV3+PP-OCRv6_medium_det")
        self.assertEqual(summary["region_count"],2)
        self.assertEqual(summary["region_sources"], {"layout": 1, "text_detection": 1})
        refresh.assert_called_once_with("test")
        conn.commit.assert_called_once()
        updates=[c for c in conn.execute.call_args_list if "UPDATE template_pages" in c.args[0]]
        self.assertEqual(len(updates),1)
        import json
        stored=json.loads(updates[0].args[1][0])
        self.assertEqual(stored["label_counts"]["text"],1)
        self.assertEqual(stored["label_counts"]["image"],1)

    def test_simulation_does_not_pass_failed_or_missing_pages(self):
        admin = services.AdminTemplateService()
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        for pages in ([], [{"status":"failed"}], [{"status":"generated","region_count":0}], [{"status":"generated","region_count":2},{"status":"failed"}]):
            with self.subTest(pages=pages), patch.object(admin,"get_template",return_value={"pages":[{}]}), patch.object(services,"_connect",return_value=conn), patch.object(services,"_refresh_template_layout_signatures",return_value=pages), patch.object(services,"_refresh_published_template_cache") as refresh:
                result = admin.run_prepublish_simulation("test")
                self.assertFalse(result["passed"])
                refresh.assert_called_once_with("test")

    def test_template_generation_uses_current_module_function_and_rejects_layout_only(self):
        from fastapi import HTTPException
        image = Image.new("RGB", (100,100), "white")
        with patch.object(services,"_load_image_source",return_value=image), patch.object(layout,"analyze_layout_signature",return_value={"model":"PP-DocLayoutV3", "regions":[]}) as current:
            with self.assertRaises(HTTPException) as error:
                services._generate_layout_signature_for_source("source")
        self.assertEqual(error.exception.status_code,503)
        current.assert_called_once()
