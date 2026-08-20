import tempfile
from pathlib import Path

import httpx
import pytest
import respx

from dendrophis.llm.calibration import (
    ModelCapabilities,
    ModelOverrideStore,
    calibrate_model,
    check_parameter_support,
    detect_provider,
    extract_capabilities_from_metadata,
    is_param_rejected,
)


def test_model_capabilities_to_from_dict():
    capabilities = ModelCapabilities(
        model_id="test-model",
        provider="test-provider",
        context_window=4096,
        max_tokens=2048,
        supports_streaming=True,
        supports_tools=False,
        supports_reasoning_effort=True,
        supports_caching=True,
        supports_prompt_cache=True,
        supports_prompt_cache_key=True,
        rejected_params=["param1"],
        requires_params=["param2"],
        test_results={"test": True},
        test_errors={"test_err": "some error"},
        raw_metadata={"raw_key": "raw_val"},
    )

    capabilities_dict = capabilities.to_dict()
    assert capabilities_dict["model_id"] == "test-model"
    assert capabilities_dict["context_window"] == 4096

    loaded_capabilities = ModelCapabilities.from_dict(capabilities_dict)
    assert loaded_capabilities.model_id == "test-model"
    assert loaded_capabilities.provider == "test-provider"
    assert loaded_capabilities.context_window == 4096
    assert loaded_capabilities.max_tokens == 2048
    assert loaded_capabilities.supports_streaming is True
    assert loaded_capabilities.supports_tools is False
    assert loaded_capabilities.supports_reasoning_effort is True
    assert loaded_capabilities.supports_caching is True
    assert loaded_capabilities.supports_prompt_cache is True
    assert loaded_capabilities.supports_prompt_cache_key is True
    assert loaded_capabilities.rejected_params == ["param1"]
    assert loaded_capabilities.requires_params == ["param2"]
    assert loaded_capabilities.test_results == {"test": True}
    assert loaded_capabilities.test_errors == {"test_err": "some error"}
    assert loaded_capabilities.raw_metadata == {"raw_key": "raw_val"}


def test_model_override_store_load_save():
    with tempfile.TemporaryDirectory() as temporary_directory:
        override_file_path = Path(temporary_directory) / "model_overrides.yaml"
        override_store = ModelOverrideStore(path=override_file_path)

        # Store should initially be empty
        assert len(override_store.list_models()) == 0

        # Save some capabilities
        capabilities = ModelCapabilities(model_id="test-model-1", provider="provider-1")
        override_store.set(capabilities)
        override_store.save()

        # Load back
        new_store = ModelOverrideStore(path=override_file_path)
        assert len(new_store.list_models()) == 1
        loaded_capabilities = new_store.get("test-model-1")
        assert loaded_capabilities is not None
        assert loaded_capabilities.model_id == "test-model-1"
        assert loaded_capabilities.provider == "provider-1"

        # Remove
        new_store.remove("test-model-1")
        new_store.save()

        fresh_store = ModelOverrideStore(path=override_file_path)
        assert len(fresh_store.list_models()) == 0


def test_detect_provider():
    assert detect_provider("https://api.deepinfra.com/v1/openai") == "deepinfra"
    assert detect_provider("https://openrouter.ai/api/v1") == "openrouter"
    assert detect_provider("https://api.mistral.ai/v1") == "mistral"
    assert detect_provider("https://api.anthropic.com/v1") == "anthropic"
    assert detect_provider("http://localhost:8000/v1") == "local"
    assert detect_provider("http://127.0.0.1:8080/v1") == "local"
    assert detect_provider("https://api.openai.com/v1") == "unknown"


def test_extract_capabilities_from_metadata():
    test_model_metadata = {
        "id": "model-1",
        "context_length": 8192,
        "max_tokens": 1024,
    }
    capabilities = extract_capabilities_from_metadata(test_model_metadata)
    assert capabilities.model_id == "model-1"
    assert capabilities.context_window == 8192
    assert capabilities.max_tokens == 1024

    # Test with nested metadata
    test_nested_metadata = {
        "id": "model-2",
        "metadata": {
            "context_length": 16384,
            "max_tokens": 4096,
        },
    }
    capabilities_nested = extract_capabilities_from_metadata(test_nested_metadata)
    assert capabilities_nested.model_id == "model-2"
    assert capabilities_nested.context_window == 16384
    assert capabilities_nested.max_tokens == 4096


@respx.mock
@pytest.mark.anyio
async def test_check_parameter_support():
    # Mock a successful post request
    respx.post("https://api.openai.com/v1/chat/completions").respond(status_code=200, json={"success": True})

    async with httpx.AsyncClient() as mock_client:
        is_supported, error_message = await check_parameter_support(
            client=mock_client,
            base_url="https://api.openai.com/v1",
            model_id="test-model",
            param_name="test_param",
            param_value="test_value",
            api_key="test_key",
        )
        assert is_supported is True
        assert error_message is None

    # Mock a failing post request
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        status_code=400, json={"error": {"message": "Parameter not supported"}}
    )

    async with httpx.AsyncClient() as mock_client:
        is_supported, error_message = await check_parameter_support(
            client=mock_client,
            base_url="https://api.openai.com/v1",
            model_id="test-model",
            param_name="test_param",
            param_value="test_value",
            api_key="test_key",
        )
        assert is_supported is False
        assert error_message == "Parameter not supported"


@respx.mock
@pytest.mark.anyio
async def test_calibrate_model():
    with tempfile.TemporaryDirectory() as temporary_directory:
        override_file_path = Path(temporary_directory) / "model_overrides.yaml"
        override_store = ModelOverrideStore(path=override_file_path)

        # Mock the models endpoint
        respx.get("https://api.openai.com/v1/models").respond(
            status_code=200,
            json={
                "data": [
                    {
                        "id": "gpt-4",
                        "context_length": 8192,
                    }
                ]
            },
        )

        # Mock parameter checks (e.g. reasoning_effort supported, cache_control/prompt_cache_key not supported)
        respx.post("https://api.openai.com/v1/chat/completions").respond(
            status_code=400, json={"error": {"message": "Invalid parameter"}}
        )

        capabilities = await calibrate_model(
            model_id="gpt-4",
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            force=True,
            store=override_store,
        )

        assert capabilities.model_id == "gpt-4"
        assert capabilities.context_window == 8192
        assert capabilities.supports_reasoning_effort is False  # mocked post returned 400
        assert "reasoning_effort" in capabilities.rejected_params

        # Test is_param_rejected function
        assert is_param_rejected("gpt-4", "reasoning_effort", store=override_store) is True
        assert is_param_rejected("gpt-4", "non-existent-param", store=override_store) is False


def test_calibration_prompt_screen():
    from dendrophis.ui.screens.calibration_prompt import CalibrationPromptScreen

    screen = CalibrationPromptScreen(model_id="test-model")
    assert screen._model_id == "test-model"


def test_app_check_and_prompt_calibration():
    from unittest.mock import MagicMock

    from dendrophis.llm.calibration import ModelOverrideStore
    from dendrophis.ui.app import DendrophisApp
    from dendrophis.ui.screens.calibration_prompt import CalibrationPromptScreen

    original_get = ModelOverrideStore.get
    ModelOverrideStore.get = MagicMock(return_value=None)

    try:
        app = MagicMock()
        DendrophisApp._check_and_prompt_calibration(app, "uncalibrated-model")

        app.push_screen.assert_called_once()
        args, _unused_kwargs = app.push_screen.call_args
        assert isinstance(args[0], CalibrationPromptScreen)
        assert args[0]._model_id == "uncalibrated-model"
    finally:
        ModelOverrideStore.get = original_get
