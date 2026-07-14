"""Network-free unit tests for src/utils/llm_client._normalize_messages.

The normalizer folds system-role content into the first user message so models
that reject the system role (some free OpenRouter models) still work."""

from src.utils.llm_client import _normalize_messages


def test_system_folded_into_first_user_message():
    out = _normalize_messages(
        [
            {"role": "system", "content": "You are a bot."},
            {"role": "user", "content": "Hi."},
        ]
    )
    assert all(m["role"] != "system" for m in out)
    assert out[0]["role"] == "user"
    assert "You are a bot." in out[0]["content"]
    assert "Hi." in out[0]["content"]


def test_multiple_system_messages_merged_in_order():
    out = _normalize_messages(
        [
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
            {"role": "user", "content": "Q"},
        ]
    )
    assert len(out) == 1
    content = out[0]["content"]
    assert content.index("A") < content.index("B") < content.index("Q")


def test_no_system_messages_passthrough():
    msgs = [{"role": "user", "content": "hello"}]
    assert _normalize_messages(msgs) == msgs


def test_system_only_becomes_user_message():
    out = _normalize_messages([{"role": "system", "content": "only system"}])
    assert out == [{"role": "user", "content": "only system"}]


def test_caller_list_not_mutated():
    original = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    snapshot = [dict(m) for m in original]
    _normalize_messages(original)
    assert original == snapshot
