#!/usr/bin/env python3
"""Bridge Twitch chat messages into the Slovozviaz website.

Required environment variables:
  TWITCH_BOT_USERNAME
  TWITCH_OAUTH_TOKEN
  TWITCH_CHANNEL
  TWITCH_BRIDGE_TARGET_URL
  TWITCH_CHAT_BRIDGE_SECRET

Optional:
  TWITCH_GAME_URL=https://andriikon.pythonanywhere.com/?game=...
  TWITCH_GAME_SCOPE=custom:...
  TWITCH_CHAT_COMMAND_PREFIX=!guess
  TWITCH_CHAT_ACCEPT_BARE_WORDS=false
  TWITCH_BRIDGE_RECONNECT_DELAY_SECONDS=5
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
from dotenv import load_dotenv
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

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


def normalize_game_scope(value: str) -> str:
    return re.sub(r"[^a-z0-9:_-]", "", value.strip().lower())[:160]


def derive_game_scope_from_url(game_url: str) -> str:
    parsed = urlparse(game_url.strip())
    params = parse_qs(parsed.query)

    raw_game_id = (params.get("game") or [""])[0].strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", raw_game_id):
        return f"custom:{raw_game_id}"

    raw_date = (params.get("date") or [""])[0].strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
        return f"date:{raw_date}"

    return "daily:current"


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
    game_scope: str,
    user_login: str,
    user_name: str,
    message: str,
    word: str,
) -> None:
    payload_dict = {
        "channel": channel,
        "user_login": user_login,
        "user_name": user_name,
        "message": message,
        "word": word,
    }
    if game_scope:
        payload_dict["game_scope"] = game_scope

    payload = json.dumps(payload_dict).encode("utf-8")

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
            print(f"[bridge] skipped word '{word}' ({reason})")
            return

        resolved_scope = data.get("game_scope") or game_scope or "active-page-scope"
        print(f"[bridge] published '{word}' from @{user_login} to {resolved_scope}")


def run_bridge() -> None:
    username = (os.getenv("TWITCH_BOT_USERNAME") or "").strip().lower()
    oauth_token = normalize_token(os.getenv("TWITCH_OAUTH_TOKEN") or "")
    channel = normalize_channel(os.getenv("TWITCH_CHANNEL") or "")
    target_url = (os.getenv("TWITCH_BRIDGE_TARGET_URL") or "").strip()
    secret = (os.getenv("TWITCH_CHAT_BRIDGE_SECRET") or "").strip()
    raw_game_url = (os.getenv("TWITCH_GAME_URL") or "").strip()
    raw_game_scope = (os.getenv("TWITCH_GAME_SCOPE") or "").strip()
    command_prefix = (os.getenv("TWITCH_CHAT_COMMAND_PREFIX") or "!guess").strip()
    accept_bare_words = env_flag("TWITCH_CHAT_ACCEPT_BARE_WORDS", default=False)
    reconnect_delay_seconds = max(1, int(os.getenv("TWITCH_BRIDGE_RECONNECT_DELAY_SECONDS", "5")))
    game_scope = normalize_game_scope(raw_game_scope)
    if not game_scope and raw_game_url:
        game_scope = normalize_game_scope(derive_game_scope_from_url(raw_game_url))
    if not game_scope:
        game_scope = ""

    missing = [
        name
        for name, value in [
            ("TWITCH_BOT_USERNAME", username),
            ("TWITCH_OAUTH_TOKEN", oauth_token),
            ("TWITCH_CHANNEL", channel),
            ("TWITCH_BRIDGE_TARGET_URL", target_url),
            ("TWITCH_CHAT_BRIDGE_SECRET", secret),
        ]
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")

    print(f"[bridge] listening to #{channel}")
    print(f"[bridge] command prefix: {command_prefix or '(none)'} | bare words: {accept_bare_words}")
    print(f"[bridge] target: {target_url}")
    print(f"[bridge] game scope: {game_scope or 'resolved by active Twitch page'}")

    ssl_context = ssl.create_default_context()

    while True:
        try:
            print("[bridge] connecting to Twitch IRC...")
            raw_socket = socket.create_connection((IRC_HOST, IRC_PORT), timeout=15)
            with ssl_context.wrap_socket(raw_socket, server_hostname=IRC_HOST) as sock:
                sock.settimeout(60)
                send_irc_line(sock, "CAP REQ :twitch.tv/tags twitch.tv/commands")
                send_irc_line(sock, f"PASS {oauth_token}")
                send_irc_line(sock, f"NICK {username}")
                send_irc_line(sock, f"JOIN #{channel}")

                buffer = ""
                authenticated = False
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
                            raise ConnectionError(
                                "Twitch IRC login failed. Regenerate the token with the IRC scope chat:read."
                            )
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
                                game_scope=game_scope,
                                user_login=user_login,
                                user_name=user_name,
                                message=message,
                                word=guess_word,
                            )
                        except urllib.error.HTTPError as exc:
                            body = exc.read().decode("utf-8", errors="ignore")
                            print(f"[bridge] publish failed with HTTP {exc.code}: {body}")
                        except urllib.error.URLError as exc:
                            print(f"[bridge] publish failed: {exc}")
                        except Exception as exc:
                            print(f"[bridge] unexpected publish error: {exc}")

        except KeyboardInterrupt:
            print("\n[bridge] stopped by user")
            return
        except Exception as exc:
            print(f"[bridge] connection error: {exc}")
            print(f"[bridge] reconnecting in {reconnect_delay_seconds}s...")
            time.sleep(reconnect_delay_seconds)


if __name__ == "__main__":
    run_bridge()
