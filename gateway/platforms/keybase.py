"""Keybase chat platform adapter.

Outbound integration via local keybase CLI plus inbound listener support.

Features:
- Outbound send support (`keybase chat send`)
- Connectivity check (`keybase whoami`)
- Inbound listener (`keybase chat api-listen`) for text messages

Requirements:
- keybase CLI installed and available on PATH (or KEYBASE_BIN set)
- keybase user logged in (`keybase whoami` succeeds)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult

logger = logging.getLogger(__name__)

DEFAULT_SEND_TIMEOUT = 30.0
WHOAMI_TIMEOUT = 10.0
LISTENER_BACKOFF = [2, 5, 10, 30, 60]

# Variables the keybase CLI actually needs.  We whitelist rather than
# inheriting os.environ wholesale so that gateway credentials (API keys,
# bot tokens, etc.) are never exposed to the third-party subprocess.
_KEYBASE_ENV_ALLOWLIST = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE",
    "KEYBASE_BIN", "KEYBASE_HOME", "KEYBASE_RUN_MODE",
    "TMPDIR", "TMP", "TEMP",
    "XDG_RUNTIME_DIR",       # Linux session bus socket used by keybase daemon
    "SSH_AUTH_SOCK",         # Key agent forwarding
    "http_proxy", "https_proxy", "no_proxy",  # Corporate proxy support
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
}


def _keybase_bin(binary: str | None = None) -> str:
    """Resolve keybase CLI binary from explicit arg, env, then default."""
    if binary and binary.strip():
        return binary.strip()
    return os.getenv("KEYBASE_BIN", "keybase").strip() or "keybase"


def _keybase_env() -> dict[str, str]:
    """Build a minimal subprocess env for the keybase CLI.

    Only whitelisted variables are forwarded so that gateway credentials
    (ANTHROPIC_API_KEY, platform bot tokens, etc.) are never exposed to
    the keybase subprocess.
    """
    env = {k: v for k, v in os.environ.items() if k in _KEYBASE_ENV_ALLOWLIST}

    keybase_home = (os.getenv("KEYBASE_HOME") or "").strip()
    if keybase_home:
        env["HOME"] = keybase_home

    run_mode = (os.getenv("KEYBASE_RUN_MODE") or "").strip()
    if run_mode:
        env["KEYBASE_RUN_MODE"] = run_mode

    return env


def check_keybase_requirements(binary: str | None = None) -> bool:
    """Return True when keybase CLI is installed and logged in."""
    kb = _keybase_bin(binary)
    if not shutil.which(kb):
        return False

    try:
        result = subprocess.run(
            [kb, "whoami"],
            capture_output=True,
            text=True,
            timeout=WHOAMI_TIMEOUT,
            check=False,
            env=_keybase_env(),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        logger.debug("Keybase: requirements check failed: %s", exc)
        return False


class KeybaseAdapter(BasePlatformAdapter):
    platform = Platform.KEYBASE

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.KEYBASE)
        extra = config.extra or {}
        self.keybase_bin = extra.get("binary") or _keybase_bin()
        self.default_channel = extra.get("default_channel")
        self.listen_enabled = extra.get("listen", True)
        self._listen_task: asyncio.Task | None = None
        self._listen_proc: asyncio.subprocess.Process | None = None
        self._username: str | None = None

    async def connect(self) -> bool:
        if not shutil.which(self.keybase_bin):
            logger.warning("Keybase: CLI not found (%s)", self.keybase_bin)
            return False

        username, err = await self._whoami()
        if not username:
            logger.warning("Keybase: whoami failed (%s)", err or "not logged in")
            return False

        self._username = username
        self._running = True

        if self.listen_enabled:
            self._listen_task = asyncio.create_task(self._listen_loop())

        logger.info("Keybase: connected as %s", username)
        return True

    async def disconnect(self) -> None:
        self._running = False

        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        self._listen_task = None

        if self._listen_proc and self._listen_proc.returncode is None:
            await self._terminate_process(self._listen_proc)
        self._listen_proc = None

        logger.info("Keybase: disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        metadata = metadata or {}

        target = (chat_id or "").strip() or (self.default_channel or "").strip()
        if not target:
            return SendResult(success=False, error="Keybase target is required")

        text = (content or "").strip()
        if not text:
            return SendResult(success=False, error="Keybase message content is empty")

        timeout = metadata.get("timeout")
        try:
            timeout_s = float(timeout) if timeout is not None else DEFAULT_SEND_TIMEOUT
        except (TypeError, ValueError):
            timeout_s = DEFAULT_SEND_TIMEOUT

        cmd = [self.keybase_bin, "chat", "send"]
        if "#" in target:
            team, channel = target.split("#", 1)
            team = team.strip()
            channel = channel.strip() or "general"
            cmd.extend([team, "--channel", channel, text])
        else:
            cmd.extend([target, text])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_keybase_env(),
            )
        except OSError as exc:
            return SendResult(success=False, error=f"Keybase send failed: {exc}")

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return SendResult(success=False, error=f"Keybase send timed out after {timeout_s:.0f}s")

        if proc.returncode != 0:
            detail = (stderr or b"").decode(errors="replace").strip()
            return SendResult(
                success=False,
                error=detail or f"keybase chat send failed (exit {proc.returncode})",
            )

        raw = (stdout or b"").decode(errors="replace").strip()
        return SendResult(success=True, message_id=None, raw_response=raw)

    async def send_typing(self, chat_id: str, metadata: dict[str, Any] | None = None) -> None:
        """Keybase CLI has no typing indicator API."""

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        """Keybase CLI image sending is not supported; send as text link instead."""
        text = f"{caption}\n{image_url}" if caption else image_url
        return await self.send(chat_id, text)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        """Return best-effort chat metadata from keybase chat target string."""
        target = (chat_id or "").strip()
        if "#" in target:
            return {"name": target, "type": "group"}
        return {"name": target, "type": "dm"}

    async def _whoami(self) -> tuple[str | None, str | None]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.keybase_bin,
                "whoami",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_keybase_env(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=WHOAMI_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return None, "whoami timed out"

            if proc.returncode != 0:
                detail = (stderr or b"").decode(errors="replace").strip()
                return None, detail or f"exit {proc.returncode}"

            name = (stdout or b"").decode(errors="replace").strip().splitlines()
            username = name[0].strip() if name else ""
            if not username:
                return None, "empty whoami output"
            return username, None
        except OSError as exc:
            return None, str(exc)

    async def _terminate_process(self, proc: asyncio.subprocess.Process, timeout: float = 2.0) -> None:
        """Terminate process gracefully, then force kill if needed."""
        if proc.returncode is not None:
            return

        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            return
        except asyncio.TimeoutError:
            pass

        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def _consume_stderr(self, stderr: asyncio.StreamReader | None, max_bytes: int = 8192) -> str:
        """Continuously drain stderr so listener subprocess cannot block on full buffers."""
        if stderr is None:
            return ""

        chunks: list[bytes] = []
        total = 0
        while True:
            line = await stderr.readline()
            if not line:
                break
            if total < max_bytes:
                remaining = max_bytes - total
                chunk = line[:remaining]
                chunks.append(chunk)
                total += len(chunk)
            # Always read to EOF so the process is never blocked on a full pipe buffer.

        return b"".join(chunks).decode(errors="replace").strip()

    def _compute_restart_delay(self, consecutive_failures: int) -> float:
        return float(LISTENER_BACKOFF[min(consecutive_failures, len(LISTENER_BACKOFF) - 1)])

    async def _drain_stderr_task(self, task: asyncio.Task[str]) -> str:
        """Await a stderr-consumer task with a timeout, cancelling it if it stalls."""
        try:
            return await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return ""

    async def _run_listener_process(self) -> tuple[bool, str]:
        """Spawn and read one api-listen process.

        Returns (had_activity, err_text). Raises CancelledError on shutdown.
        """
        proc: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[str] | None = None
        had_activity = False
        err_text = ""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.keybase_bin,
                "chat",
                "api-listen",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_keybase_env(),
            )
            self._listen_proc = proc
            stderr_task = asyncio.create_task(self._consume_stderr(proc.stderr))

            if proc.stdout is None:
                raise RuntimeError("keybase api-listen stdout pipe is None")
            while self._running:
                line = await proc.stdout.readline()
                if not line:
                    break
                had_activity = True
                event = self._parse_listen_line(line)
                if event:
                    await self.handle_message(event)

            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass

            if stderr_task is not None:
                err_text = await self._drain_stderr_task(stderr_task)
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                await self._terminate_process(proc)
            raise
        finally:
            if proc and not self._running and proc.returncode is None:
                await self._terminate_process(proc)
            if self._listen_proc is proc:
                self._listen_proc = None

        return had_activity, err_text

    async def _listen_loop(self) -> None:
        consecutive_failures = 0

        while self._running:
            try:
                had_activity, err_text = await self._run_listener_process()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Keybase: listener error: %s", exc, exc_info=True)
                had_activity, err_text = False, ""

            if err_text:
                logger.warning("Keybase: listener exited: %s", err_text)

            if had_activity:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            if consecutive_failures >= 6:
                self._set_fatal_error(
                    "keybase_listener_exhausted",
                    "Keybase api-listen failed to restart after 6 consecutive attempts. "
                    "Check that the keybase daemon is running and the user is logged in.",
                    retryable=True,
                )
                break

            await asyncio.sleep(self._compute_restart_delay(consecutive_failures))

    def _parse_listen_line(self, line: bytes) -> MessageEvent | None:
        try:
            payload = json.loads(line.decode(errors="replace"))
        except Exception:
            return None

        msg = payload.get("msg") if isinstance(payload, dict) else None
        if not isinstance(msg, dict):
            return None

        sender = msg.get("sender") or {}
        sender_username = (sender.get("username") or "").strip()
        if not sender_username:
            return None

        # Ignore our own outbound messages to avoid loops.
        if self._username and sender_username.lower() == self._username.lower():
            return None

        text = self._extract_text(msg)
        if not text:
            return None

        channel = msg.get("channel") or {}
        chat_id, chat_type = self._channel_to_chat(channel)
        if not chat_id:
            return None

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_id,
            chat_type=chat_type,
            user_id=sender_username,
            user_name=sender_username,
        )

        ts = msg.get("sent_at")
        timestamp = self._parse_timestamp(ts)

        return MessageEvent(
            source=source,
            text=text,
            message_type=MessageType.TEXT,
            timestamp=timestamp,
        )

    @staticmethod
    def _extract_text(msg: dict[str, Any]) -> str:
        content = msg.get("content") or {}
        ctype = (content.get("type") or "").strip().lower()

        if ctype == "text":
            text_obj = content.get("text") or {}
            return (text_obj.get("body") or "").strip()

        if ctype == "attachment":
            text_obj = content.get("attachment") or {}
            return (text_obj.get("title") or "").strip()

        if ctype == "headline":
            text_obj = content.get("headline") or {}
            return (text_obj.get("headline") or "").strip()

        return ""

    @staticmethod
    def _channel_to_chat(channel: dict[str, Any]) -> tuple[str, str]:
        name = (channel.get("name") or "").strip()
        members_type = (channel.get("members_type") or "").strip().lower()
        topic_name = (channel.get("topic_name") or "").strip()
        conversation_id = (channel.get("conv_id") or channel.get("conversation_id") or "").strip()

        if members_type == "team" and name:
            topic = topic_name or "general"
            return f"{name}#{topic}", "group"

        if members_type in {"impteamnative", "impteamupgrade"}:
            if name and topic_name:
                return f"{name}#{topic_name}", "group"
            if name:
                return name, "group"

        if name:
            return name, "dm"
        if conversation_id:
            return conversation_id, "dm"
        logger.warning("Keybase: received message with no usable channel identifier; dropping")
        return "", "dm"

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        now = datetime.now(tz=timezone.utc)
        try:
            v = float(value)
        except (TypeError, ValueError):
            return now

        # Keybase historically emits microseconds; be resilient to ms/sec.
        if v > 1e14:
            v = v / 1_000_000.0
        elif v > 1e11:
            v = v / 1_000.0

        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (ValueError, OSError):
            return now
