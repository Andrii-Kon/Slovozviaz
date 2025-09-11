import { fetchRankedWords } from "./api.js";
import { renderGuesses, createGuessItem } from "./ui.js";

const weekdayFmt = new Intl.DateTimeFormat('uk-UA', { weekday: 'short' });
const monthFmt = new Intl.DateTimeFormat('uk-UA', { month: 'short' });

let isGoingUp = false;
let allowedWords = new Set();
// Замість одного масиву guesses
const gameState = {
    guesses: [],      // Реальні спроби гравця
    hints: [],        // Підказки (окремо)
    guessCount: 0,    // Лічильник спроб
    hintCount: 0      // Лічильник підказок
};
let bestRank = Infinity;
let rankedWords = [];
let lastWord = null;
let MAX_RANK = 0;
let dayNumber = null;
let currentGameDate = null;
let didWin = false;
let didGiveUp = false;
let giveUpWord = null;

// Функція для оновлення відображення лічильника підказок
function updateHintCountDisplay() {
    const hintCountElem = document.getElementById("hintCount");
    if (hintCountElem) {
        hintCountElem.textContent = gameState.hintCount;
    }
}

// const gameStates = {}; // Закоментовано, оскільки не використовується активно

function getNextHintRank(currentBestRank, currentGuesses, currentRankedWords, currentMaxRank) {
    let localIsGoingUp = isGoingUp;

    if (currentBestRank === Infinity) {
        const wordObj = currentRankedWords.find(x => x.rank === 500);
        return wordObj ? 500 : null;
    }
    if (currentBestRank === 1) return null;

    const isGuessed = r => currentGuesses.some(g => g.rank === r);

    if (!localIsGoingUp) {
        if (currentBestRank > 2) {
            let candidate = Math.floor(currentBestRank / 2);
            while (candidate >= 2) {
                if (!isGuessed(candidate)) return candidate;
                candidate = Math.floor(candidate / 2);
            }
            if (!isGuessed(2)) return 2;
            isGoingUp = true;
            localIsGoingUp = true;
        }
        if (currentBestRank === 2) {
            isGoingUp = true;
            localIsGoingUp = true;
        }
    }

    if (localIsGoingUp) {
        let bigger = currentBestRank + 1;
        while (bigger <= currentMaxRank) {
            if (!isGuessed(bigger)) return bigger;
            bigger++;
        }
    }
    return null;
}

function computeGameNumber(dateStr) {
    const baseDate = new Date(2025, 5, 2); // Травень - 4-й місяць (0-індексація)
    const [year, month, day] = dateStr.split("-").map(Number);
    const currentDate = new Date(year, month - 1, day);
    baseDate.setHours(0, 0, 0, 0);
    currentDate.setHours(0, 0, 0, 0);
    const diffMs = currentDate - baseDate;
    const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));
    return diffDays + 1;
}

function updateGameDateLabel() {
    const label = document.getElementById("gameDateLabel");
    if (!label) return;
    const gameNum = currentGameDate ? computeGameNumber(currentGameDate) : dayNumber;
    label.textContent = `Гра: #${gameNum}`;
}

async function fetchAllowedWords() {
    try {
        const response = await fetch("/api/wordlist");
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        allowedWords = new Set(data.map(word => word.toLowerCase()));
        console.log(`Loaded ${allowedWords.size} allowed words.`);
    } catch (err) {
        console.error("[Error] Failed to fetch allowed words:", err);
    }
}

function saveGameState() {
    if (!currentGameDate) return;
    const state = {
        guesses: gameState.guesses,
        hints: gameState.hints,
        guessCount: gameState.guessCount,
        hintCount: gameState.hintCount,
        bestRank,
        isGoingUp,
        lastWord,
        didWin,
        didGiveUp,
        giveUpWord
    };
    try {
        localStorage.setItem(`gameState_${currentGameDate}`, JSON.stringify(state));
    } catch (e) {
        console.error("Failed to save game state to localStorage:", e);
    }
}

