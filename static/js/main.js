import { fetchRankedWords, submitGuess } from "./api.js";
import { renderGuesses, createGuessItem } from "./ui.js";

/**
 * Глобальні змінні гри
 */
let isGoingUp = false;
let allowedWords = new Set();
let guessCount = 0;
let bestRank = Infinity;
let rankedWords = [];
let guesses = [];
let lastWord = null;
let MAX_RANK = 0;
let dayNumber = null;

function getNextHintRank(bestRank, guesses, rankedWords, maxRank) {
    if (bestRank === Infinity) {
        const wordObj = rankedWords.find(x => x.rank === 500);
        if (wordObj) return 500;
        return null;
    }
    if (bestRank === 1) {
        return null;
    }
    const isGuessed = (r) => guesses.some(g => g.rank === r);
    if (!isGoingUp) {
        if (bestRank > 2) {
            let candidate = Math.floor(bestRank / 2);
            while (candidate >= 2) {
                if (!isGuessed(candidate)) {
                    return candidate;
                }
                candidate = Math.floor(candidate / 2);
            }
            if (!isGuessed(2)) {
                return 2;
            }
            isGoingUp = true;
        }
        if (bestRank === 2) {
            isGoingUp = true;
        }
    }
    let bigger = bestRank + 1;
    while (bigger <= maxRank) {
        if (!isGuessed(bigger)) {
            return bigger;
        }
        bigger++;
    }
    return null;
}

function computeGameNumber(dateStr) {
    const baseDate = new Date(2025, 2, 31);
    const [year, month, day] = dateStr.split("-").map(Number);
    const currentDate = new Date(year, month - 1, day);
    const diffMs = currentDate - baseDate;
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    return diffDays + 1;
}

