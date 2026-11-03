import copy
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from fastapi import HTTPException

from app.api.schemas import OcrModelPayload
from app.business.services import GlobalSettingsService, DEFAULT_OCR_MODEL_SETTINGS, normalize_ocr_model_settings
from app.core import model_runtime_client as client


def response(model='rec:thai_new'):
    result = MagicMock()
    result.__enter__.return_value.read.return_value = json.dumps({'success':True,'model':model,'result':{'results':[]}}).encode()
    return result


def payload():
    return OcrModelPayload(display_name='Fine-tuned', single_api_path='/paddle/api/v1/text-recognitions',
        batch_api_path='/paddle/api/v1/text-recognition-batches', model_name='rec',
        model_version='v5', runtime_model_id='thai_new')


class OcrModelRegistrationTests(unittest.TestCase):
    def test_add_calls_real_inference_client_for_single_and_batch_before_saving(self):
        service = GlobalSettingsService()
        with patch.object(service,'get_ocr_model_settings',return_value={'ocr_models':copy.deepcopy(DEFAULT_OCR_MODEL_SETTINGS)}), \
             patch.object(service,'_save_setting') as save, \
             patch.object(client,'GATEWAY_URL','https://gateway.example'), \
             patch.object(client.urllib.request,'urlopen',side_effect=[response(),response()]) as http:
            result=service.upsert_ocr_model('text_recognition',payload())
        self.assertEqual(http.call_count,2)
        self.assertIn('/paddle/api/v1/text-recognitions?version=v5&model_id=thai_new',http.call_args_list[0].args[0].full_url)
        self.assertIn('/paddle/api/v1/text-recognition-batches?version=v5&model_id=thai_new',http.call_args_list[1].args[0].full_url)
        self.assertIn('image',json.loads(http.call_args_list[0].args[0].data))
        self.assertIn('images',json.loads(http.call_args_list[1].args[0].data))
        self.assertTrue(result['processing_check']['processing_verified'])
        self.assertEqual(result['ocr_models']['active']['text_recognition'],'thai_ocr_v5_mobile')
        saved=json.loads(save.call_args.args[1])
        self.assertEqual(saved['models']['text_recognition'][-1]['id'],'thai_new')
        self.assertFalse(result['fixed_models'])

    def test_failed_single_or_batch_request_does_not_save_model(self):
        for responses in [[HTTPError('https://gateway.example',404,'missing',{},None)],
                          [response(),HTTPError('https://gateway.example',503,'unavailable',{},None)]]:
            with self.subTest(request_count=len(responses)):
                service=GlobalSettingsService()
                with patch.object(service,'get_ocr_model_settings',return_value={'ocr_models':copy.deepcopy(DEFAULT_OCR_MODEL_SETTINGS)}), \
                     patch.object(service,'_save_setting') as save, patch.object(client,'GATEWAY_URL','https://gateway.example'), \
                     patch.object(client.urllib.request,'urlopen',side_effect=responses):
                    with self.assertRaises(HTTPException) as error:
                        service.upsert_ocr_model('text_recognition',payload())
                self.assertEqual(error.exception.status_code,503)
                save.assert_not_called()

    def test_gateway_cannot_silently_substitute_default_for_requested_model(self):
        service=GlobalSettingsService()
        with patch.object(service,'get_ocr_model_settings',return_value={'ocr_models':copy.deepcopy(DEFAULT_OCR_MODEL_SETTINGS)}), \
             patch.object(service,'_save_setting') as save, patch.object(client,'GATEWAY_URL','https://gateway.example'), \
             patch.object(client.urllib.request,'urlopen',return_value=response('default')):
            with self.assertRaises(HTTPException) as error:
                service.upsert_ocr_model('text_recognition',payload())
        self.assertIn('did not confirm',str(error.exception.detail))
        save.assert_not_called()

    def test_model_check_calls_processing_endpoints_without_registry(self):
        service=GlobalSettingsService()
        with patch.object(service,'get_ocr_model_settings',return_value={'ocr_models':copy.deepcopy(DEFAULT_OCR_MODEL_SETTINGS)}), \
             patch.object(client,'GATEWAY_URL','https://gateway.example'), \
             patch.object(client.urllib.request,'urlopen',side_effect=[response('default'),response('default')]) as http:
            result=service.check_ocr_model_runtime('text_recognition','thai_ocr_v5_mobile')
        self.assertTrue(result['processing_verified'])
        self.assertEqual(result['validation_scope'],'processing_api')
        self.assertTrue(all('/ocr-models' not in call.args[0].full_url for call in http.call_args_list))
        self.assertEqual(http.call_count,2)

    def test_unknown_or_unidentified_endpoint_response_cannot_pass_probe(self):
        missing_model=MagicMock()
        missing_model.__enter__.return_value.read.return_value=b'{"success":true,"result":{}}'
        with patch.object(client,'GATEWAY_URL','https://gateway.example'), \
             patch.object(client.urllib.request,'urlopen',return_value=missing_model):
            with self.assertRaises(client.ModelRuntimeUnavailableError):
                client.probe_ocr_model('text_recognition',DEFAULT_OCR_MODEL_SETTINGS['models']['text_recognition'][0])

    def test_saved_custom_models_and_selection_are_used_by_inference(self):
        settings=copy.deepcopy(DEFAULT_OCR_MODEL_SETTINGS)
        settings['models']['text_recognition'].append(dict(id='thai_new',display_name='New',
            single_api_path='/paddle/api/v1/text-recognitions?version=v5&model_id=thai_new',
            batch_api_path='/paddle/api/v1/text-recognition-batches?version=v5&model_id=thai_new'))
        settings['active']['text_recognition']='thai_new'
        normalized=normalize_ocr_model_settings(settings)
        self.assertEqual(normalized['active']['text_recognition'],'thai_new')
        connection=MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value={'value':json.dumps(settings)}
        with patch.object(client,'connect_db',return_value=connection):
            self.assertIn('model_id=thai_new',client._active_ocr_path('text_recognition','single'))
            self.assertIn('model_id=thai_new',client._active_ocr_path('text_recognition','batch'))

    def test_leaf_gateway_model_selection_metadata_is_accepted_for_real_probe(self):
        for wrapper in ('data','result'):
            with self.subTest(wrapper=wrapper):
                reply=MagicMock()
                reply.__enter__.return_value.read.return_value=json.dumps({'success':True,wrapper:{
                    'contract_version':'leaf-inference-v1',
                    'model_selection':{'model_name':'th_PP-OCRv5_mobile_rec','version':'v5','variant':'baseline'},
                    'rec_text':'OCR TEST 123','rec_score':.9}}).encode()
                with patch.object(client,'GATEWAY_URL','https://gateway.example'), \
                     patch.object(client.urllib.request,'urlopen',return_value=reply):
                    checked=client.probe_ocr_model('text_recognition',DEFAULT_OCR_MODEL_SETTINGS['models']['text_recognition'][0])
                self.assertTrue(checked['processing_verified'])
                self.assertEqual(checked['models']['batch'],'th_PP-OCRv5_mobile_rec')

    def test_saved_custom_model_is_not_erased_when_settings_are_reloaded(self):
        settings=copy.deepcopy(DEFAULT_OCR_MODEL_SETTINGS)
        custom=dict(id='custom',display_name='Custom',single_api_path='/paddle/api/v1/custom-rec',batch_api_path='/paddle/api/v1/custom-rec-batch')
        settings['models']['text_recognition'].append(custom)
        settings['active']['text_recognition']='custom'
        normalized=normalize_ocr_model_settings(json.dumps(settings))
        self.assertEqual(normalized['models']['text_recognition'][-1],custom)
        self.assertEqual(normalized['active']['text_recognition'],'custom')
        normalized['models']['text_recognition'][-1]['display_name']='changed'
        self.assertEqual(settings['models']['text_recognition'][-1]['display_name'],'Custom')

    def test_settings_listing_does_not_call_gateway(self):
        service=GlobalSettingsService()
        with patch.object(service,'_apply_pending_settings_if_due'), \
             patch.object(service,'_active_ocr_model_settings_no_apply',return_value=copy.deepcopy(DEFAULT_OCR_MODEL_SETTINGS)), \
             patch.object(service,'_load_setting_value',return_value=None), \
             patch.object(client.urllib.request,'urlopen') as http:
            result=service.get_ocr_model_settings()
        self.assertFalse(result['fixed_models'])
        self.assertIsNone(result['registry_error'])
        http.assert_not_called()

    def test_active_model_cannot_be_deleted(self):
        service=GlobalSettingsService()
        with patch.object(service,'get_ocr_model_settings',return_value={'ocr_models':copy.deepcopy(DEFAULT_OCR_MODEL_SETTINGS)}):
            with self.assertRaises(HTTPException) as error:
                service.delete_ocr_model('text_recognition','thai_ocr_v5_mobile')
        self.assertEqual(error.exception.status_code,409)


if __name__=='__main__':
    unittest.main()
