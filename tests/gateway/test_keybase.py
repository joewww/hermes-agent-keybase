"""Tests for Keybase platform integration."""

import ast
import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from gateway.config import Platform, PlatformConfig


def _run_async_immediately(coro):
    return asyncio.run(coro)


def _extract_module_dict_literal(path: Path, var_name: str):
    """Extract a top-level literal dict assignment without importing the module."""
    module_ast = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module_ast.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == var_name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{var_name} not found in {path}")


class TestKeybaseConfig:
    def test_keybase_platform_enum_exists(self):
        assert Platform.KEYBASE.value == "keybase"

    def test_apply_env_overrides_keybase(self):
        from gateway.config import GatewayConfig, _apply_env_overrides

        env = {
            "KEYBASE_ENABLED": "true",
            "KEYBASE_HOME_CHANNEL": "teamname#general",
            "KEYBASE_HOME_CHANNEL_NAME": "Team General",
            "KEYBASE_BIN": "/usr/local/bin/keybase",
        }
        with patch.dict(os.environ, env, clear=False):
            config = GatewayConfig()
            _apply_env_overrides(config)

        assert Platform.KEYBASE in config.platforms
        pc = config.platforms[Platform.KEYBASE]
        assert pc.enabled is True
        assert pc.extra.get("binary") == "/usr/local/bin/keybase"
        assert pc.home_channel is not None
        assert pc.home_channel.chat_id == "teamname#general"
        assert pc.home_channel.name == "Team General"

    def test_keybase_in_connected_platforms(self):
        from gateway.config import GatewayConfig, PlatformConfig

        config = GatewayConfig()
        config.platforms[Platform.KEYBASE] = PlatformConfig(enabled=True)
        connected = config.get_connected_platforms()
        assert Platform.KEYBASE in connected


class TestKeybaseGatewayAuthorization:
    def test_keybase_in_allowlist_maps(self):
        from gateway.config import GatewayConfig
        from gateway.run import GatewayRunner

        gw = GatewayRunner.__new__(GatewayRunner)
        gw.config = GatewayConfig()
        gw.pairing_store = MagicMock()
        gw.pairing_store.is_approved.return_value = False

        source = MagicMock()
        source.platform = Platform.KEYBASE
        source.user_id = "alice"

        with patch.dict("os.environ", {}, clear=True):
            assert gw._is_user_authorized(source) is False

    def test_keybase_allow_all_users_flag(self):
        from gateway.config import GatewayConfig
        from gateway.run import GatewayRunner

        gw = GatewayRunner.__new__(GatewayRunner)
        gw.config = GatewayConfig()
        gw.pairing_store = MagicMock()
        gw.pairing_store.is_approved.return_value = False

        source = MagicMock()
        source.platform = Platform.KEYBASE
        source.user_id = "anyone"

        with patch.dict("os.environ", {"KEYBASE_ALLOW_ALL_USERS": "true"}, clear=True):
            assert gw._is_user_authorized(source) is True


class TestKeybaseSendMessageRouting:
    def test_send_message_schema_mentions_keybase_target(self):
        repo_root = Path(__file__).resolve().parents[2]
        schema = _extract_module_dict_literal(repo_root / "tools" / "send_message_tool.py", "SEND_MESSAGE_SCHEMA")

        target_desc = schema["parameters"]["properties"]["target"]["description"].lower()
        assert "delivery target" in target_desc
        assert "keybase:teamname#general" in target_desc


