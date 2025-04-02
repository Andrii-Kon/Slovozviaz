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

// Глобальний об'єкт для збереження станів ігор
const gameStates = {};

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

// Функція updateGameDateLabel оновлює текст підпису гри у форматі "Гра: #X"
function updateGameDateLabel() {
    const label = document.getElementById("gameDateLabel");
    label.textContent = `Гра: #${ currentGameDate ? computeGameNumber(currentGameDate) : dayNumber }`;
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

    // Кнопки меню
    const hintButton = document.getElementById("hintButton");
    const previousGamesBtn = document.getElementById("previousGamesBtn");
    const giveUpBtn = document.getElementById("giveUpBtn");

    // Модалка "Здатися"
    const giveUpModal = document.getElementById("giveUpModal");
    const closeGiveUpModal = document.getElementById("closeGiveUpModal");
    const giveUpYesBtn = document.getElementById("giveUpYesBtn");
    const giveUpNoBtn = document.getElementById("giveUpNoBtn");

    // Модалка "Попередні ігри"
    const previousGamesModal = document.getElementById("previousGamesModal");
    const closePreviousGamesModal = document.getElementById("closePreviousGamesModal");
    const previousGamesList = document.getElementById("previousGamesList");
    const randomGameBtn = document.getElementById("randomGameBtn");
    randomGameBtn.textContent = "🔀 Random";

    // Модалка "Closest words"
    const closestWordsModal = document.getElementById("closestWordsModal");
    const closeModalBtn = document.getElementById("closeModalBtn");

    try {
        const response = await fetch("/api/daily-index");
        const dailyIndexData = await response.json();
        dayNumber = dailyIndexData.game_number;
        // Якщо currentGameDate не встановлено, встановлюємо її на сьогоднішню дату
        if (!currentGameDate) {
            currentGameDate = new Date().toISOString().split("T")[0];
        }
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
        // Зберігаємо поточний стан, якщо є активна гра
        if (currentGameDate) {
            gameStates[currentGameDate] = {
                guesses: guesses,
                guessCount: guessCount,
            };
        }
        try {
            const response = await fetch(`/archive/${game_date}`);
            if (!response.ok) return alert("Архів не знайдено для цієї дати");
            const archiveData = await response.json();

            rankedWords = archiveData.ranking;
            MAX_RANK = rankedWords.length;

            // Завантажуємо збережений стан гри, якщо він є; інакше ініціалізуємо новий стан
            if (gameStates[game_date]) {
                guesses = gameStates[game_date].guesses;
                guessCount = gameStates[game_date].guessCount;
            } else {
                guesses = [];
                guessCount = 0;
            }
            guessCountElem.textContent = guessCount;
            renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);

            currentGameDate = game_date;
            updateGameDateLabel();

            // Якщо гра виграна (є слово з рангом 1) – залишаємо блок "Вітаємо!",
            // інакше – ховаємо його та розблокуємо поле вводу
            const congratsBlock = document.getElementById("congratsBlock");
            if (guesses.some(g => g.rank === 1)) {
                if (congratsBlock) congratsBlock.classList.remove("hidden");
                guessInput.disabled = true;
                submitGuessBtn.disabled = true;
                if (hintButton) hintButton.disabled = true;
                closestWordsBtn.classList.remove("hidden");
            } else {
                if (congratsBlock) congratsBlock.classList.add("hidden");
                guessInput.disabled = false;
                submitGuessBtn.disabled = false;
                if (hintButton) hintButton.disabled = false;
                closestWordsBtn.classList.add("hidden");
            }
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
                endGameAsWin();
            }
        }

        renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
        guessInput.value = "";
        guessInput.focus();
    }

    function endGameAsWin() {
        const congratsBlock = document.getElementById("congratsBlock");
        if (congratsBlock) congratsBlock.classList.remove("hidden");
        const guessesUsedElem = document.getElementById("guessesUsed");
        if (guessesUsedElem) guessesUsedElem.textContent = guessCount;
        const gameNumberElem = document.getElementById("gameNumber");
        if (gameNumberElem) {
            gameNumberElem.textContent = currentGameDate ? computeGameNumber(currentGameDate) : dayNumber;
        }
        guessInput.disabled = true;
        submitGuessBtn.disabled = true;
        if (hintButton) hintButton.disabled = true;
        closestWordsBtn.classList.remove("hidden");
    }

    guessInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") handleSubmit();
    });
    submitGuessBtn.addEventListener("click", handleSubmit);

    hintButton.addEventListener("click", () => {
        if (howToPlayBlock && howToPlayBlock.style.display !== "none")
            howToPlayBlock.style.display = "none";
        if (rankedWords.length === 0)
            return alert("Список слів порожній або не завантажений!");
        const nextHintRank = getNextHintRank(bestRank, guesses, rankedWords, MAX_RANK);
        if (!nextHintRank)
            return alert("Немає підходящої підказки.");
        const hintWordObj = rankedWords.find(item => item.rank === nextHintRank);
        if (!hintWordObj)
            return alert("Не знайдено слово з рангу " + nextHintRank);
        lastWord = hintWordObj.word;
        guesses.push({ word: hintWordObj.word, rank: hintWordObj.rank, error: false });
        if (hintWordObj.rank < bestRank) bestRank = hintWordObj.rank;
        renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
        // Приховуємо випадаюче меню після натискання на підказку
        dropdownMenu.classList.add("hidden");
    });

    // --- Логіка модального вікна "Здатися" ---
    giveUpBtn.addEventListener("click", () => {
        giveUpModal.classList.remove("hidden");
    });

    giveUpYesBtn.addEventListener("click", () => {
        if (rankedWords.length > 0) {
            const secretWordObj = rankedWords.find(item => item.rank === 1) || rankedWords[0];
            guesses.push({ word: secretWordObj.word, rank: 1, error: false });
            bestRank = 1;
            guessCount++;
            guessCountElem.textContent = guessCount;
            renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
            endGameAsWin();
        }
        giveUpModal.classList.add("hidden");
    });

    giveUpNoBtn.addEventListener("click", () => {
        giveUpModal.classList.add("hidden");
    });
    closeGiveUpModal.addEventListener("click", () => {
        giveUpModal.classList.add("hidden");
    });
    // --- Кінець логіки "Здатися" ---

    previousGamesBtn.addEventListener("click", async () => {
        try {
            const response = await fetch("/archive");
            const dates = await response.json();
            previousGamesList.innerHTML = "";
            const today = new Date().toLocaleDateString('en-CA');
            dates.forEach(dateStr => {
                if (dateStr > today) return;
                const gameNumber = computeGameNumber(dateStr);
                const btn = document.createElement("button");
                btn.textContent = `Гра #${gameNumber}`;
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
    closeModalBtn.addEventListener("click", () => {
        closestWordsModal.classList.add("hidden");
    });

    // --- Логіка кнопки меню (⋮) ---
    const menuButton = document.getElementById("menuButton");
    const dropdownMenu = document.getElementById("dropdownMenu");
    menuButton.addEventListener("click", (event) => {
        event.stopPropagation();
        dropdownMenu.classList.toggle("hidden");
    });
    document.addEventListener("click", (event) => {
        if (!menuButton.contains(event.target) && !dropdownMenu.contains(event.target)) {
            dropdownMenu.classList.add("hidden");
        }
    });
});