// static/js/api.js
export async function fetchRankedWords(date = null) {
    const url = date ? `/ranked?date=${encodeURIComponent(date)}` : "/ranked";
    const response = await fetch(url);
    const data = await response.json();
    return data;
}

export async function fetchRankedWordsByWord(word) {
    const url = `/api/ranked-by-word?word=${encodeURIComponent(word)}`;
    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
}

export async function fetchRankedWordsByGameId(gameId) {
    const url = `/api/ranked-by-game?game=${encodeURIComponent(gameId)}`;
    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
}

export async function normalizeWordToKnownLemma(word) {
    const url = `/api/normalize-word?word=${encodeURIComponent(word)}`;
    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
}

export async function fetchTwitchConnectionStatus(next = null) {
    const params = new URLSearchParams();
    if (next) params.set("next", next);
    const url = params.toString()
        ? `/api/twitch-connection/status?${params.toString()}`
        : "/api/twitch-connection/status";

    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
}

export async function disconnectTwitchConnection() {
    const response = await fetch("/api/twitch-connection/disconnect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store"
    });
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
}

export async function fetchTwitchChatStatus(channel = null, gameScope = null) {
    const params = new URLSearchParams();
    if (channel) params.set("channel", channel);
    if (gameScope) params.set("game_scope", gameScope);

    const url = params.toString()
        ? `/api/twitch-chat/status?${params.toString()}`
        : "/api/twitch-chat/status";

    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
}

export async function registerTwitchChatTarget(channel, gameScope, pageUrl = null) {
    const response = await fetch("/api/twitch-chat/target", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
            channel,
            game_scope: gameScope,
            page_url: pageUrl
        })
    });

    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
}

export async function fetchTwitchChatEvents(afterId = 0, channel = null, gameScope = null, limit = null) {
    const params = new URLSearchParams();
    params.set("after_id", String(Math.max(0, Number(afterId) || 0)));
    if (channel) params.set("channel", channel);
    if (gameScope) params.set("game_scope", gameScope);
    if (Number.isFinite(limit) && limit > 0) params.set("limit", String(limit));

    const response = await fetch(`/api/twitch-chat/events?${params.toString()}`, { cache: "no-store" });
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
}

export async function fetchTwitchChatSolvers(channel, limit = 50) {
    const params = new URLSearchParams();
    if (channel) params.set("channel", channel);
    if (Number.isFinite(limit) && limit > 0) params.set("limit", String(limit));

    const response = await fetch(`/api/twitch-chat/solvers?${params.toString()}`, { cache: "no-store" });
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
}

export async function submitGuess(word) {
    const response = await fetch("/guess", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ word })
    });
    const data = await response.json();
    return data;
}
