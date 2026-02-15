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

export async function submitGuess(word) {
    const response = await fetch("/guess", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ word })
    });
    const data = await response.json();
    return data;
}