function hideInitialInfoBlocks() {
    const howToPlayBlock = document.getElementById("howToPlayBlock");
    const privacyPolicyBlock = document.getElementById("privacyPolicyBlock");

    if (howToPlayBlock && howToPlayBlock.style.display !== "none") {
        howToPlayBlock.style.display = "none";
    }
    if (privacyPolicyBlock && privacyPolicyBlock.style.display !== "none") {
        privacyPolicyBlock.style.display = "none";
    }
}

function showInitialInfoBlocks() {
    const howToPlayBlock = document.getElementById("howToPlayBlock");
    const privacyPolicyBlock = document.getElementById("privacyPolicyBlock");

    if (howToPlayBlock) {
        howToPlayBlock.style.display = "";
    }
    if (privacyPolicyBlock) {
        privacyPolicyBlock.style.display = "";
    }
}

function loadGameState() {
    if (!currentGameDate) return;

    const savedState = localStorage.getItem(`gameState_${currentGameDate}`);
    const guessCountElem = document.getElementById("guessCount");
    const guessesContainer = document.getElementById("guessesContainer");
    const lastGuessWrapper = document.getElementById("lastGuessWrapper");
    const lastGuessDisplay = document.getElementById("lastGuessDisplay");
    const howToPlayBlock = document.getElementById("howToPlayBlock");
    const privacyPolicyBlock = document.getElementById("privacyPolicyBlock");

    if (!savedState) {
        resetUIForActiveGame();
        if (howToPlayBlock) howToPlayBlock.style.display = ""; // Show on new game
        if (privacyPolicyBlock) privacyPolicyBlock.style.display = ""; // Show on new game
        return;
    }

    try {
        const state = JSON.parse(savedState);
        gameState.guesses = state.guesses || [];
        gameState.hints = state.hints || [];
        gameState.guessCount = state.guessCount || 0;
        gameState.hintCount = state.hintCount || 0;
        bestRank = state.bestRank !== undefined ? state.bestRank : Infinity;
        isGoingUp = state.isGoingUp || false;
        lastWord = state.lastWord || null;
        didWin = state.didWin || false;
        didGiveUp = state.didGiveUp || false;
        giveUpWord = state.giveUpWord || null;

        if (guessCountElem) guessCountElem.textContent = gameState.guessCount;
        updateHintCountDisplay(); // Оновлюємо лічільник підказок
        renderGuesses([...gameState.guesses, ...gameState.hints], lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);

        if (gameState.guesses.length > 0 || gameState.hints.length > 0 || didWin || didGiveUp) {
            if (howToPlayBlock) howToPlayBlock.style.display = "none";
            if (privacyPolicyBlock) privacyPolicyBlock.style.display = "none";
        } else {
            if (howToPlayBlock) howToPlayBlock.style.display = "";
            if (privacyPolicyBlock) privacyPolicyBlock.style.display = "";
        }

        if (didGiveUp && giveUpWord) {
            showLoseMessageUI(giveUpWord);
        } else if (didWin) {
            showWinMessageUI();
        } else {
            resetUIForActiveGame();
        }
    } catch (e) {
        console.error("Failed to parse or apply saved game state:", e);
        localStorage.removeItem(`gameState_${currentGameDate}`);
        resetUIForActiveGame();
        if (howToPlayBlock) howToPlayBlock.style.display = "";
        if (privacyPolicyBlock) privacyPolicyBlock.style.display = "";
    }
}

