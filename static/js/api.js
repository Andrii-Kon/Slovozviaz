// static/js/api.js
export async function fetchRankedWords(date = null) {
    const url = date ? `/ranked?date=${encodeURIComponent(date)}` : "/ranked";
    const response = await fetch(url);
    const data = await response.json();
    return data;
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