function formatDateToString(dateStr) {
    const [year, month, day] = dateStr.split("-").map(Number);
    const d = new Date(year, month - 1, day);
    const weekdayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const dayOfWeek = weekdayNames[d.getDay()];
    const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${dayOfWeek}, ${monthNames[d.getMonth()]} ${day}`;
}

async function fetchAllowedWords() {
    try {
        const response = await fetch("/api/wordlist");
        const data = await response.json();
        allowedWords = new Set(data.map(word => word.toLowerCase()));
    } catch (err) {
        console.error("[Error] Failed to fetch allowed words:", err);
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    await fetchAllowedWords();

    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const guessesContainer = document.getElementById("guessesContainer");
    const guessCountElem = document.getElementById("guessCount");
    const lastGuessWrapper = document.getElementById("lastGuessWrapper");
    const lastGuessDisplay = document.getElementById("lastGuessDisplay");
    const howToPlayBlock = document.getElementById("howToPlayBlock");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const previousGamesBtn = document.getElementById("previousGamesBtn");
    const previousGamesModal = document.getElementById("previousGamesModal");
    const closePreviousGamesModal = document.getElementById("closePreviousGamesModal");
    const previousGamesList = document.getElementById("previousGamesList");
    const randomGameBtn = document.getElementById("randomGameBtn");

    const closestWordsModal = document.getElementById("closestWordsModal");
    const closeModalBtn = document.getElementById("closeModalBtn");

    try {
        const response = await fetch("/api/daily-index");
        const dailyIndexData = await response.json();
        dayNumber = dailyIndexData.day_number;
    } catch (err) {
        console.error("[Error] Failed to fetch daily index:", err);
    }

    try {
        rankedWords = await fetchRankedWords();
    } catch (err) {
        console.error("[Error] fetchRankedWords failed:", err);
    }
    MAX_RANK = rankedWords.length;

    async function loadArchive(game_date) {
        try {
            const response = await fetch(`/archive/${game_date}`);
            if (!response.ok) {
                alert("Архів не знайдено для цієї дати");
                return;
            }
            const archiveData = await response.json();
            rankedWords = archiveData.ranking;
            MAX_RANK = rankedWords.length;
            guesses = [];
            guessCount = 0;
            guessCountElem.textContent = 0;
            alert(`Завантажено гру #${computeGameNumber(game_date)} (${formatDateToString(game_date)})`);
        } catch (err) {
            console.error("Error loading archive for date", game_date, err);
            alert("Помилка завантаження архіву");
        }
    }

    async function handleSubmit() {
        const word = guessInput.value.trim().toLowerCase();
        if (!word) return;

        if (guesses.some(g => g.word === word)) {
            alert(`Слово "${word}" уже вгадали`);
            return;
        }

        if (!allowedWords.has(word)) {
            alert("Вибачте, я не знаю цього слова");
            return;
        }

        if (howToPlayBlock && howToPlayBlock.style.display !== "none") {
            howToPlayBlock.style.display = "none";
        }

        let data;
        try {
            data = await submitGuess(word);
        } catch (err) {
            console.error("[Error] submitGuess failed:", err);
            return;
        }

        guessCount++;
        guessCountElem.textContent = guessCount;
        lastWord = word;

        if (data.error) {
            guesses.push({ word, rank: Infinity, error: true, errorMessage: data.error });
        } else {
            guesses.push({ word, rank: data.rank, error: false });
            if (data.rank < bestRank) {
                bestRank = data.rank;
            }

            if (data.rank === 1) {
                const congratsBlock = document.getElementById("congratsBlock");
                if (congratsBlock) {
                    congratsBlock.classList.remove("hidden");
                }

                const guessesUsedElem = document.getElementById("guessesUsed");
                if (guessesUsedElem) {
                    guessesUsedElem.textContent = guessCount;
                }

                // ✅ ВСТАВЛЯЄМО НОМЕР ГРИ
                const gameNumberElem = document.getElementById("gameNumber");
                if (gameNumberElem) {
                    gameNumberElem.textContent = dayNumber;
                }

                guessInput.disabled = true;
                submitGuessBtn.disabled = true;
                const hintButton = document.getElementById("hintButton");
                if (hintButton) {
                    hintButton.disabled = true;
                }
                closestWordsBtn.classList.remove("hidden");
            }
        }

        renderGuesses(
            guesses,
            lastWord,
            MAX_RANK,
            guessesContainer,
            lastGuessWrapper,
            lastGuessDisplay
        );
        guessInput.value = "";
        guessInput.focus();
    }

    guessInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            handleSubmit();
        }
    });
    submitGuessBtn.addEventListener("click", handleSubmit);

    const hintButton = document.createElement("button");
    hintButton.textContent = "Hint";
    hintButton.id = "hintButton";
    document.querySelector(".input-section").appendChild(hintButton);

    hintButton.addEventListener("click", () => {
        if (howToPlayBlock && howToPlayBlock.style.display !== "none") {
            howToPlayBlock.style.display = "none";
        }
        if (rankedWords.length === 0) {
            alert("Список слів порожній або не завантажений!");
            return;
        }
        const nextHintRank = getNextHintRank(bestRank, guesses, rankedWords, MAX_RANK);
        if (!nextHintRank) {
            alert("Немає підходящої підказки.");
            return;
        }
        const hintWordObj = rankedWords.find(item => item.rank === nextHintRank);
        if (!hintWordObj) {
            alert("Не знайдено слово з рангу " + nextHintRank);
            return;
        }
        lastWord = hintWordObj.word;
        guesses.push({
            word: hintWordObj.word,
            rank: hintWordObj.rank,
            error: false
        });
        if (hintWordObj.rank < bestRank) {
            bestRank = hintWordObj.rank;
        }
        renderGuesses(
            guesses,
            lastWord,
            MAX_RANK,
            guessesContainer,
            lastGuessWrapper,
            lastGuessDisplay
        );
    });

    previousGamesBtn.addEventListener("click", async () => {
        try {
            const response = await fetch("/archive");
            const dates = await response.json();
            if (!Array.isArray(dates) || dates.length === 0) {
                previousGamesList.innerHTML = "<p>Поки немає архівів</p>";
            } else {
                previousGamesList.innerHTML = "";
                dates.forEach(dateStr => {
                    const gameNumber = computeGameNumber(dateStr);
                    const labelDate = formatDateToString(dateStr);
                    const btn = document.createElement("button");
                    btn.textContent = `#${gameNumber} ${labelDate}`;
                    btn.addEventListener("click", () => {
                        loadArchive(dateStr);
                        previousGamesModal.classList.add("hidden");
                    });
                    previousGamesList.appendChild(btn);
                });
            }
            previousGamesModal.classList.remove("hidden");
        } catch (err) {
            console.error("[Error] Failed to fetch archive list:", err);
            previousGamesList.innerHTML = "<p>Помилка завантаження архіву</p>";
            previousGamesModal.classList.remove("hidden");
        }
    });

    closePreviousGamesModal.addEventListener("click", () => {
        previousGamesModal.classList.add("hidden");
    });

    randomGameBtn.addEventListener("click", async () => {
        try {
            const response = await fetch("/archive");
            const dates = await response.json();
            if (dates.length === 0) {
                alert("Немає архівів");
                return;
            }
            const randomDate = dates[Math.floor(Math.random() * dates.length)];
            await loadArchive(randomDate);
            previousGamesModal.classList.add("hidden");
        } catch (err) {
            console.error("[Error] Random game:", err);
        }
    });

    function showClosestWords() {
        const closestWordsList = document.getElementById("closestWordsList");
        closestWordsList.innerHTML = "";
        const top500 = rankedWords.slice(0, 500);
        top500.forEach(item => {
            const guessItem = createGuessItem(
                { word: item.word, rank: item.rank, error: false },
                MAX_RANK
            );
            closestWordsList.appendChild(guessItem);
        });
        closestWordsModal.classList.remove("hidden");
    }

    closestWordsBtn.addEventListener("click", showClosestWords);

    closeModalBtn.addEventListener("click", () => {
        closestWordsModal.classList.add("hidden");
    });
});
