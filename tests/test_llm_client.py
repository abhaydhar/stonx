"""Tests for modules.llm_client -- must degrade to None, never raise."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules import llm_client


def _fake_config(api_key=None, base_url=None, model=None):
    return SimpleNamespace(
        GEMINI_API_KEY=api_key,
        LLM_BASE_URL=base_url or llm_client.DEFAULT_BASE_URL,
        LLM_MODEL=model or llm_client.DEFAULT_MODEL,
    )


class TestIsConfigured:
    def test_false_when_no_key(self):
        with patch.object(llm_client, "_get_config", return_value=_fake_config(api_key=None)):
            assert llm_client.is_configured() is False

    def test_true_when_key_present(self):
        with patch.object(llm_client, "_get_config", return_value=_fake_config(api_key="abc")):
            assert llm_client.is_configured() is True

    def test_false_when_config_unavailable(self):
        with patch.object(llm_client, "_get_config", return_value=None):
            assert llm_client.is_configured() is False


class TestGetClient:
    def test_none_when_no_api_key(self):
        with patch.object(llm_client, "_get_config", return_value=_fake_config(api_key=None)):
            assert llm_client.get_client() is None

    def test_builds_client_with_base_url_when_key_present(self):
        cfg = _fake_config(api_key="abc", base_url="https://example.test/openai/")
        with patch.object(llm_client, "_get_config", return_value=cfg):
            with patch("openai.OpenAI") as mock_openai:
                llm_client.get_client()
                mock_openai.assert_called_once_with(api_key="abc", base_url="https://example.test/openai/")

    def test_none_when_construction_raises(self):
        cfg = _fake_config(api_key="abc")
        with patch.object(llm_client, "_get_config", return_value=cfg):
            with patch("openai.OpenAI", side_effect=RuntimeError("boom")):
                assert llm_client.get_client() is None


class TestComplete:
    def test_none_when_client_unavailable(self):
        with patch.object(llm_client, "get_client", return_value=None):
            assert llm_client.complete("sys", "user") is None

    def test_returns_stripped_content_on_success(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  hello world  "))]
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        with patch.object(llm_client, "get_client", return_value=fake_client):
            with patch.object(llm_client, "_get_config", return_value=_fake_config(api_key="abc")):
                result = llm_client.complete("sys", "user")
        assert result == "hello world"
        fake_client.chat.completions.create.assert_called_once()
        _, kwargs = fake_client.chat.completions.create.call_args
        assert kwargs["model"] == llm_client.DEFAULT_MODEL
        assert kwargs["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ]

    def test_none_on_exception(self):
        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("network down")
        with patch.object(llm_client, "get_client", return_value=fake_client):
            assert llm_client.complete("sys", "user") is None

    def test_none_when_content_is_none(self):
        fake_response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))])
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        with patch.object(llm_client, "get_client", return_value=fake_client):
            assert llm_client.complete("sys", "user") is None

    def test_explicit_model_overrides_config_default(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        with patch.object(llm_client, "get_client", return_value=fake_client):
            with patch.object(llm_client, "_get_config", return_value=_fake_config(api_key="abc")):
                llm_client.complete("sys", "user", model="gemini-2.5-pro")
        _, kwargs = fake_client.chat.completions.create.call_args
        assert kwargs["model"] == "gemini-2.5-pro"
