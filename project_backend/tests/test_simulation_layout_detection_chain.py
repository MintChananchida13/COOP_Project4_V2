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
        with patch.object(admin,"get_template",return_value={"id":"test", "pages":[{}]}), patch.object(services,"_connect",return_value=conn), patch.object(layout,"_layout_raw_items_cache",OrderedDict()), patch.object(layout,"_require_runtime"), patch.object(client,"is_runtime_configured",return_value=True), patch.object(client,"_post_predict",return_value=raw) as http:
            result=admin.run_prepublish_simulation("test")
        self.assertFalse(http.call_args.args[1]["layout_only"])
        summary=result["layout_signature_pages"][0]
        self.assertEqual(summary["model"],"PP-DocLayoutV3+PP-OCRv6_medium_det")
        self.assertEqual(summary["region_count"],2)
        updates=[c for c in conn.execute.call_args_list if "UPDATE template_pages" in c.args[0]]
        self.assertEqual(len(updates),1)
        import json
        stored=json.loads(updates[0].args[1][0])
        self.assertEqual(stored["label_counts"]["text"],1)
        self.assertEqual(stored["label_counts"]["image"],1)
