"""Read Kaggle secrets without displaying values."""

import os


def load_secrets(names):
    values = {}
    missing = [name for name in names if not os.environ.get(name)]
    client = None
    if missing:
        try:
            from kaggle_secrets import UserSecretsClient

            client = UserSecretsClient()
        except ImportError:
            pass
    for name in names:
        value = os.environ.get(name, "")
        if not value and client is not None:
            try:
                value = client.get_secret(name)
            except Exception:
                pass
        if value:
            values[name] = value
    return values