function showWinMessageUI() {
    const congratsBlock = document.getElementById("congratsBlock");
    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const hintButton = document.getElementById("hintButton");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const giveUpBtn = document.getElementById("giveUpBtn");

    if (congratsBlock) {
        const congratsTitle = document.getElementById("congratsTitle");
        if (congratsTitle) congratsTitle.textContent = "Вітаємо!";

        const congratsMessageElem = document.getElementById("congratsMessage");
        const guessesUsedElem = document.getElementById("guessesUsed");
        const gameNumberElem = document.getElementById("gameNumber");
        if (congratsMessageElem && guessesUsedElem && gameNumberElem) {
            const gameNum = currentGameDate ? computeGameNumber(currentGameDate) : dayNumber;
            guessesUsedElem.textContent = gameState.guessCount;
            gameNumberElem.textContent = gameNum;
            congratsMessageElem.textContent = `Ви знайшли секретне слово #${gameNum} за ${gameState.guessCount} спроб(и)!`;
        }
        congratsBlock.classList.remove("hidden");
    }
    if (guessInput) guessInput.disabled = true;
    if (submitGuessBtn) submitGuessBtn.disabled = true;
    if (hintButton) hintButton.disabled = true;
    if (giveUpBtn) giveUpBtn.disabled = true;
    if (closestWordsBtn) closestWordsBtn.classList.remove("hidden");
}

function showLoseMessageUI(secretWord) {
    const congratsBlock = document.getElementById("congratsBlock");
    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const hintButton = document.getElementById("hintButton");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const giveUpBtn = document.getElementById("giveUpBtn");

    if (!congratsBlock) return;
    const congratsTitle = document.getElementById("congratsTitle");
    if (congratsTitle) congratsTitle.textContent = "Нехай щастить наступного разу!";

    const congratsMessageElem = document.getElementById("congratsMessage");
    if (congratsMessageElem) {
        const gameNum = currentGameDate ? computeGameNumber(currentGameDate) : dayNumber;
        congratsMessageElem.textContent = `Ви здалися на слові #${gameNum} за ${gameState.guessCount} спроб(и).\nСлово було: "${secretWord}".`;
    }
    congratsBlock.classList.remove("hidden");

    if (guessInput) guessInput.disabled = true;
    if (submitGuessBtn) submitGuessBtn.disabled = true;
    if (hintButton) hintButton.disabled = true;
    if (giveUpBtn) giveUpBtn.disabled = true;
    if (closestWordsBtn) closestWordsBtn.classList.remove("hidden");
}

function resetUIForActiveGame() {
    const congratsBlock = document.getElementById("congratsBlock");
    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const hintButton = document.getElementById("hintButton");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const giveUpBtn = document.getElementById("giveUpBtn");
    // const authorshipBtn = document.getElementById("authorshipBtn"); // Зазвичай завжди активна

    if (congratsBlock) congratsBlock.classList.add("hidden");
    if (closestWordsBtn) closestWordsBtn.classList.add("hidden");

    if (guessInput) guessInput.disabled = false;
    if (submitGuessBtn) submitGuessBtn.disabled = false;
    if (hintButton) hintButton.disabled = false;
    if (giveUpBtn) giveUpBtn.disabled = false;
    // if (authorshipBtn) authorshipBtn.disabled = false;
}

function endGameAsWin() {
    if (didWin) return;
    didWin = true;
    didGiveUp = false;
    showWinMessageUI();
    saveGameState();
}

function endGameAsGiveUp(secretWord) {
    if (didWin || didGiveUp) return;
    didGiveUp = true;
    didWin = false;
    giveUpWord = secretWord;
    showLoseMessageUI(secretWord);
    saveGameState();
}