class TestKeybaseAdapterParsing:
    def test_parse_team_message_line(self):
        from gateway.platforms.keybase import KeybaseAdapter

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"listen": False}))
        adapter._username = "hermesbot"
        line = (
            b'{"type":"chat","msg":{"sender":{"username":"alice"},'
            b'"channel":{"name":"ops","members_type":"team","topic_name":"general"},'
            b'"content":{"type":"text","text":{"body":"hello team"}},'
            b'"sent_at":1735689600000000}}\n'
        )

        event = adapter._parse_listen_line(line)
        assert event is not None
        assert event.source.platform == Platform.KEYBASE
        assert event.source.chat_id == "ops#general"
        assert event.source.chat_type == "group"
        assert event.source.user_id == "alice"
        assert event.text == "hello team"

    def test_parse_ignores_own_message(self):
        from gateway.platforms.keybase import KeybaseAdapter

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"listen": False}))
        adapter._username = "alice"
        line = (
            b'{"type":"chat","msg":{"sender":{"username":"alice"},'
            b'"channel":{"name":"bob","members_type":"kbfs"},'
            b'"content":{"type":"text","text":{"body":"self message"}}}}\n'
        )

        assert adapter._parse_listen_line(line) is None

    def test_parse_invalid_json_line_returns_none(self):
        from gateway.platforms.keybase import KeybaseAdapter

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"listen": False}))
        assert adapter._parse_listen_line(b"not-json\n") is None

    def test_get_chat_info_group_and_dm(self):
        from gateway.platforms.keybase import KeybaseAdapter

        adapter = KeybaseAdapter(PlatformConfig(enabled=True))
        group = _run_async_immediately(adapter.get_chat_info("team#general"))
        dm = _run_async_immediately(adapter.get_chat_info("alice"))

        assert group["type"] == "group"
        assert dm["type"] == "dm"

    def test_parse_impteamnative_with_topic(self):
        from gateway.platforms.keybase import KeybaseAdapter

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"listen": False}))
        adapter._username = "hermesbot"
        line = (
            b'{"type":"chat","msg":{"sender":{"username":"alice"},'
            b'"channel":{"name":"alice,hermesbot","members_type":"impteamnative","topic_name":"general"},'
            b'"content":{"type":"text","text":{"body":"hi"}},'
            b'"sent_at":1735689600000000}}\n'
        )
        event = adapter._parse_listen_line(line)
        assert event is not None
        assert event.source.chat_id == "alice,hermesbot#general"
        assert event.source.chat_type == "group"

    def test_parse_impteamnative_without_topic(self):
        from gateway.platforms.keybase import KeybaseAdapter

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"listen": False}))
        adapter._username = "hermesbot"
        line = (
            b'{"type":"chat","msg":{"sender":{"username":"alice"},'
            b'"channel":{"name":"alice,hermesbot","members_type":"impteamnative"},'
            b'"content":{"type":"text","text":{"body":"hi"}},'
            b'"sent_at":1735689600000000}}\n'
        )
        event = adapter._parse_listen_line(line)
        assert event is not None
        assert event.source.chat_id == "alice,hermesbot"
        assert event.source.chat_type == "group"

    def test_parse_empty_channel_drops_event(self):
        from gateway.platforms.keybase import KeybaseAdapter

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"listen": False}))
        adapter._username = "hermesbot"
        # No name, no conv_id — should be dropped rather than dispatched to chat_id=""
        line = (
            b'{"type":"chat","msg":{"sender":{"username":"alice"},'
            b'"channel":{"members_type":"team"},'
            b'"content":{"type":"text","text":{"body":"hi"}}}}\n'
        )
        assert adapter._parse_listen_line(line) is None


class TestKeybaseSendTargetFormatting:
    def test_send_uses_channel_flag_for_team_targets(self):
        from gateway.platforms.keybase import KeybaseAdapter

        class _Proc:
            def __init__(self):
                self.returncode = 0

            async def communicate(self):
                return b"ok", b""

        captured = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _Proc()

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"binary": "keybase", "listen": False}))
        with patch("asyncio.create_subprocess_exec", new=_fake_exec):
            res = _run_async_immediately(adapter.send("myteam#general", "hello"))

        assert res.success is True
        assert captured["args"][:3] == ("keybase", "chat", "send")
        assert "--channel" in captured["args"]
        assert "myteam" in captured["args"]

    def test_send_timeout_kills_subprocess(self):
        from gateway.platforms.keybase import KeybaseAdapter

        class _Proc:
            def __init__(self):
                self.returncode = None
                self.killed = False

            async def communicate(self):
                await asyncio.sleep(10)
                return b"", b""

            def kill(self):
                self.killed = True
                self.returncode = -9

        proc = _Proc()

        async def _fake_exec(*args, **kwargs):
            return proc

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"binary": "keybase", "listen": False}))
        with patch("asyncio.create_subprocess_exec", new=_fake_exec):
            res = _run_async_immediately(adapter.send("myteam#general", "hello", metadata={"timeout": 0.01}))

        assert res.success is False
        assert "timed out" in (res.error or "")
        assert proc.killed is True

    def test_send_accepts_reply_to_kwarg(self):
        """reply_to must be accepted (and ignored) — base._send_with_retry always passes it."""
        from gateway.platforms.keybase import KeybaseAdapter

        class _Proc:
            returncode = 0
            async def communicate(self):
                return b"ok", b""

        async def _fake_exec(*args, **kwargs):
            return _Proc()

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"binary": "keybase", "listen": False}))
        with patch("asyncio.create_subprocess_exec", new=_fake_exec):
            res = _run_async_immediately(adapter.send("user123", "hi", reply_to="msg-999"))

        assert res.success is True


