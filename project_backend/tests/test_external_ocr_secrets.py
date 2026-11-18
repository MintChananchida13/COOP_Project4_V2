import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.core.external_ocr_secrets import ExternalOcrSecretError, decrypt_api_key, encrypt_api_key


class ExternalOcrSecretTests(unittest.TestCase):
    def test_round_trip_and_wrong_key(self):
        with patch.dict(os.environ, {"EXTERNAL_OCR_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            encrypted = encrypt_api_key("sensitive-api-key")
            self.assertNotIn("sensitive-api-key", encrypted)
            self.assertEqual(decrypt_api_key(encrypted), "sensitive-api-key")
        with patch.dict(os.environ, {"EXTERNAL_OCR_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            with self.assertRaises(ExternalOcrSecretError):
                decrypt_api_key(encrypted)

    def test_missing_or_invalid_key_fails_closed(self):
        for key in ("", "invalid"):
            with self.subTest(key=key), patch.dict(os.environ, {"EXTERNAL_OCR_ENCRYPTION_KEY": key}):
                with self.assertRaises(ExternalOcrSecretError):
                    encrypt_api_key("credential")
