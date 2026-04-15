"""Preflight / factory tests for the Keybase gateway adapter.

These tests verify that the adapter factory in gateway/run.py correctly
gates adapter creation on check_keybase_requirements().
"""

from unittest.mock import MagicMock, patch

from gateway.config import Platform, PlatformConfig


class TestKeybaseAdapterFactory:
    def test_factory_returns_none_when_requirements_not_met(self):
        """_create_adapter returns None when keybase CLI is absent or not logged in."""
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = MagicMock()
        runner.config.group_sessions_per_user = False
        runner.config.thread_sessions_per_user = False

        pc = PlatformConfig(enabled=True, extra={"binary": "keybase", "listen": False})

        with patch("gateway.platforms.keybase.check_keybase_requirements", return_value=False):
            result = runner._create_adapter(Platform.KEYBASE, pc)

        assert result is None

    def test_factory_returns_adapter_when_requirements_met(self):
        """_create_adapter returns a KeybaseAdapter when the CLI is present and logged in."""
        from gateway.platforms.keybase import KeybaseAdapter
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = MagicMock()
        runner.config.group_sessions_per_user = False
        runner.config.thread_sessions_per_user = False

        pc = PlatformConfig(enabled=True, extra={"binary": "keybase", "listen": False})

        with patch("gateway.platforms.keybase.check_keybase_requirements", return_value=True):
            result = runner._create_adapter(Platform.KEYBASE, pc)

        assert isinstance(result, KeybaseAdapter)