document.addEventListener("DOMContentLoaded", async () => {
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/static/sw.js')
                .then(registration => console.log('Service Worker зареєстровано успішно:', registration))
                .catch(error => console.log('Помилка реєстрації Service Worker:', error));
        });
    }

    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const guessesContainer = document.getElementById("guessesContainer");
    const guessCountElem = document.getElementById("guessCount");
    const lastGuessWrapper = document.getElementById("lastGuessWrapper");
    const lastGuessDisplay = document.getElementById("lastGuessDisplay");
    const howToPlayBlock = document.getElementById("howToPlayBlock");
    const privacyPolicyBlock = document.getElementById("privacyPolicyBlock"); // Отримуємо блок політики
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const hintButton = document.getElementById("hintButton");
    const previousGamesBtn = document.getElementById("previousGamesBtn");
    const giveUpBtn = document.getElementById("giveUpBtn");
    const giveUpModal = document.getElementById("giveUpModal");
    const closeGiveUpModal = document.getElementById("closeGiveUpModal");
    const giveUpYesBtn = document.getElementById("giveUpYesBtn");
    const giveUpNoBtn = document.getElementById("giveUpNoBtn");
    const previousGamesModal = document.getElementById("previousGamesModal");
    const closePreviousGamesModal = document.getElementById("closePreviousGamesModal");
    const previousGamesList = document.getElementById("previousGamesList");
    const randomGameBtn = document.getElementById("randomGameBtn");
    const closestWordsModal = document.getElementById("closestWordsModal");
    const closeModalBtn = document.getElementById("closeModalBtn");
    const menuButton = document.getElementById("menuButton");
    const dropdownMenu = document.getElementById("dropdownMenu");
    const shareButton = document.getElementById("shareButton");
    // const readMoreBtn = document.getElementById("readMoreBtn"); // Закоментовано, якщо не використовується

    const authorshipBtn = document.getElementById('authorshipBtn');
    const authorshipModal = document.getElementById('authorshipModal');
    const closeAuthorshipModalBtn = document.getElementById('closeAuthorshipModal');

    if (randomGameBtn) randomGameBtn.textContent = "🔀 Випадкова";

    await fetchAllowedWords();

    try {
        const response = await fetch("/api/daily-index");
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const dailyIndexData = await response.json();
        dayNumber = dailyIndexData.game_number;
        if (!currentGameDate) {
            currentGameDate = new Date().toISOString().split("T")[0];
            gameState.guesses = [];
            gameState.hints = [];
            gameState.guessCount = 0;
            gameState.hintCount = 0;
            bestRank = Infinity;
            isGoingUp = false;
            lastWord = null;
            didWin = false;
            didGiveUp = false;
            giveUpWord = null;
        }
    } catch (err) {
        console.error("[Error] Failed to fetch daily index:", err);
        if (document.getElementById("gameDateLabel")) document.getElementById("gameDateLabel").textContent = "Помилка завантаження гри";
        return;
    }

    try {
        const dateParam = currentGameDate === new Date().toISOString().split("T")[0] ? null : currentGameDate;
        rankedWords = await fetchRankedWords(dateParam);
        if (!Array.isArray(rankedWords)) throw new Error("Ranked words data is not an array");
        MAX_RANK = rankedWords.length > 0 ? Math.max(...rankedWords.map(w => w.rank)) : 0;
        console.log(`Loaded ${rankedWords.length} ranked words for ${currentGameDate}. Max rank: ${MAX_RANK}`);
    } catch (err) {
        console.error(`[Error] fetchRankedWords failed for ${currentGameDate}:`, err);
        if (guessInput) guessInput.disabled = true;
        if (submitGuessBtn) submitGuessBtn.disabled = true;
        if (document.getElementById("gameDateLabel")) document.getElementById("gameDateLabel").textContent = "Помилка слів";
        return;
    }

    updateGameDateLabel();
    loadGameState(); // Це оновить видимість howToPlayBlock та privacyPolicyBlock

    async function handleSubmit() {
        if (didWin || didGiveUp) return;

        const word = guessInput.value.trim().toLowerCase();
        if (!word) return;

        hideInitialInfoBlocks(); // Ховаємо блоки при першій спробі

        if (gameState.guesses.some(g => g.word === word)) {
            if (lastGuessDisplay && lastGuessWrapper) {
                const errorMsgElement = document.createElement("div");
                errorMsgElement.textContent = `Слово "${word}" вже вгадано`;
                guessInput.value = "";
                errorMsgElement.style.cssText = "color: #ffffff; padding: 0px 12px; text-align: left; font-style: italic;";
                lastGuessDisplay.innerHTML = "";
                lastGuessDisplay.appendChild(errorMsgElement);
                lastGuessWrapper.classList.remove("hidden");
            }
            guessInput.focus();
            return;
        }

        if (!allowedWords.has(word)) {
            if (lastGuessDisplay && lastGuessWrapper) {
                const errorMsgElement = document.createElement("div");
                errorMsgElement.textContent = "Вибачте, я не знаю цього слова";
                errorMsgElement.style.cssText = "color: #ffffff; padding: 0px 12px; text-align: left; font-style: italic;";
                lastGuessDisplay.innerHTML = "";
                lastGuessDisplay.appendChild(errorMsgElement);
                lastGuessWrapper.classList.remove("hidden");
            }
            guessInput.focus();
            return;
        }

        const match = rankedWords.find(item => item.word === word);
        let data = match ? { rank: match.rank } : { rank: Infinity, error: true, errorMessage: "Цього слова немає у рейтингу цього дня." };

        gameState.guessCount++;
        if (guessCountElem) guessCountElem.textContent = gameState.guessCount;
        lastWord = word;
        gameState.guesses.push({ word, rank: data.rank, error: data.error || false, errorMessage: data.errorMessage });

        gameState.guesses.sort((a, b) => {
            if (a.error && !b.error) return -1;
            if (!a.error && b.error) return 1;
            if (a.error && b.error) return 0;
            return a.rank - b.rank;
        });

        if (!data.error) {
            if (data.rank < bestRank) bestRank = data.rank;
            if (data.rank === 1) {
                renderGuesses([...gameState.guesses, ...gameState.hints], lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
                endGameAsWin();
                return;
            }
        }
        renderGuesses([...gameState.guesses, ...gameState.hints], lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
        guessInput.value = "";
        guessInput.focus();
        saveGameState();
    }

    async function loadArchive(game_date) {
        if (currentGameDate) saveGameState();
        console.log(`Loading archive for date: ${game_date}`);

        if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Завантаження гри...</p>';
        if (lastGuessWrapper) lastGuessWrapper.classList.add('hidden');
        if (guessCountElem) guessCountElem.textContent = '...';
        if (document.getElementById("gameDateLabel")) document.getElementById("gameDateLabel").textContent = 'Завантаження...';

        try {
            const response = await fetch(`/archive/${game_date}`);
            if (!response.ok) {
                alert(response.status === 404 ? `Архів для дати ${game_date} не знайдено.` : `Помилка завантаження архіву: ${response.statusText}`);
                if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Помилка завантаження.</p>';
                return;
            }
            const archiveData = await response.json();
            if (!archiveData || !Array.isArray(archiveData.ranking)) throw new Error("Invalid archive data format");

            rankedWords = archiveData.ranking;
            MAX_RANK = rankedWords.length > 0 ? Math.max(...rankedWords.map(w => w.rank)) : 0;
            currentGameDate = game_date;
            gameState.guesses = [];
            gameState.hints = [];
            gameState.guessCount = 0;
            gameState.hintCount = 0;
            bestRank = Infinity;
            isGoingUp = false;
            lastWord = null;
            didWin = false;
            didGiveUp = false;
            giveUpWord = null;

            console.log(`Loaded ${rankedWords.length} words for ${game_date}. Max rank: ${MAX_RANK}`);
            updateGameDateLabel();
            if (guessCountElem) guessCountElem.textContent = '0';
            updateHintCountDisplay(); // Оновлюємо лічільник підказок
            if (guessesContainer) guessesContainer.innerHTML = '';
            if (lastGuessWrapper) lastGuessWrapper.classList.add('hidden');

            showInitialInfoBlocks(); // Показуємо інфо-блоки для нової гри
            loadGameState(); // Завантажуємо стан, якщо він є, або ініціалізуємо

        } catch (err) {
            console.error("Error loading archive for date", game_date, err);
            alert("Помилка завантаження архіву. Див. консоль для деталей.");
            if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Помилка завантаження.</p>';
        } finally {
            if (previousGamesModal) previousGamesModal.classList.add("hidden");
            if (dropdownMenu) dropdownMenu.classList.add("hidden");
            if (guessInput) guessInput.focus();
        }
    }

    if (guessInput) guessInput.addEventListener("keypress", e => e.key === "Enter" && handleSubmit());
    if (submitGuessBtn) submitGuessBtn.addEventListener("click", handleSubmit);

    if (hintButton) {
        hintButton.addEventListener("click", () => {
            if (didWin || didGiveUp) return;
            hideInitialInfoBlocks(); // Ховаємо інфо-блоки при дії
            if (rankedWords.length === 0) {
                alert("Список слів ще не завантажено!");
                return;
            }
            const nextHintRank = getNextHintRank(bestRank, [...gameState.guesses, ...gameState.hints], rankedWords, MAX_RANK);
            if (nextHintRank === null) {
                alert("Не вдалося знайти підходящу підказку.");
                return;
            }
            const hintWordObj = rankedWords.find(item => item.rank === nextHintRank);
            if (!hintWordObj) {
                alert(`Помилка: Не знайдено слово з рангом ${nextHintRank}.`);
                return;
            }

            // ✅ ВИПРАВЛЕННЯ: Збільшуємо тільки hintCount
            gameState.hintCount++;
            updateHintCountDisplay(); // Оновлюємо відображення підказок

            lastWord = hintWordObj.word;
            gameState.hints.push({ word: hintWordObj.word, rank: hintWordObj.rank, error: false, isHint: true });
            gameState.hints.sort((a, b) => (a.error && !b.error) ? -1 : (!a.error && b.error) ? 1 : (a.error && b.error) ? 0 : a.rank - b.rank);
            if (hintWordObj.rank < bestRank) bestRank = hintWordObj.rank;
            renderGuesses([...gameState.guesses, ...gameState.hints], lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
            if (hintWordObj.rank === 1) endGameAsWin();
            else saveGameState();
            if (dropdownMenu) dropdownMenu.classList.add("hidden");
        });
    }

    if (giveUpBtn) giveUpBtn.addEventListener("click", () => {
        if (didWin || didGiveUp) return;
        if (giveUpModal) giveUpModal.classList.remove("hidden");
        if (dropdownMenu) dropdownMenu.classList.add("hidden");
    });
    if (closeGiveUpModal) closeGiveUpModal.addEventListener("click", () => giveUpModal && giveUpModal.classList.add("hidden"));
    if (giveUpNoBtn) giveUpNoBtn.addEventListener("click", () => giveUpModal && giveUpModal.classList.add("hidden"));

    if (giveUpYesBtn) {
        giveUpYesBtn.addEventListener("click", () => {
            if (didWin || didGiveUp) {
                if (giveUpModal) giveUpModal.classList.add("hidden");
                return;
            }
            hideInitialInfoBlocks(); // Ховаємо інфо-блоки
            const secretWordObj = rankedWords.find(item => item.rank === 1);
            const secretWord = secretWordObj ? secretWordObj.word : (rankedWords.length > 0 ? rankedWords[0].word : "невідомо");

            // ✅ ВИПРАВЛЕННЯ: НЕ збільшуємо guessCount при здачі
            // gameState.guessCount++; // Закоментовано - здача не є спробою
            // if (guessCountElem) guessCountElem.textContent = gameState.guessCount; // Закоментовано

            gameState.guesses.push({ word: secretWord, rank: 1, error: false, gaveUp: true });
            lastWord = secretWord;
            bestRank = 1;
            gameState.guesses.sort((a, b) => (a.error && !b.error) ? -1 : (!a.error && b.error) ? 1 : (a.error && b.error) ? 0 : a.rank - b.rank);
            renderGuesses([...gameState.guesses, ...gameState.hints], lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
            endGameAsGiveUp(secretWord);
            if (giveUpModal) giveUpModal.classList.add("hidden");
        });
    }
    if (giveUpModal) giveUpModal.addEventListener('click', e => e.target === giveUpModal && giveUpModal.classList.add('hidden'));

    // ─────────────────────────────────────────────────────────────────────
    // ОНОВЛЕНО: Рендер списку архівів одним innerHTML + делегування кліків
    // ─────────────────────────────────────────────────────────────────────
    if (previousGamesBtn) {
        previousGamesBtn.addEventListener("click", async () => {
            if (previousGamesModal) previousGamesModal.classList.remove("hidden");
            if (dropdownMenu) dropdownMenu.classList.add("hidden");
            if (previousGamesList) previousGamesList.innerHTML = "<p>Завантаження архіву...</p>";
            try {
                const response = await fetch("/archive");
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const dates = await response.json();
                if (!Array.isArray(dates)) throw new Error("Archive list format incorrect");

                const today = new Date().toISOString().split("T")[0];
                dates.sort((a, b) => b.localeCompare(a));
                const filtered = dates.filter(d => d <= today);

                if (previousGamesList) {
                    if (filtered.length === 0) {
                        previousGamesList.innerHTML = "<p>Архівних ігор не знайдено.</p>";
                    } else {
                        const parts = new Array(filtered.length);
                        for (let i = 0; i < filtered.length; i++) {
                            const dateStr = filtered[i];
                            const gameNumber = computeGameNumber(dateStr);
                            const dateObj = new Date(dateStr + "T00:00:00");
                            const weekday = weekdayFmt.format(dateObj);
                            const month = monthFmt.format(dateObj).replace('.', '');
                            const day = dateObj.getDate();

                            // 👇 новий блок: дістаємо локальний стан
                            let statusLabel = "";
                            try {
                                const saved = localStorage.getItem(`gameState_${dateStr}`);
                                if (saved) {
                                    const state = JSON.parse(saved);
                                    if (state.didWin) {
                                        statusLabel = "Відгадав";
                                    } else if (state.didGiveUp) {
                                        statusLabel = "Здався";
                                    }
                                }
                            } catch (e) {
                                console.warn("Cannot parse game state for", dateStr, e);
                            }

                            parts[i] =
                                `<button class="archive-button" data-date="${dateStr}">
      <span class="archive-left">#${gameNumber}&nbsp;&nbsp;${weekday}, ${day} ${month}</span>
      <span class="archive-right">${statusLabel}</span>
   </button>`;
                        }
                        previousGamesList.innerHTML = parts.join("");

                        // делегування кліків
                        previousGamesList.onclick = (e) => {
                            const btn = e.target.closest('button.archive-button');
                            if (!btn) return;
                            loadArchive(btn.dataset.date);
                        };
                    }
                }
            } catch (err) {
                console.error("[Error] Failed to fetch archive list:", err);
                if (previousGamesList) previousGamesList.innerHTML = "<p>Помилка завантаження архіву.</p>";
            }
        });
    }
    if (closePreviousGamesModal) closePreviousGamesModal.addEventListener("click", () => previousGamesModal && previousGamesModal.classList.add("hidden"));
    if (previousGamesModal) previousGamesModal.addEventListener('click', e => e.target === previousGamesModal && previousGamesModal.classList.add('hidden'));

    if (randomGameBtn) {
        randomGameBtn.addEventListener("click", async () => {
            if (dropdownMenu) dropdownMenu.classList.add("hidden");
            try {
                const response = await fetch("/archive");
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const dates = await response.json();
                const today = new Date().toISOString().split("T")[0];
                const validDates = dates.filter(date => date <= today);
                if (validDates.length === 0) {
                    alert("Не знайдено доступних архівних ігор.");
                    return;
                }
                const randomDate = validDates[Math.floor(Math.random() * validDates.length)];
                await loadArchive(randomDate);
            } catch (err) {
                console.error("[Error] Failed to load random game:", err);
                alert("Помилка при завантаженні випадкової гри.");
            }
        });
    }

    function showClosestWords() {
        const closestWordsList = document.getElementById("closestWordsList");
        if (!closestWordsList) return;
        closestWordsList.innerHTML = "";
        const topN = rankedWords.slice(0, 500).sort((a, b) => a.rank - b.rank);
        const closestWordsTitle = document.getElementById("closestWordsTitle");
        if (closestWordsTitle) closestWordsTitle.textContent = `Це були ${topN.length} найближчих слів:`;
        if (topN.length === 0) closestWordsList.innerHTML = "<p>Список слів порожній.</p>";
        else topN.forEach(item => closestWordsList.appendChild(createGuessItem({ word: item.word, rank: item.rank, error: false }, MAX_RANK)));
        if (closestWordsModal) closestWordsModal.classList.remove("hidden");
    }
    if (closestWordsBtn) closestWordsBtn.addEventListener("click", showClosestWords);
    if (closeModalBtn) closeModalBtn.addEventListener("click", () => closestWordsModal && closestWordsModal.classList.add("hidden"));
    if (closestWordsModal) closestWordsModal.addEventListener('click', e => e.target === closestWordsModal && closestWordsModal.classList.add('hidden'));

    if (menuButton && dropdownMenu) menuButton.addEventListener("click", e => {
        e.stopPropagation();
        dropdownMenu.classList.toggle("hidden");
    });
    document.addEventListener("click", e => {
        if (menuButton && dropdownMenu && !menuButton.contains(e.target) && !dropdownMenu.contains(e.target)) {
            dropdownMenu.classList.add("hidden");
        }
    });

    if (shareButton) {
        shareButton.addEventListener('click', async () => {
            if (!didWin && !didGiveUp) {
                alert("Ви ще не завершили гру, щоб поділитися результатом!");
                return;
            }
            const gameNum = currentGameDate ? computeGameNumber(currentGameDate) : dayNumber;
            let shareText = `Словозв'яз #${gameNum}\nСпроб: ${gameState.guessCount}\nПідказок: ${gameState.hintCount}\n`;
            const closestGuessRank = gameState.guesses.filter(g => !g.error && g.rank !== 1 && g.rank !== Infinity).reduce((minRank, g) => Math.min(minRank, g.rank), Infinity);
            if (didWin) shareText += "✅ Знайдено!\n";
            else if (didGiveUp) shareText += `🏳️ Здався. Найближче слово: ${closestGuessRank !== Infinity ? `(ранг ${closestGuessRank})` : '(немає)'}\n`;
            shareText += `\n${window.location.href}`;
            try {
                if (navigator.share) await navigator.share({ title: `Словозв'яз #${gameNum}`, text: shareText });
                else {
                    await navigator.clipboard.writeText(shareText);
                    alert('Результат скопійовано до буферу обміну!');
                }
            } catch (err) {
                console.error('Error sharing:', err);
                alert('Не вдалося поділитися або скопіювати.');
            }
        });
    }

    // const readMoreBtn = document.getElementById("readMoreBtn"); // Закоментовано, якщо не використовується
    // if (readMoreBtn) readMoreBtn.addEventListener('click', () => window.open('https://github.com/Konon-hub/Slovozviaz', '_blank'));

    if (authorshipBtn && authorshipModal && closeAuthorshipModalBtn && dropdownMenu) {
        authorshipBtn.addEventListener('click', () => {
            authorshipModal.classList.remove('hidden');
            dropdownMenu.classList.add('hidden');
        });
        closeAuthorshipModalBtn.addEventListener('click', () => authorshipModal.classList.add('hidden'));
        authorshipModal.addEventListener('click', e => e.target === authorshipModal && authorshipModal.classList.add('hidden'));
    } else {
        console.error('Error: Could not find all elements for the Authorship modal.');
    }
});
