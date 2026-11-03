"""The /health label must name the endpoint honestly, never assume Vultr."""
from app.llm import inference_status, provider_label


def test_vultr_host_is_named_vultr():
    assert provider_label("https://api.vultrinference.com/v1") == "vultr-serverless-inference"


def test_other_openai_host_is_not_called_vultr():
    label = provider_label("https://api.studio.nebius.ai/v1")
    assert label == "openai-compatible:api.studio.nebius.ai"
    assert "vultr" not in label


def test_unconfigured_still_reports_local_fallback():
    status = inference_status(env={})
    assert status["configured"] is False
    assert status["mode"] == "local-fallback"


def test_configured_reports_provider_and_no_secret():
    env = {
        "VULTR_INFERENCE_BASE_URL": "https://api.studio.nebius.ai/v1",
        "VULTR_INFERENCE_API_KEY": "test-not-a-real-key",
        "VULTR_INFERENCE_MODEL": "deepseek-ai/DeepSeek-V4.1-Flash",
    }
    status = inference_status(env=env)
    assert status["configured"] is True
    assert status["mode"] == "openai-compatible:api.studio.nebius.ai"
    assert "test-not-a-real-key" not in str(status)
