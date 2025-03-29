export async function fetchRankedWords() {
    const response = await fetch("/ranked");
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
