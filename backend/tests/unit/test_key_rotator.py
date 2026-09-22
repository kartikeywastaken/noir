import concurrent.futures
from unittest.mock import MagicMock

from pydantic import SecretStr

from noir.domain.config import NoirConfig
from noir.infrastructure.ai.gemini import GeminiProvider
from noir.infrastructure.ai.key_rotator import ApiKeyRotator


def test_key_rotator_empty():
    rotator = ApiKeyRotator([])
    assert not rotator.has_keys()
    assert rotator.get_next_key() == ""
    assert rotator.get_all_keys() == []


def test_key_rotator_single_key():
    rotator = ApiKeyRotator(["key1"])
    assert rotator.has_keys()
    assert rotator.get_next_key() == "key1"
    assert rotator.get_next_key() == "key1"


def test_key_rotator_round_robin():
    rotator = ApiKeyRotator(["key1", "key2", "key3"])
    assert rotator.get_all_keys() == ["key1", "key2", "key3"]
    assert rotator.get_next_key() == "key1"
    assert rotator.get_next_key() == "key2"
    assert rotator.get_next_key() == "key3"
    assert rotator.get_next_key() == "key1"


def test_key_rotator_deduplication_and_stripping():
    rotator = ApiKeyRotator(["  key1 ", "key2", "key1", "", "   "])
    assert rotator.get_all_keys() == ["key1", "key2"]


def test_key_rotator_cooldown_skipping():
    rotator = ApiKeyRotator(["key1", "key2", "key3"])
    # Mark key2 as rate-limited for 60s
    rotator.mark_rate_limited("key2", cooldown_seconds=60.0)
    assert rotator.is_in_cooldown("key2")
    assert not rotator.is_in_cooldown("key1")

    # Sequence should skip key2
    assert rotator.get_next_key() == "key1"
    assert rotator.get_next_key() == "key3"
    assert rotator.get_next_key() == "key1"
    assert rotator.get_next_key() == "key3"


def test_key_rotator_all_in_cooldown():
    rotator = ApiKeyRotator(["key1", "key2"])
    # Put key1 in cooldown for 10s, key2 for 50s
    rotator.mark_rate_limited("key1", cooldown_seconds=10.0)
    rotator.mark_rate_limited("key2", cooldown_seconds=50.0)

    # The caller must wait instead of immediately reusing a cooling key.
    assert rotator.get_next_key() == ""
    assert 0 < rotator.retry_after_seconds() <= 10


def test_key_rotator_concurrency():
    rotator = ApiKeyRotator(["key1", "key2", "key3", "key4"])
    results = []

    def worker():
        for _ in range(50):
            k = rotator.get_next_key()
            results.append(k)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker) for _ in range(8)]
        for f in futures:
            f.result()

    assert len(results) == 400
    for k in ["key1", "key2", "key3", "key4"]:
        assert results.count(k) == 100


def test_noir_config_gemini_api_keys():
    cfg = NoirConfig(
        _env_file=None,
        gemini_api_keys=SecretStr("key_a, key_b, key_c"),
    )
    assert cfg.get_gemini_generation_keys() == ["key_a", "key_b", "key_c"]
    assert cfg.gemini_key_for("generation") == "key_a"


def test_noir_config_gemini_keys_deduplication():
    cfg = NoirConfig(
        _env_file=None,
        gemini_api_keys=SecretStr("key_a, key_b"),
        gemini_generation_api_key=SecretStr("key_b, key_c"),
        gemini_api_key=SecretStr("key_a, key_d"),
    )
    # Order: gemini_api_keys, then generation, then legacy
    assert cfg.get_gemini_generation_keys() == ["key_a", "key_b", "key_c", "key_d"]


def test_noir_config_safe_dict_redacts_keys():
    cfg = NoirConfig(
        _env_file=None,
        gemini_api_keys=SecretStr("secret_key_1,secret_key_2"),
        gemini_generation_api_key=SecretStr("secret_gen"),
    )
    safe = cfg.to_safe_dict()
    assert safe["gemini_api_keys"] == "***REDACTED***"
    assert safe["gemini_generation_api_key"] == "***REDACTED***"
    assert "secret_key_1" not in str(safe)


def test_gemini_provider_uses_rotator_and_rotates(monkeypatch):
    cfg = NoirConfig(
        _env_file=None,
        gemini_api_keys=SecretStr("key_1, key_2"),
    )
    provider = GeminiProvider(config=cfg, purpose="generation")
    assert provider.key_rotator.get_all_keys() == ["key_1", "key_2"]


def test_gemini_provider_429_failover_to_next_key(monkeypatch):
    cfg = NoirConfig(
        _env_file=None,
        gemini_api_keys=SecretStr("rate_limited_key, working_key"),
        ai_model="gemini-3.6-flash",
    )
    provider = GeminiProvider(config=cfg, purpose="generation")
    provider.key_rotator.reset_cooldowns()

    call_keys = []

    def mock_client_for_key(key):
        from google.genai import types

        client = MagicMock()

        def generate_content(model, contents, config):
            call_keys.append(key)
            if key == "rate_limited_key":
                exc = Exception("429 RESOURCE_EXHAUSTED quota exceeded")
                exc.status_code = 429
                raise exc
            mock_response = MagicMock()
            mock_response.text = '{"status": "ok"}'
            mock_response.prompt_feedback = None
            mock_candidate = MagicMock()
            mock_candidate.finish_reason = types.FinishReason.STOP
            mock_response.candidates = [mock_candidate]
            return mock_response

        client.models.generate_content = generate_content
        return client

    monkeypatch.setattr(provider, "_client_for_key", mock_client_for_key)

    result = provider._call_model("test prompt", json_output=True)
    assert result is not None
    # Both keys were tried in sequence
    assert "rate_limited_key" in call_keys
    assert "working_key" in call_keys
    # The rate-limited key was placed in cooldown
    assert provider.key_rotator.is_in_cooldown("rate_limited_key")
