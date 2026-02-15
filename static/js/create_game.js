function normalizeWord(value) {
    return (value || "").trim().toLowerCase();
}

function formatSimilarityPercent(value) {
    const num = Number(value);
    if (Number.isNaN(num)) return "—";
    return (num * 100).toFixed(2);
}

async function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
    }

    const tmp = document.createElement("textarea");
    tmp.value = text;
    tmp.style.position = "fixed";
    tmp.style.left = "-9999px";
    document.body.appendChild(tmp);
    tmp.focus();
    tmp.select();
    let copied = false;
    try {
        copied = document.execCommand("copy");
    } finally {
        document.body.removeChild(tmp);
    }
    return copied;
}

function renderTopWords(tbody, ranking) {
    tbody.innerHTML = "";
    const top500 = (Array.isArray(ranking) ? ranking : []).slice(0, 500);

    for (const item of top500) {
        const tr = document.createElement("tr");

        const rankTd = document.createElement("td");
        rankTd.textContent = String(item.rank ?? "");

        const wordTd = document.createElement("td");
        wordTd.textContent = String(item.word ?? "");

        const similarityTd = document.createElement("td");
        similarityTd.textContent = formatSimilarityPercent(item.similarity);

        tr.appendChild(rankTd);
        tr.appendChild(wordTd);
        tr.appendChild(similarityTd);
        tbody.appendChild(tr);
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("createGameForm");
    const input = document.getElementById("createGameWordInput");
    const submitBtn = document.getElementById("createGameSubmitBtn");
    const errorEl = document.getElementById("createGameError");
    const resultSection = document.getElementById("createGameResult");
    const linkInput = document.getElementById("createGameLinkInput");
    const copyBtn = document.getElementById("copyGameLinkBtn");
    const copyStatus = document.getElementById("copyGameLinkStatus");
    const openLink = document.getElementById("openCreatedGameLink");
    const topWordsBody = document.getElementById("createGameTopWordsBody");

    if (
        !form || !input || !submitBtn || !errorEl ||
        !resultSection || !linkInput || !copyBtn ||
        !copyStatus || !openLink || !topWordsBody
    ) {
        return;
    }

    const showError = (message) => {
        errorEl.textContent = message;
        errorEl.classList.remove("hidden");
    };

    const hideError = () => {
        errorEl.textContent = "";
        errorEl.classList.add("hidden");
    };

    const showCopyStatus = (message, isError = false) => {
        copyStatus.textContent = message;
        copyStatus.classList.remove("hidden");
        copyStatus.classList.toggle("create-game-copy-status-error", isError);
    };

    const hideCopyStatus = () => {
        copyStatus.textContent = "";
        copyStatus.classList.add("hidden");
        copyStatus.classList.remove("create-game-copy-status-error");
    };

    copyBtn.addEventListener("click", async () => {
        hideCopyStatus();
        const url = linkInput.value.trim();
        if (!url) {
            showCopyStatus("Немає лінка для копіювання.", true);
            return;
        }

        try {
            const ok = await copyToClipboard(url);
            showCopyStatus(ok ? "Лінк скопійовано." : "Не вдалося скопіювати лінк.", !ok);
        } catch (error) {
            console.error("[create-game] copy failed:", error);
            showCopyStatus("Не вдалося скопіювати лінк.", true);
        }
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        hideError();
        hideCopyStatus();

        const word = normalizeWord(input.value);
        if (!word) {
            showError("Введіть слово.");
            input.focus();
            return;
        }

        submitBtn.disabled = true;
        submitBtn.textContent = "Створюємо...";

        try {
            const response = await fetch(`/api/ranked-by-word?word=${encodeURIComponent(word)}`, {
                cache: "no-store",
            });
            const payload = await response.json();

            if (!response.ok) {
                showError(payload?.error || "Не вдалося створити гру.");
                input.focus();
                return;
            }

            if (!payload || !Array.isArray(payload.ranking)) {
                showError("Сервер повернув некоректні дані.");
                return;
            }

            const gameId = String(payload.game_id || "").trim().toLowerCase();
            if (!/^[0-9a-f]{64}$/.test(gameId)) {
                showError("Сервер повернув некоректний id гри.");
                return;
            }

            const shareUrl = `${window.location.origin}/?game=${encodeURIComponent(gameId)}`;

            linkInput.value = shareUrl;
            openLink.href = shareUrl;
            renderTopWords(topWordsBody, payload.ranking);

            resultSection.classList.remove("hidden");
            resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) {
            console.error("[create-game] submit failed:", error);
            showError("Не вдалося створити гру. Спробуйте ще раз.");
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = "Створити гру";
        }
    });
});
