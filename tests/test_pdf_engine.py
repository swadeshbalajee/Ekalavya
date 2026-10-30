from types import SimpleNamespace

import pytest

from scholarmotion.providers.llm import openai_compatible
from scholarmotion.providers.llm.openai_compatible import OpenAICompatibleProvider


def _model(model_id, modalities):
    return SimpleNamespace(
        id=model_id, model_extra={"architecture": {"input_modalities": modalities}}
    )


class _Models:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error

    async def list(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


def _provider(models, engine="cloudflare-ai"):
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.client = SimpleNamespace(models=models)
    provider.pdf_engine = engine
    return provider


@pytest.mark.asyncio
async def test_models_that_read_files_get_the_pdf_and_others_a_conversion(monkeypatch):
    monkeypatch.setattr(openai_compatible, "_MODEL_INPUTS", None)
    provider = _provider(
        _Models(
            [
                _model("anthropic/claude-sonnet-5.5", ["text", "image", "file"]),
                _model("deepseek/deepseek-chat", ["text"]),
            ]
        ),
        engine="mistral-ocr",
    )

    assert await provider._pdf_engine("anthropic/claude-sonnet-5.5") == "native"
    assert await provider._pdf_engine("deepseek/deepseek-chat") == "mistral-ocr"
    assert await provider._pdf_engine("unlisted/model") == "mistral-ocr"


@pytest.mark.asyncio
async def test_an_unreachable_model_list_falls_back_to_the_conversion(monkeypatch):
    monkeypatch.setattr(openai_compatible, "_MODEL_INPUTS", None)
    provider = _provider(_Models(error=RuntimeError("offline")))

    assert await provider._pdf_engine("anthropic/claude-sonnet-5.5") == "cloudflare-ai"
    assert openai_compatible._MODEL_INPUTS is None
