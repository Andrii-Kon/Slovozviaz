#!/usr/bin/env python3
"""Centralized Twitch chat worker for Slovozviaz.

This worker uses one shared Twitch bot account to join every active streamer
channel that connected through the website OAuth flow. Chat guesses are sent
to the website publish endpoint, which then routes them to the currently
active game for that channel.

Required env vars:
  TWITCH_BOT_USERNAME
  TWITCH_OAUTH_TOKEN
  TWITCH_BRIDGE_TARGET_URL
  TWITCH_CHAT_BRIDGE_SECRET

Optional:
  TWITCH_WORKER_CHANNELS_URL=https://your-site/api/twitch-worker/channels
  TWITCH_CHAT_COMMAND_PREFIX=!guess
  TWITCH_CHAT_ACCEPT_BARE_WORDS=false
  TWITCH_WORKER_REFRESH_SECONDS=30
  TWITCH_WORKER_RECONNECT_DELAY_SECONDS=5
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Dict, Optional, Set

from dotenv import load_dotenv

from app import app, db, TwitchConnection

load_dotenv()

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
PRIVMSG_RE = re.compile(
    r"^(?:@(?P<tags>[^ ]+) )?:(?P<prefix>[^ ]+) PRIVMSG #(?P<channel>[^ ]+) :(?P<message>.*)$"
)


def env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_channel(value: str) -> str:
    return value.strip().lower().lstrip("#")


def normalize_token(value: str) -> str:
    token = value.strip()
    if token.startswith("oauth:"):
        return token
    return f"oauth:{token}"


def parse_tags(raw_tags: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    if not raw_tags:
        return parsed

    for item in raw_tags.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


def extract_guess_word(message: str, command_prefix: str, accept_bare_words: bool) -> Optional[str]:
    text = message.strip()
    if not text:
        return None

    normalized_prefix = command_prefix.strip()
    if normalized_prefix:
        lowered_text = text.lower()
        lowered_prefix = normalized_prefix.lower()
        if lowered_text.startswith(f"{lowered_prefix} "):
            parts = text[len(normalized_prefix):].strip().split()
            if len(parts) == 1:
                return parts[0].strip().lower()
            return None
        if lowered_text == lowered_prefix:
            return None

    if not accept_bare_words:
        return None

    parts = text.split()
    if len(parts) != 1:
        return None

    if parts[0].startswith(("http://", "https://")):
        return None

    return parts[0].strip().lower()


def send_irc_line(sock: ssl.SSLSocket, line: str) -> None:
    sock.sendall(f"{line}\r\n".encode("utf-8"))


def publish_guess(
    target_url: str,
    secret: str,
    channel: str,
    user_login: str,
    user_name: str,
    message: str,
    word: str,
) -> None:
    payload = json.dumps({
        "channel": channel,
        "user_login": user_login,
        "user_name": user_name,
        "message": message,
        "word": word,
    }).encode("utf-8")

    request = urllib.request.Request(
        target_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Twitch-Bridge-Secret": secret,
        },
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
        if response.status not in {200, 202}:
            raise RuntimeError(f"Unexpected response {response.status}: {body}")

        data = json.loads(body or "{}")
        if not data.get("accepted", False):
            reason = data.get("reason", "unknown")
            print(f"[worker] skipped '{word}' for #{channel} ({reason})")
            return

        resolved_scope = data.get("game_scope") or "active-page-scope"
        print(f"[worker] published '{word}' from @{user_login} to #{channel} / {resolved_scope}")


def load_active_channels() -> Set[str]:
    channels_url = (os.getenv("TWITCH_WORKER_CHANNELS_URL") or "").strip()
    secret = (os.getenv("TWITCH_CHAT_BRIDGE_SECRET") or "").strip()
    if channels_url:
        req = urllib.request.Request(
            channels_url,
            headers={
                "Accept": "application/json",
                "X-Twitch-Bridge-Secret": secret,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8")
            payload = json.loads(body or "{}")
            channels = payload.get("channels")
            if not isinstance(channels, list):
                return set()
            return {normalize_channel(str(channel)) for channel in channels if str(channel).strip()}

    with app.app_context():
        rows = (
            TwitchConnection.query
            .filter_by(is_active=True)
            .with_entities(TwitchConnection.twitch_login)
            .all()
        )
        return {normalize_channel(row[0]) for row in rows if row and row[0]}


def run_worker() -> None:
    username = normalize_channel(os.getenv("TWITCH_BOT_USERNAME") or "")
    oauth_token = normalize_token(os.getenv("TWITCH_OAUTH_TOKEN") or "")
    target_url = (os.getenv("TWITCH_BRIDGE_TARGET_URL") or "").strip()
    secret = (os.getenv("TWITCH_CHAT_BRIDGE_SECRET") or "").strip()
    command_prefix = (os.getenv("TWITCH_CHAT_COMMAND_PREFIX") or "!guess").strip()
    accept_bare_words = env_flag("TWITCH_CHAT_ACCEPT_BARE_WORDS", default=False)
    refresh_seconds = max(10, int(os.getenv("TWITCH_WORKER_REFRESH_SECONDS", "30")))
    reconnect_delay_seconds = max(1, int(os.getenv("TWITCH_WORKER_RECONNECT_DELAY_SECONDS", "5")))

    missing = [
        name
        for name, value in [
            ("TWITCH_BOT_USERNAME", username),
            ("TWITCH_OAUTH_TOKEN", oauth_token),
            ("TWITCH_BRIDGE_TARGET_URL", target_url),
            ("TWITCH_CHAT_BRIDGE_SECRET", secret),
        ]
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")

    ssl_context = ssl.create_default_context()

    while True:
        try:
            channels = load_active_channels()
            if not channels:
                print("[worker] no active Twitch connections yet; waiting...")
                time.sleep(refresh_seconds)
                continue

            print(f"[worker] connecting as @{username}; channels: {', '.join(sorted(channels))}")
            raw_socket = socket.create_connection((IRC_HOST, IRC_PORT), timeout=15)
            with ssl_context.wrap_socket(raw_socket, server_hostname=IRC_HOST) as sock:
                sock.settimeout(60)
                send_irc_line(sock, "CAP REQ :twitch.tv/tags twitch.tv/commands")
                send_irc_line(sock, f"PASS {oauth_token}")
                send_irc_line(sock, f"NICK {username}")
                for channel in sorted(channels):
                    send_irc_line(sock, f"JOIN #{channel}")

                joined_channels = set(channels)
                last_refresh_at = time.time()
                authenticated = False
                buffer = ""

                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        if not authenticated:
                            raise ConnectionError("Twitch IRC closed the connection before authentication completed.")
                        raise ConnectionError("Twitch IRC connection closed.")

                    buffer += chunk.decode("utf-8", errors="ignore")
                    while "\r\n" in buffer:
                        line, buffer = buffer.split("\r\n", 1)
                        if not line:
                            continue

                        if " NOTICE * :Login authentication failed" in line:
                            raise ConnectionError("Twitch IRC login failed. Regenerate the shared bot token with chat:read.")
                        if " NOTICE * :Improperly formatted auth" in line:
                            raise ConnectionError("Twitch IRC rejected the PASS/NICK authentication format.")
                        if line.startswith(":tmi.twitch.tv 001 "):
                            authenticated = True

                        if line.startswith("PING "):
                            send_irc_line(sock, line.replace("PING", "PONG", 1))
                            continue

                        match = PRIVMSG_RE.match(line)
                        if not match:
                            continue

                        channel = normalize_channel(match.group("channel") or "")
                        tags = parse_tags(match.group("tags") or "")
                        user_login = match.group("prefix").split("!", 1)[0].strip().lower()
                        user_name = (tags.get("display-name") or user_login or "chat").strip()
                        message = match.group("message").strip()
                        guess_word = extract_guess_word(message, command_prefix, accept_bare_words)
                        if not guess_word:
                            continue

                        try:
                            publish_guess(
                                target_url=target_url,
                                secret=secret,
                                channel=channel,
                                user_login=user_login,
                                user_name=user_name,
                                message=message,
                                word=guess_word,
                            )
                        except urllib.error.HTTPError as exc:
                            body = exc.read().decode("utf-8", errors="ignore")
                            print(f"[worker] publish failed with HTTP {exc.code}: {body}")
                        except urllib.error.URLError as exc:
                            print(f"[worker] publish failed: {exc}")
                        except Exception as exc:
                            print(f"[worker] unexpected publish error: {exc}")

                    if time.time() - last_refresh_at < refresh_seconds:
                        continue

                    latest_channels = load_active_channels()
                    added_channels = sorted(latest_channels - joined_channels)
                    removed_channels = sorted(joined_channels - latest_channels)
                    for channel in added_channels:
                        send_irc_line(sock, f"JOIN #{channel}")
                        print(f"[worker] joined #{channel}")
                    for channel in removed_channels:
                        send_irc_line(sock, f"PART #{channel}")
                        print(f"[worker] left #{channel}")

                    joined_channels = set(latest_channels)
                    last_refresh_at = time.time()

        except KeyboardInterrupt:
            print("\n[worker] stopped by user")
            return
        except Exception as exc:
            print(f"[worker] connection error: {exc}")
            print(f"[worker] reconnecting in {reconnect_delay_seconds}s...")
            time.sleep(reconnect_delay_seconds)


if __name__ == "__main__":
    run_worker()
