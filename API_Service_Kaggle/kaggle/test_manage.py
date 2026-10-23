import unittest
from unittest.mock import patch

import manage


class ConfigTests(unittest.TestCase):
    def test_requires_secrets(self):
        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
            manage.validate_config({"MODEL_GATEWAY_API_KEY": "12345678"})

    def test_rejects_non_postgres(self):
        with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
            manage.validate_config({"DATABASE_URL": "sqlite:///tmp/db", "MODEL_GATEWAY_API_KEY": "12345678"})

    def test_valid_config_does_not_print_secret(self):
        with patch("builtins.print") as output:
            manage.validate_config({"DATABASE_URL": "postgresql://user:password@db.example/test", "MODEL_GATEWAY_API_KEY": "secret-key"})
        printed = str(output.call_args_list)
        self.assertNotIn("password", printed)
        self.assertNotIn("secret-key", printed)


if __name__ == "__main__":
    unittest.main()
