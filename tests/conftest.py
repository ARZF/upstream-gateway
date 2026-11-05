import os

import pytest

from app.core.config import Settings


PROVIDER_URLS = {
    "provider_a_url": "https://provider-a/v1/process",
    "provider_b_url": "https://provider-b/v1/process",
    "provider_c_url": "https://provider-c/v1/process",
}

for name, value in PROVIDER_URLS.items():
    os.environ[name.upper()] = value


@pytest.fixture
def settings_factory():
    def make_settings(**overrides):
        return Settings(**(PROVIDER_URLS | overrides))

    return make_settings
