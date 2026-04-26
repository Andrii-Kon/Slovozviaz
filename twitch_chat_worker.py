#!/usr/bin/env python3
"""Centralized Twitch EventSub chat worker for Slovozviaz.

This worker listens to Twitch chat via EventSub WebSockets and forwards
guesses to the website publish endpoint. The website remains the source of
truth for active games and routing to the correct page/game scope.

Required env vars:
  TWITCH_CLIENT_ID
  TWITCH_BRIDGE_TARGET_URL
  TWITCH_CHAT_BRIDGE_SECRET

Recommended env vars:
  TWITCH_WORKER_CONNECTIONS_URL=https://your-site/api/twitch-worker/connections

Optional:
  TWITCH_EVENTSUB_WEBSOCKET_URL=wss://eventsub.wss.twitch.tv/ws
  TWITCH_CHAT_COMMAND_PREFIX=!guess
  TWITCH_CHAT_ACCEPT_BARE_WORDS=false
  TWITCH_CHAT_ACCEPT_ALL_MESSAGES=false
  TWITCH_WORKER_REFRESH_SECONDS=30
  TWITCH_WORKER_RECONNECT_DELAY_SECONDS=5
  TWITCH_WORKER_PUBLISH_QUEUE_SIZE=2000
  TWITCH_WORKER_PUBLISH_RETRY_COUNT=3
  TWITCH_WORKER_PUBLISH_RETRY_DELAY_SECONDS=1
  TWITCH_EVENTSUB_KEEPALIVE_GRACE_SECONDS=15
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from websocket import (
    WebSocketConnectionClosedException,
    WebSocketTimeoutException,
    create_connection,
)

load_dotenv()

EVENTSUB_WEBSOCKET_URL = os.getenv("TWITCH_EVENTSUB_WEBSOCKET_URL", "wss://eventsub.wss.twitch.tv/ws").strip()
EVENTSUB_SUBSCRIPTIONS_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"

DEFAULT_REFRESH_SECONDS = max(10, int(os.getenv("TWITCH_WORKER_REFRESH_SECONDS", "30")))
DEFAULT_RECONNECT_DELAY_SECONDS = max(1, int(os.getenv("TWITCH_WORKER_RECONNECT_DELAY_SECONDS", "5")))
DEFAULT_PUBLISH_QUEUE_SIZE = max(100, int(os.getenv("TWITCH_WORKER_PUBLISH_QUEUE_SIZE", "2000")))
DEFAULT_PUBLISH_RETRY_COUNT = max(1, int(os.getenv("TWITCH_WORKER_PUBLISH_RETRY_COUNT", "3")))
DEFAULT_PUBLISH_RETRY_DELAY_SECONDS = max(1, int(os.getenv("TWITCH_WORKER_PUBLISH_RETRY_DELAY_SECONDS", "1")))
DEFAULT_KEEPALIVE_GRACE_SECONDS = max(5, int(os.getenv("TWITCH_EVENTSUB_KEEPALIVE_GRACE_SECONDS", "15")))


@dataclass(frozen=True)
class WorkerConnection:
    connection_id: int
    source_url: str
    twitch_user_id: str
    twitch_login: str
    twitch_display_name: str
    access_token: str
    token_scopes: List[str]
    game_scope: str
    page_url: Optional[str]


class EventSubReconnect(Exception):
    def __init__(self, reconnect_url: str):
        super().__init__("EventSub requested reconnect")
        self.reconnect_url = reconnect_url


def env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_channel(value: str) -> str:
    return value.strip().lower().lstrip("#")


def extract_guess_word(
    message: str,
    command_prefix: str,
    accept_bare_words: bool,
    accept_all_messages: bool,
) -> Optional[str]:
    text = message.strip()
    if not text:
        return None

    if accept_all_messages:
        parts = text.split()
        if not parts:
            return None
        if parts[0].startswith(("http://", "https://")):
            return None
        return parts[0].strip().lower()

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


def derive_connections_url() -> str:
    explicit_url = (os.getenv("TWITCH_WORKER_CONNECTIONS_URL") or "").strip()
    if explicit_url:
        return explicit_url

    legacy_url = (os.getenv("TWITCH_WORKER_CHANNELS_URL") or "").strip()
    if legacy_url.endswith("/api/twitch-worker/channels"):
        return legacy_url[: -len("/channels")] + "/connections"
    if legacy_url:
        return legacy_url
    return ""


def parse_url_list(raw_value: str) -> List[str]:
    parts = [part.strip() for part in raw_value.replace("\n", ",").split(",")]
    return [part for part in parts if part]


def http_json_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    json_payload: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    data = None
    request_headers = dict(headers or {})
    if json_payload is not None:
        data = json.dumps(json_payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, method=method, headers=request_headers, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return json.loads(body or "{}")


def load_active_connections_from_source(connections_url: str, secret: str) -> List[WorkerConnection]:
    req = urllib.request.Request(
        connections_url,
        headers={
            "Accept": "application/json",
            "X-Twitch-Bridge-Secret": secret,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read().decode("utf-8")
        payload = json.loads(body or "{}")

    skipped = payload.get("skipped")
    if isinstance(skipped, list):
        for item in skipped:
            if not isinstance(item, dict):
                continue
            login = item.get("twitch_login") or "unknown"
            reason = item.get("reason") or "unknown"
            if reason == "missing_scopes":
                missing_scopes = ", ".join(item.get("missing_scopes") or [])
                print(f"[worker] skipped @{login}: reconnect Twitch with scopes [{missing_scopes}]")
            elif reason == "token_refresh_failed":
                print(f"[worker] skipped @{login}: token refresh failed")

    connections = payload.get("connections")
    if not isinstance(connections, list):
        return []

    parsed: List[WorkerConnection] = []
    for item in connections:
        if not isinstance(item, dict):
            continue
        try:
            parsed.append(
                WorkerConnection(
                    connection_id=int(item.get("connection_id")),
                    source_url=connections_url,
                    twitch_user_id=str(item.get("twitch_user_id") or "").strip(),
                    twitch_login=normalize_channel(str(item.get("twitch_login") or "")),
                    twitch_display_name=str(item.get("twitch_display_name") or "").strip(),
                    access_token=str(item.get("access_token") or "").strip(),
                    token_scopes=[
                        str(scope).strip()
                        for scope in (item.get("token_scopes") or [])
                        if str(scope).strip()
                    ],
                    game_scope=str(item.get("game_scope") or "").strip(),
                    page_url=str(item.get("page_url") or "").strip() or None,
                )
            )
        except Exception:
            continue

    return [
        item
        for item in parsed
        if item.twitch_user_id and item.twitch_login and item.access_token
    ]


def load_active_connections(connections_urls: List[str], secret: str) -> List[WorkerConnection]:
    combined: List[WorkerConnection] = []
    seen_keys: set[str] = set()

    for connections_url in connections_urls:
        try:
            source_connections = load_active_connections_from_source(connections_url, secret)
        except Exception as exc:
            print(f"[worker] failed to load connections from {connections_url}: {exc}")
            continue

        for connection in source_connections:
            dedupe_key = connection.twitch_user_id or f"{connection.source_url}:{connection.connection_id}"
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            combined.append(connection)

    return combined


def publish_guess(
    target_url: str,
    secret: str,
    channel: str,
    user_login: str,
    user_name: str,
    message: str,
    word: str,
    message_id: str,
) -> None:
    payload = {
        "channel": channel,
        "user_login": user_login,
        "user_name": user_name,
        "message": message,
        "word": word,
        "message_id": message_id,
    }
    request = urllib.request.Request(
        target_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Twitch-Bridge-Secret": secret,
        },
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read().decode("utf-8")
        if response.status not in {200, 202}:
            raise RuntimeError(f"Unexpected response {response.status}: {body}")

        data = json.loads(body or "{}")
        if not data.get("accepted", False):
            reason = data.get("reason", "unknown")
            print(f"[worker] skipped '{word}' for #{channel} ({reason})")
            return

        if data.get("duplicate"):
            print(f"[worker] duplicate '{word}' from @{user_login} for #{channel}")
            return

        resolved_scope = data.get("game_scope") or "active-page-scope"
        print(f"[worker] published '{word}' from @{user_login} to #{channel} / {resolved_scope}")


def publisher_loop(
    publish_queue: "queue.Queue[dict]",
    retry_count: int,
    retry_delay_seconds: int,
) -> None:
    retryable_statuses = {429, 500, 502, 503, 504}

    while True:
        payload = publish_queue.get()
        try:
            target_urls = payload.pop("target_urls")
            for target_url in target_urls:
                target_payload = dict(payload)
                target_payload["target_url"] = target_url
                for attempt in range(1, retry_count + 1):
                    try:
                        publish_guess(**target_payload)
                        break
                    except urllib.error.HTTPError as exc:
                        body = exc.read().decode("utf-8", errors="ignore")
                        if exc.code in retryable_statuses and attempt < retry_count:
                            print(
                                f"[worker] publish retry {attempt}/{retry_count} "
                                f"for {target_url} after HTTP {exc.code}"
                            )
                            time.sleep(retry_delay_seconds * attempt)
                            continue
                        print(f"[worker] publish failed for {target_url} with HTTP {exc.code}: {body}")
                        break
                    except urllib.error.URLError as exc:
                        if attempt < retry_count:
                            print(
                                f"[worker] publish retry {attempt}/{retry_count} "
                                f"for {target_url} after URL error: {exc}"
                            )
                            time.sleep(retry_delay_seconds * attempt)
                            continue
                        print(f"[worker] publish failed for {target_url}: {exc}")
                        break
                    except Exception as exc:
                        if attempt < retry_count:
                            print(
                                f"[worker] publish retry {attempt}/{retry_count} "
                                f"for {target_url} after error: {exc}"
                            )
                            time.sleep(retry_delay_seconds * attempt)
                            continue
                        print(f"[worker] unexpected publish error for {target_url}: {exc}")
                        break
        finally:
            publish_queue.task_done()


def connect_eventsub_session(ws_url: str):
    return create_connection(ws_url, timeout=20, enable_multithread=True)


def wait_for_welcome(ws) -> Dict[str, Any]:
    deadline = time.time() + 15
    while time.time() < deadline:
        raw_message = ws.recv()
        if not raw_message:
            continue
        frame = json.loads(raw_message)
        metadata = frame.get("metadata") or {}
        if metadata.get("message_type") != "session_welcome":
            continue
        payload = frame.get("payload") or {}
        session = payload.get("session") or {}
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            raise RuntimeError("EventSub welcome payload missing session id")
        return frame
    raise RuntimeError("Timed out waiting for EventSub session_welcome")


def create_chat_subscription(client_id: str, session_id: str, connection: WorkerConnection) -> str:
    payload = {
        "type": "channel.chat.message",
        "version": "1",
        "condition": {
            "broadcaster_user_id": connection.twitch_user_id,
            "user_id": connection.twitch_user_id,
        },
        "transport": {
            "method": "websocket",
            "session_id": session_id,
        },
    }
    response = http_json_request(
        EVENTSUB_SUBSCRIPTIONS_URL,
        method="POST",
        headers={
            "Authorization": f"Bearer {connection.access_token}",
            "Client-Id": client_id,
        },
        json_payload=payload,
        timeout=20,
    )
    data = response.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise RuntimeError("EventSub subscription response did not contain subscription data")

    subscription_id = str(data[0].get("id") or "").strip()
    subscription_status = str(data[0].get("status") or "").strip()
    if subscription_status and subscription_status not in {"enabled", "webhook_callback_verification_pending"}:
        raise RuntimeError(
            f"Unexpected EventSub subscription status for @{connection.twitch_login}: {subscription_status}"
        )
    if not subscription_id:
        raise RuntimeError("EventSub subscription response missing id")
    return subscription_id


def enqueue_notification(
    frame: Dict[str, Any],
    publish_queue: "queue.Queue[dict]",
    target_urls: List[str],
    secret: str,
    command_prefix: str,
    accept_bare_words: bool,
    accept_all_messages: bool,
) -> None:
    payload = frame.get("payload") or {}
    event = payload.get("event") or {}
    subscription = payload.get("subscription") or {}

    if subscription.get("type") != "channel.chat.message":
        return

    channel = normalize_channel(str(event.get("broadcaster_user_login") or ""))
    user_login = normalize_channel(str(event.get("chatter_user_login") or ""))
    user_name = str(event.get("chatter_user_name") or user_login or "chat").strip()
    message_id = str(event.get("message_id") or "").strip()
    message_obj = event.get("message") or {}
    message = str(message_obj.get("text") or "").strip()
    guess_word = extract_guess_word(
        message,
        command_prefix,
        accept_bare_words,
        accept_all_messages,
    )
    if not channel or not user_login or not guess_word:
        return

    publish_payload = {
        "target_urls": target_urls,
        "secret": secret,
        "channel": channel,
        "user_login": user_login,
        "user_name": user_name,
        "message": message,
        "word": guess_word,
        "message_id": message_id,
    }
    try:
        publish_queue.put_nowait(publish_payload)
    except queue.Full:
        print(
            f"[worker] publish queue full; dropped {guess_word!r} "
            f"from @{user_login} for #{channel}"
        )


def connection_signature(connection: WorkerConnection) -> str:
    return (
        f"{connection.source_url}:{connection.connection_id}:{connection.twitch_user_id}:"
        f"{connection.twitch_login}:{connection.access_token}"
    )


class EventSubConnectionWorker(threading.Thread):
    def __init__(
        self,
        connection: WorkerConnection,
        client_id: str,
        target_urls: List[str],
        secret: str,
        command_prefix: str,
        accept_bare_words: bool,
        accept_all_messages: bool,
        reconnect_delay_seconds: int,
        keepalive_grace_seconds: int,
        publish_queue: "queue.Queue[dict]",
    ) -> None:
        super().__init__(
            name=f"twitch-eventsub-{connection.twitch_login}",
            daemon=True,
        )
        self.connection = connection
        self.client_id = client_id
        self.target_urls = target_urls
        self.secret = secret
        self.command_prefix = command_prefix
        self.accept_bare_words = accept_bare_words
        self.accept_all_messages = accept_all_messages
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.keepalive_grace_seconds = keepalive_grace_seconds
        self.publish_queue = publish_queue
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def _sleep_or_stop(self, seconds: int) -> bool:
        return self.stop_event.wait(max(0, seconds))

    def run(self) -> None:
        next_ws_url = EVENTSUB_WEBSOCKET_URL

        while not self.stop_event.is_set():
            websocket_conn = None
            try:
                print(f"[worker] connecting to EventSub for #{self.connection.twitch_login}")
                websocket_conn = connect_eventsub_session(next_ws_url)
                welcome_frame = wait_for_welcome(websocket_conn)
                session = (welcome_frame.get("payload") or {}).get("session") or {}
                session_id = str(session.get("id") or "").strip()
                keepalive_timeout_seconds = int(session.get("keepalive_timeout_seconds") or 10)
                websocket_conn.settimeout(
                    max(10, keepalive_timeout_seconds + self.keepalive_grace_seconds)
                )
                next_ws_url = EVENTSUB_WEBSOCKET_URL

                subscription_id = create_chat_subscription(
                    self.client_id,
                    session_id,
                    self.connection,
                )
                print(
                    f"[worker] subscribed #{self.connection.twitch_login} "
                    f"(subscription {subscription_id})"
                )

                last_message_at = time.time()
                while not self.stop_event.is_set():
                    try:
                        raw_message = websocket_conn.recv()
                    except WebSocketTimeoutException:
                        if time.time() - last_message_at > (
                            keepalive_timeout_seconds + self.keepalive_grace_seconds
                        ):
                            raise ConnectionError("EventSub keepalive timeout")
                        raw_message = None

                    if not raw_message:
                        continue

                    last_message_at = time.time()
                    frame = json.loads(raw_message)
                    metadata = frame.get("metadata") or {}
                    message_type = str(metadata.get("message_type") or "").strip()

                    if message_type == "session_keepalive":
                        continue

                    if message_type == "notification":
                        enqueue_notification(
                            frame,
                            self.publish_queue,
                            self.target_urls,
                            self.secret,
                            self.command_prefix,
                            self.accept_bare_words,
                            self.accept_all_messages,
                        )
                        continue

                    if message_type == "session_reconnect":
                        session_payload = (frame.get("payload") or {}).get("session") or {}
                        reconnect_url = str(session_payload.get("reconnect_url") or "").strip()
                        if reconnect_url:
                            raise EventSubReconnect(reconnect_url)
                        raise ConnectionError("EventSub requested reconnect without reconnect_url")

                    if message_type == "revocation":
                        subscription = (frame.get("payload") or {}).get("subscription") or {}
                        print(
                            "[worker] subscription revoked for "
                            f"#{self.connection.twitch_login}: "
                            f"{subscription.get('type')} / {subscription.get('status')}"
                        )
                        raise ConnectionError("EventSub subscription revoked")

            except EventSubReconnect as exc:
                print(f"[worker] EventSub requested reconnect for #{self.connection.twitch_login}")
                next_ws_url = exc.reconnect_url
                continue
            except KeyboardInterrupt:
                return
            except (WebSocketConnectionClosedException, ConnectionError) as exc:
                print(f"[worker] connection error for #{self.connection.twitch_login}: {exc}")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                print(
                    f"[worker] HTTP error for #{self.connection.twitch_login}: "
                    f"{exc.code} {body}"
                )
            except Exception as exc:
                print(f"[worker] unexpected error for #{self.connection.twitch_login}: {exc}")
            finally:
                if websocket_conn is not None:
                    try:
                        websocket_conn.close()
                    except Exception:
                        pass

            if self.stop_event.is_set():
                break

            print(
                f"[worker] reconnecting #{self.connection.twitch_login} "
                f"in {self.reconnect_delay_seconds}s..."
            )
            stopped = self._sleep_or_stop(self.reconnect_delay_seconds)
            if stopped:
                break
            next_ws_url = EVENTSUB_WEBSOCKET_URL


def run_worker() -> None:
    client_id = (os.getenv("TWITCH_CLIENT_ID") or "").strip()
    raw_target_url = (os.getenv("TWITCH_BRIDGE_TARGET_URL") or "").strip()
    secret = (os.getenv("TWITCH_CHAT_BRIDGE_SECRET") or "").strip()
    connections_url = derive_connections_url()
    target_urls = parse_url_list(raw_target_url)
    connections_urls = parse_url_list(connections_url)
    command_prefix = (os.getenv("TWITCH_CHAT_COMMAND_PREFIX") or "!guess").strip()
    accept_bare_words = env_flag("TWITCH_CHAT_ACCEPT_BARE_WORDS", default=False)
    accept_all_messages = env_flag("TWITCH_CHAT_ACCEPT_ALL_MESSAGES", default=False)
    refresh_seconds = DEFAULT_REFRESH_SECONDS
    reconnect_delay_seconds = DEFAULT_RECONNECT_DELAY_SECONDS
    publish_queue_size = DEFAULT_PUBLISH_QUEUE_SIZE
    publish_retry_count = DEFAULT_PUBLISH_RETRY_COUNT
    publish_retry_delay_seconds = DEFAULT_PUBLISH_RETRY_DELAY_SECONDS
    keepalive_grace_seconds = DEFAULT_KEEPALIVE_GRACE_SECONDS

    missing = [
        key
        for key, value in [
            ("TWITCH_CLIENT_ID", client_id),
            ("TWITCH_BRIDGE_TARGET_URL", ", ".join(target_urls)),
            ("TWITCH_CHAT_BRIDGE_SECRET", secret),
            ("TWITCH_WORKER_CONNECTIONS_URL", ", ".join(connections_urls)),
        ]
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")

    publish_queue: "queue.Queue[dict]" = queue.Queue(maxsize=publish_queue_size)
    publisher = threading.Thread(
        target=publisher_loop,
        args=(publish_queue, publish_retry_count, publish_retry_delay_seconds),
        name="twitch-eventsub-publisher",
        daemon=True,
    )
    publisher.start()
    active_workers: Dict[str, EventSubConnectionWorker] = {}
    active_signatures: Dict[str, str] = {}
    reported_no_connections = False

    try:
        while True:
            desired_connections = load_active_connections(connections_urls, secret)
            desired_by_id = {
                (connection.twitch_user_id or f"{connection.source_url}:{connection.connection_id}"): connection
                for connection in desired_connections
            }

            if not desired_by_id:
                if not reported_no_connections:
                    print("[worker] no active EventSub-ready Twitch connections yet; waiting...")
                    reported_no_connections = True
            else:
                reported_no_connections = False

            removed_ids = set(active_workers) - set(desired_by_id)
            for connection_id in sorted(removed_ids):
                worker = active_workers.pop(connection_id)
                active_signatures.pop(connection_id, None)
                print(f"[worker] stopping EventSub session for #{worker.connection.twitch_login}")
                worker.stop()
                worker.join(timeout=5)

            for connection_id, connection in desired_by_id.items():
                desired_signature = connection_signature(connection)
                current_worker = active_workers.get(connection_id)
                current_signature = active_signatures.get(connection_id)

                if current_worker and current_signature == desired_signature and current_worker.is_alive():
                    continue

                if current_worker:
                    print(f"[worker] restarting EventSub session for #{current_worker.connection.twitch_login}")
                    current_worker.stop()
                    current_worker.join(timeout=5)

                worker = EventSubConnectionWorker(
                    connection=connection,
                    client_id=client_id,
                    target_urls=target_urls,
                    secret=secret,
                    command_prefix=command_prefix,
                    accept_bare_words=accept_bare_words,
                    accept_all_messages=accept_all_messages,
                    reconnect_delay_seconds=reconnect_delay_seconds,
                    keepalive_grace_seconds=keepalive_grace_seconds,
                    publish_queue=publish_queue,
                )
                active_workers[connection_id] = worker
                active_signatures[connection_id] = desired_signature
                worker.start()

            time.sleep(refresh_seconds)
    except KeyboardInterrupt:
        print("\n[worker] stopped by user")
    finally:
        for worker in active_workers.values():
            worker.stop()
        for worker in active_workers.values():
            worker.join(timeout=5)


if __name__ == "__main__":
    run_worker()
