import os
import unittest
from unittest.mock import MagicMock, patch
from app.core import model_runtime_client as client


class GatewayPrefixTests(unittest.TestCase):
    def test_all_runtime_urls_use_paddle_prefix(self):
        with patch.object(client, 'GATEWAY_URL', 'https://gateway.example/'), \
             patch.dict(os.environ, {'MODEL_RUNTIME_ROLE':''}):
            for kind in client.ModelRuntimeKind:
                url = client.runtime_url(kind)
                self.assertTrue(url.startswith('https://gateway.example/paddle/api/v1/'), url)
                self.assertEqual(url.count('/paddle'), 1)

    def test_stored_legacy_paths_and_new_paths_use_the_same_endpoint(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"success":true,"result":{}}'
        with patch.object(client, 'GATEWAY_URL', 'https://gateway.example'), \
             patch.object(client.urllib.request, 'urlopen', return_value=response) as http:
            for path in ['/api/v1/text-recognition-batches?version=v5',
                         '/paddle/api/v1/text-recognition-batches?version=v5']:
                client._post_predict(client.ModelRuntimeKind.TEXT_RECOGNITION, {}, path_override=path)
                self.assertEqual(http.call_args.args[0].full_url,
                                 'https://gateway.example/paddle/api/v1/text-recognition-batches?version=v5')



if __name__ == '__main__':
    unittest.main()