class TestKeybaseListenerBackoff:
    def test_restart_delay_stepped_backoff(self):
        from gateway.platforms.keybase import KeybaseAdapter

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"listen": False}))

        assert adapter._compute_restart_delay(0) == 2.0
        assert adapter._compute_restart_delay(1) == 5.0
        assert adapter._compute_restart_delay(2) == 10.0
        assert adapter._compute_restart_delay(3) == 30.0
        assert adapter._compute_restart_delay(4) == 60.0
        assert adapter._compute_restart_delay(10) == 60.0  # capped


class TestKeybaseAncillaryWiring:
    def test_keybase_prompt_hint_exists(self):
        from agent.prompt_builder import PLATFORM_HINTS

        assert "keybase" in PLATFORM_HINTS

    def test_keybase_toolset_exists_and_in_gateway(self):
        from toolsets import get_toolset

        assert get_toolset("hermes-keybase") is not None
        gw = get_toolset("hermes-gateway")
        assert "hermes-keybase" in gw["includes"]

    def test_keybase_in_cronjob_deliver_description(self):
        repo_root = Path(__file__).resolve().parents[2]
        schema = _extract_module_dict_literal(repo_root / "tools" / "cronjob_tools.py", "CRONJOB_SCHEMA")

        desc = schema["parameters"]["properties"]["deliver"]["description"].lower()
        assert "keybase" in desc


class TestKeybaseSendImage:
    def test_send_image_falls_back_to_text_with_caption(self):
        from gateway.platforms.keybase import KeybaseAdapter

        class _Proc:
            def __init__(self):
                self.returncode = 0

            async def communicate(self):
                return b"ok", b""

        captured = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
            return _Proc()

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"binary": "keybase", "listen": False}))
        with patch("asyncio.create_subprocess_exec", new=_fake_exec):
            res = _run_async_immediately(
                adapter.send_image("alice", "https://example.com/img.png", caption="look at this")
            )

        assert res.success is True
        # Message text should contain both caption and URL
        sent_text = captured["args"][-1]
        assert "look at this" in sent_text
        assert "https://example.com/img.png" in sent_text

    def test_send_image_without_caption(self):
        from gateway.platforms.keybase import KeybaseAdapter

        class _Proc:
            def __init__(self):
                self.returncode = 0

            async def communicate(self):
                return b"ok", b""

        captured = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
            return _Proc()

        adapter = KeybaseAdapter(PlatformConfig(enabled=True, extra={"binary": "keybase", "listen": False}))
        with patch("asyncio.create_subprocess_exec", new=_fake_exec):
            res = _run_async_immediately(
                adapter.send_image("alice", "https://example.com/img.png")
            )

        assert res.success is True
        assert captured["args"][-1] == "https://example.com/img.png"


class TestKeybaseEnvWhitelist:
    def test_keybase_env_excludes_gateway_credentials(self):
        from gateway.platforms.keybase import _keybase_env

        sensitive = {
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "DISCORD_BOT_TOKEN": "discord-token",
            "TELEGRAM_BOT_TOKEN": "tg-token",
            "OPENAI_API_KEY": "openai-secret",
        }
        with patch.dict(os.environ, sensitive, clear=False):
            env = _keybase_env()

        for key in sensitive:
            assert key not in env, f"{key} should not be forwarded to keybase subprocess"

    def test_keybase_env_includes_required_vars(self):
        from gateway.platforms.keybase import _keybase_env

        base = {"PATH": "/usr/bin:/bin", "HOME": "/home/user", "LANG": "en_US.UTF-8"}
        with patch.dict(os.environ, base, clear=True):
            env = _keybase_env()

        assert env.get("PATH") == "/usr/bin:/bin"
        assert env.get("HOME") == "/home/user"

    def test_keybase_env_home_override(self):
        from gateway.platforms.keybase import _keybase_env

        with patch.dict(os.environ, {"KEYBASE_HOME": "/custom/.keybase", "HOME": "/home/user"}, clear=False):
            env = _keybase_env()

        assert env["HOME"] == "/custom/.keybase"
