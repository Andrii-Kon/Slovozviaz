import { fetchRankedWords, submitGuess } from "./api.js";
import { renderGuesses, createGuessItem } from "./ui.js";

let isGoingUp = false;
let allowedWords = new Set();
let guessCount = 0;
let bestRank = Infinity;
let rankedWords = [];
let guesses = [];
let lastWord = null;
let MAX_RANK = 0;
let dayNumber = null;
let currentGameDate = null;

function getNextHintRank(bestRank, guesses, rankedWords, maxRank) {
    if (bestRank === Infinity) {
        const wordObj = rankedWords.find(x => x.rank === 500);
        if (wordObj) return 500;
        return null;
    }
    if (bestRank === 1) return null;

    const isGuessed = r => guesses.some(g => g.rank === r);
    if (!isGoingUp) {
        if (bestRank > 2) {
            let candidate = Math.floor(bestRank / 2);
            while (candidate >= 2) {
                if (!isGuessed(candidate)) return candidate;
                candidate = Math.floor(candidate / 2);
            }
            if (!isGuessed(2)) return 2;
            isGoingUp = true;
        }
        if (bestRank === 2) isGoingUp = true;
    }
    let bigger = bestRank + 1;
    while (bigger <= maxRank) {
        if (!isGuessed(bigger)) return bigger;
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

function updateGameDateLabel() {
    const label = document.getElementById("gameDateLabel");
    // Використовуємо локальну дату у форматі ISO (YYYY-MM-DD)
    const today = new Date().toLocaleDateString('en-CA');
    const targetDate = currentGameDate || today;
    label.textContent = `${formatDateToString(targetDate)} (№${computeGameNumber(targetDate)})`;
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
    randomGameBtn.textContent = "🔀 Random";
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
    updateGameDateLabel();

    async function loadArchive(game_date) {
        try {
            const response = await fetch(`/archive/${game_date}`);
            if (!response.ok) return alert("Архів не знайдено для цієї дати");
            const archiveData = await response.json();
            rankedWords = archiveData.ranking;
            MAX_RANK = rankedWords.length;
            guesses = [];
            guessCount = 0;
            guessCountElem.textContent = 0;
            currentGameDate = game_date;
            updateGameDateLabel();
        } catch (err) {
            console.error("Error loading archive for date", game_date, err);
            alert("Помилка завантаження архіву");
        }
    }

    async function handleSubmit() {
        const word = guessInput.value.trim().toLowerCase();
        if (!word) return;
        if (guesses.some(g => g.word === word)) return alert(`Слово "${word}" уже вгадали`);
        if (!allowedWords.has(word)) return alert("Вибачте, я не знаю цього слова");
        if (howToPlayBlock && howToPlayBlock.style.display !== "none") howToPlayBlock.style.display = "none";

        let data;
        if (currentGameDate) {
            const match = rankedWords.find(item => item.word === word);
            data = match ? { rank: match.rank } : { error: "Цього слова не було в грі цього дня." };
        } else {
            try {
                const response = await fetch("/guess", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ word })
                });
                data = await response.json();
            } catch (err) {
                console.error("[Error] submitGuess failed:", err);
                return;
            }
        }

        guessCount++;
        guessCountElem.textContent = guessCount;
        lastWord = word;

        if (data.error) {
            guesses.push({ word, rank: Infinity, error: true, errorMessage: data.error });
        } else {
            guesses.push({ word, rank: data.rank, error: false });
            if (data.rank < bestRank) bestRank = data.rank;
            if (data.rank === 1) {
                const congratsBlock = document.getElementById("congratsBlock");
                if (congratsBlock) congratsBlock.classList.remove("hidden");
                const guessesUsedElem = document.getElementById("guessesUsed");
                if (guessesUsedElem) guessesUsedElem.textContent = guessCount;
                const gameNumberElem = document.getElementById("gameNumber");
                if (gameNumberElem) gameNumberElem.textContent = currentGameDate ? computeGameNumber(currentGameDate) : dayNumber;
                guessInput.disabled = true;
                submitGuessBtn.disabled = true;
                const hintButton = document.getElementById("hintButton");
                if (hintButton) hintButton.disabled = true;
                closestWordsBtn.classList.remove("hidden");
            }
        }

        renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
        guessInput.value = "";
        guessInput.focus();
    }

    guessInput.addEventListener("keypress", (e) => { if (e.key === "Enter") handleSubmit(); });
    submitGuessBtn.addEventListener("click", handleSubmit);

    const hintButton = document.createElement("button");
    hintButton.textContent = "Hint";
    hintButton.id = "hintButton";
    document.querySelector(".input-section").appendChild(hintButton);
    hintButton.addEventListener("click", () => {
        if (howToPlayBlock && howToPlayBlock.style.display !== "none") howToPlayBlock.style.display = "none";
        if (rankedWords.length === 0) return alert("Список слів порожній або не завантажений!");
        const nextHintRank = getNextHintRank(bestRank, guesses, rankedWords, MAX_RANK);
        if (!nextHintRank) return alert("Немає підходящої підказки.");
        const hintWordObj = rankedWords.find(item => item.rank === nextHintRank);
        if (!hintWordObj) return alert("Не знайдено слово з рангу " + nextHintRank);
        lastWord = hintWordObj.word;
        guesses.push({ word: hintWordObj.word, rank: hintWordObj.rank, error: false });
        if (hintWordObj.rank < bestRank) bestRank = hintWordObj.rank;
        renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
    });

    previousGamesBtn.addEventListener("click", async () => {
        try {
            const response = await fetch("/archive");
            const dates = await response.json();
            previousGamesList.innerHTML = "";
            // Використовуємо локальну дату у форматі ISO (YYYY-MM-DD)
            const today = new Date().toLocaleDateString('en-CA');
            dates.forEach(dateStr => {
                // Фільтруємо лише ігри з датою, що не перевищує сьогоднішню
                if (dateStr > today) return;
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
            const today = new Date().toISOString().split("T")[0];
            const validDates = dates.filter(date => date <= today);
            if (validDates.length === 0) return alert("Немає архівів");
            const randomDate = validDates[Math.floor(Math.random() * validDates.length)];
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
    closeModalBtn.addEventListener("click", () => closestWordsModal.classList.add("hidden"));
});