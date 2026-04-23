import {
    fetchRankedWords,
    fetchRankedWordsByWord,
    fetchRankedWordsByGameId,
    normalizeWordToKnownLemma,
    fetchTwitchConnectionStatus,
    disconnectTwitchConnection,
    registerTwitchChatTarget,
    fetchTwitchChatEvents
} from "./api.js?v=20260420-2";
import { renderGuesses, createGuessItem } from "./ui.js?v=20260415-1";

const weekdayFmt = new Intl.DateTimeFormat('uk-UA', { weekday: 'short' });
const monthFmt = new Intl.DateTimeFormat('uk-UA', { month: 'short' });
const kyivDateFmt = new Intl.DateTimeFormat("uk-UA", {
    timeZone: "Europe/Kyiv",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
});

let isGoingUp = false;
let allowedWords = new Set();
let allowedWordsLoaded = false;
let allowedWordsLoadingPromise = null;
// Замість одного масиву guesses
const gameState = {
    guesses: [],      // Реальні спроби гравця
    hints: [],        // Підказки (окремо)
    guessCount: 0,    // Лічильник спроб
    hintCount: 0      // Лічильник підказок
};
let bestRank = Infinity;
let rankedWords = [];
let rankedWordLookup = new Map();
let lastWord = null;
let MAX_RANK = 0;
let dayNumber = null;
let currentGameDate = null;
let currentCustomGameId = null;
let didWin = false;
let didGiveUp = false;
let giveUpWord = null;
let archiveDatesCache = null;
let archiveDatesInFlight = null;
let nextEntrySequence = 0;
let guessSubmissionQueue = Promise.resolve();

const twitchChatState = {
    enabled: false,
    channel: null,
    gameScope: null,
    pollIntervalMs: 1500,
    targetRefreshMs: 45000,
    lastEventId: 0,
    isPolling: false,
    pollTimerId: null,
    targetHeartbeatId: null,
    errorCount: 0
};

const twitchConnectionState = {
    oauthEnabled: false,
    workerReady: false,
    connected: false,
    connection: null,
    connectUrl: null
};

const TWITCH_LEADERBOARD_REFRESH_MS = 30000;
const TWITCH_INLINE_LEADERBOARD_LIMIT = 10;
const TWITCH_SIDEBAR_LEADERBOARD_LIMIT = 10;
const TWITCH_LEADERBOARD_STORAGE_EVENT = "slovozviaz:game-state-saved";
const TWITCH_LEADERBOARD_LOG_STORAGE_KEY = "slovozviaz:twitch-leaderboard-log:v1";
const twitchLeaderboardState = {
    channel: null,
    solvers: [],
    isLoading: false,
    lastLoadedAt: 0,
    refreshTimerId: null
};

function setButtonTextPreservingIcon(button, label) {
    if (!button) return;
    const textNode = button.querySelector("span:last-child");
    if (textNode) {
        textNode.textContent = label;
        return;
    }
    button.textContent = label;
}

const ARCHIVE_DATES_CACHE_KEY = "archiveDatesCache_v1";
const ARCHIVE_DATES_CACHE_TTL_MS = 5 * 60 * 1000;
const SETTINGS_STORAGE_KEY = "slovozviazSettings_v1";
const DEFAULT_SETTINGS = {
    hintMode: "easy",
    sortMode: "similarity"
};
const settings = { ...DEFAULT_SETTINGS };

function normalizeHintMode(value) {
    return ["easy", "medium", "hard"].includes(value) ? value : DEFAULT_SETTINGS.hintMode;
}

function normalizeSortMode(value) {
    return ["similarity", "guess-order"].includes(value) ? value : DEFAULT_SETTINGS.sortMode;
}

function loadSettings() {
    try {
        const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
        if (!raw) return;

        const parsed = JSON.parse(raw);
        settings.hintMode = normalizeHintMode(parsed?.hintMode);
        settings.sortMode = normalizeSortMode(parsed?.sortMode);
    } catch (err) {
        console.warn("Cannot load settings from localStorage:", err);
    }
}

function saveSettings() {
    try {
        localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
    } catch (err) {
        console.warn("Cannot save settings to localStorage:", err);
    }
}

// Функція для оновлення відображення лічильника підказок
function updateHintCountDisplay() {
    const hintCountElem = document.getElementById("hintCount");
    if (hintCountElem) {
        hintCountElem.textContent = gameState.hintCount;
    }
}

// const gameStates = {}; // Закоментовано, оскільки не використовується активно

function getUsedRanks(entries) {
    return new Set(entries.filter(entry => !entry.error && Number.isFinite(entry.rank)).map(entry => entry.rank));
}

function findNearestAvailableRank(targetRank, usedRanks, minRank, maxRank) {
    if (!Number.isFinite(targetRank) || minRank > maxRank) return null;

    const clampedTarget = Math.min(maxRank, Math.max(minRank, Math.round(targetRank)));
    for (let offset = 0; offset <= maxRank - minRank; offset++) {
        const lower = clampedTarget - offset;
        if (lower >= minRank && !usedRanks.has(lower)) return lower;

        const upper = clampedTarget + offset;
        if (offset > 0 && upper <= maxRank && !usedRanks.has(upper)) return upper;
    }

    return null;
}

function getRandomAvailableRank(usedRanks, minRank, maxRank) {
    if (minRank > maxRank) return null;

    const available = [];
    for (let rank = minRank; rank <= maxRank; rank++) {
        if (!usedRanks.has(rank)) {
            available.push(rank);
        }
    }

    if (available.length === 0) return null;
    return available[Math.floor(Math.random() * available.length)];
}

function getNextHintRank(currentBestRank, currentGuesses, currentRankedWords, currentMaxRank, hintMode) {
    const usedRanks = getUsedRanks(currentGuesses);
    const defaultHintRank = findNearestAvailableRank(500, usedRanks, 2, currentMaxRank);

    if (currentBestRank === Infinity) {
        return defaultHintRank;
    }
    if (currentBestRank === 1) return null;

    const betterMinRank = 2;
    const betterMaxRank = currentBestRank - 1;

    if (hintMode === "hard") {
        return getRandomAvailableRank(usedRanks, betterMinRank, betterMaxRank)
            ?? getRandomAvailableRank(usedRanks, currentBestRank + 1, currentMaxRank);
    }

    const targetRank = hintMode === "medium"
        ? currentBestRank - 1
        : Math.floor(currentBestRank / 2);

    return findNearestAvailableRank(targetRank, usedRanks, betterMinRank, betterMaxRank)
        ?? findNearestAvailableRank(currentBestRank + 1, usedRanks, currentBestRank + 1, currentMaxRank)
        ?? defaultHintRank
        ?? (currentRankedWords.find(wordObj => !usedRanks.has(wordObj.rank) && wordObj.rank > 1)?.rank ?? null);
}

function computeGameNumber(dateStr) {
    if (typeof dateStr !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
        return null;
    }
    const baseDate = new Date(2025, 5, 2); // Травень - 4-й місяць (0-індексація)
    const [year, month, day] = dateStr.split("-").map(Number);
    const currentDate = new Date(year, month - 1, day);
    baseDate.setHours(0, 0, 0, 0);
    currentDate.setHours(0, 0, 0, 0);
    const diffMs = currentDate - baseDate;
    const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));
    return diffDays + 1;
}

function normalizeWord(value) {
    return (value || "").trim().toLowerCase();
}

function normalizeGameId(value) {
    return (value || "").trim().toLowerCase();
}

function normalizeDateParam(value) {
    const normalized = (value || "").trim();
    return /^\d{4}-\d{2}-\d{2}$/.test(normalized) ? normalized : null;
}

function normalizeTwitchChannel(value) {
    return (value || "")
        .trim()
        .toLowerCase()
        .replace(/^#/, "")
        .replace(/[^a-z0-9_]/g, "");
}

function normalizeTwitchGameScope(value) {
    return (value || "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9:_-]/g, "");
}

function getCurrentKyivDateString() {
    const parts = kyivDateFmt.formatToParts(new Date());
    const year = parts.find(part => part.type === "year")?.value;
    const month = parts.find(part => part.type === "month")?.value;
    const day = parts.find(part => part.type === "day")?.value;

    if (!year || !month || !day) {
        throw new Error("Cannot format current Kyiv date.");
    }

    return `${year}-${month}-${day}`;
}

function getCurrentGameNumber() {
    if (currentCustomGameId || !currentGameDate) return null;
    return computeGameNumber(currentGameDate);
}

function setCongratsMessage(message) {
    const congratsMessageElem = document.getElementById("congratsMessage");
    if (congratsMessageElem) {
        congratsMessageElem.textContent = message;
    }
}

function getCurrentGameStateKey() {
    if (currentCustomGameId) return `gameState_custom_${currentCustomGameId}`;
    if (!currentGameDate) return null;
    return `gameState_${currentGameDate}`;
}

function hydrateStoredEntries(entries) {
    if (!Array.isArray(entries)) return [];

    let localNextSequence = nextEntrySequence;
    const hydratedEntries = entries
        .filter(entry => entry && typeof entry.word === "string")
        .map(entry => {
            const savedSequence = Number.isInteger(entry.sequence) && entry.sequence >= 0
                ? entry.sequence
                : null;

            if (savedSequence !== null) {
                if (savedSequence >= localNextSequence) {
                    localNextSequence = savedSequence + 1;
                }
                return { ...entry, sequence: savedSequence };
            }

            const sequence = localNextSequence++;
            return { ...entry, sequence };
        });

    nextEntrySequence = localNextSequence;
    return hydratedEntries;
}

function getRankedWordEntry(word) {
    if (typeof word !== "string" || !word) return null;
    return rankedWordLookup.get(word) || null;
}

function enrichEntriesWithRankingData(entries) {
    if (!Array.isArray(entries) || rankedWordLookup.size === 0) return entries;

    return entries.map(entry => {
        if (!entry || entry.error || typeof entry.word !== "string") return entry;

        const rankedWord = getRankedWordEntry(entry.word);
        if (!rankedWord) return entry;

        const nextEntry = { ...entry };
        if (!Number.isFinite(nextEntry.rank)) {
            nextEntry.rank = rankedWord.rank;
        }
        if (!Number.isFinite(nextEntry.similarity) && Number.isFinite(rankedWord.similarity)) {
            nextEntry.similarity = rankedWord.similarity;
        }
        return nextEntry;
    });
}

function createGameEntry(entry) {
    const sequence = nextEntrySequence++;
    return { ...entry, sequence };
}

function getCombinedEntries() {
    return [...gameState.guesses, ...gameState.hints];
}

function renderCurrentGuesses() {
    const guessesContainer = document.getElementById("guessesContainer");
    const lastGuessWrapper = document.getElementById("lastGuessWrapper");
    const lastGuessDisplay = document.getElementById("lastGuessDisplay");

    if (!guessesContainer || !lastGuessWrapper || !lastGuessDisplay) return;
    renderGuesses(
        getCombinedEntries(),
        lastWord,
        MAX_RANK,
        guessesContainer,
        lastGuessWrapper,
        lastGuessDisplay,
        settings.sortMode
    );
}

function applySettingsToForm() {
    const hintRadio = document.querySelector(`input[name="hintMode"][value="${settings.hintMode}"]`);
    const sortRadio = document.querySelector(`input[name="sortMode"][value="${settings.sortMode}"]`);

    if (hintRadio) hintRadio.checked = true;
    if (sortRadio) sortRadio.checked = true;
}

function resetRuntimeGameState() {
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
    nextEntrySequence = 0;
}

function updateUrlForCurrentGame() {
    const url = new URL(window.location.href);
    if (currentCustomGameId) {
        url.searchParams.set("game", currentCustomGameId);
        url.searchParams.delete("custom");
        url.searchParams.delete("date");
    } else {
        url.searchParams.delete("game");
        url.searchParams.delete("custom");
        const todayStr = getCurrentKyivDateString();
        if (currentGameDate && currentGameDate !== todayStr) {
            url.searchParams.set("date", currentGameDate);
        } else {
            url.searchParams.delete("date");
        }
    }
    history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function updateGameDateLabel() {
    const label = document.getElementById("gameDateLabel");
    if (!label) return;

    if (currentCustomGameId) {
        label.textContent = "Кастом гра";
        return;
    }

    const gameNum = currentGameDate ? computeGameNumber(currentGameDate) : dayNumber;
    label.textContent = gameNum ? `Гра: #${gameNum}` : "Гра: #?";
}

async function fetchAllowedWords() {
    if (allowedWordsLoaded) return true;
    if (allowedWordsLoadingPromise) return allowedWordsLoadingPromise;

    allowedWordsLoadingPromise = (async () => {
        try {
            const response = await fetch("/api/wordlist");
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            allowedWords = new Set(data.map(word => word.toLowerCase()));
            allowedWordsLoaded = true;
            console.log(`Loaded ${allowedWords.size} allowed words.`);
            return true;
        } catch (err) {
            console.error("[Error] Failed to fetch allowed words:", err);
            return false;
        } finally {
            allowedWordsLoadingPromise = null;
        }
    })();

    return allowedWordsLoadingPromise;
}

async function resolveGuessWord(rawWord) {
    const originalWord = normalizeWord(rawWord);
    if (!originalWord) {
        return {
            originalWord,
            resolvedWord: originalWord,
            wasChanged: false,
        };
    }

    if (allowedWordsLoaded && allowedWords.has(originalWord)) {
        return {
            originalWord,
            resolvedWord: originalWord,
            wasChanged: false,
        };
    }

    try {
        const response = await normalizeWordToKnownLemma(originalWord);
        if (!response.ok) {
            return {
                originalWord,
                resolvedWord: originalWord,
                wasChanged: false,
            };
        }

        const resolvedWord = normalizeWord(response?.data?.resolved_word) || originalWord;
        return {
            originalWord,
            resolvedWord,
            wasChanged: resolvedWord !== originalWord,
        };
    } catch (err) {
        console.warn("Cannot normalize guess word:", err);
        return {
            originalWord,
            resolvedWord: originalWord,
            wasChanged: false,
        };
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function readArchiveDatesFromSessionCache(allowExpired = false) {
    try {
        const raw = sessionStorage.getItem(ARCHIVE_DATES_CACHE_KEY);
        if (!raw) return null;

        const parsed = JSON.parse(raw);
        if (!parsed || !Array.isArray(parsed.dates) || typeof parsed.savedAt !== "number") {
            return null;
        }

        const isFresh = (Date.now() - parsed.savedAt) <= ARCHIVE_DATES_CACHE_TTL_MS;
        if (!allowExpired && !isFresh) return null;
        return parsed.dates;
    } catch (err) {
        console.warn("Cannot read archive dates cache:", err);
        return null;
    }
}

function writeArchiveDatesToSessionCache(dates) {
    try {
        sessionStorage.setItem(ARCHIVE_DATES_CACHE_KEY, JSON.stringify({
            dates: dates,
            savedAt: Date.now()
        }));
    } catch (err) {
        console.warn("Cannot write archive dates cache:", err);
    }
}

async function fetchArchiveDates(forceRefresh = false) {
    if (!forceRefresh && Array.isArray(archiveDatesCache)) return archiveDatesCache;

    if (!forceRefresh) {
        const cached = readArchiveDatesFromSessionCache(false);
        if (cached) {
            archiveDatesCache = cached;
            return cached;
        }
        if (archiveDatesInFlight) return archiveDatesInFlight;
    }

    archiveDatesInFlight = (async () => {
        let lastError = null;
        for (let attempt = 0; attempt < 2; attempt++) {
            try {
                const response = await fetch("/archive", { cache: "no-store" });
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const dates = await response.json();
                if (!Array.isArray(dates)) throw new Error("Archive list format incorrect");

                const today = getCurrentKyivDateString();
                dates.sort((a, b) => b.localeCompare(a));
                const filtered = dates.filter(d => d <= today);

                archiveDatesCache = filtered;
                writeArchiveDatesToSessionCache(filtered);
                return filtered;
            } catch (err) {
                lastError = err;
                if (attempt === 0) await sleep(250);
            }
        }

        const stale = readArchiveDatesFromSessionCache(true);
        if (stale) {
            console.warn("Using stale archive dates cache due fetch error.");
            archiveDatesCache = stale;
            return stale;
        }

        throw lastError || new Error("Failed to fetch archive dates");
    })().finally(() => {
        archiveDatesInFlight = null;
    });

    return archiveDatesInFlight;
}

function saveGameState() {
    const storageKey = getCurrentGameStateKey();
    if (!storageKey) return;

    const hasProgressToSave = (
        gameState.guesses.length > 0
        || gameState.hints.length > 0
        || gameState.guessCount > 0
        || gameState.hintCount > 0
        || didWin
        || didGiveUp
        || Boolean(lastWord)
        || Boolean(giveUpWord)
    );
    if (!hasProgressToSave) return;

    const twitchChannel = normalizeTwitchChannel(
        twitchConnectionState.connection?.twitch_login || twitchChatState.channel || ""
    );
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
        giveUpWord,
        twitchChannel: twitchChannel || null,
        updatedAt: Date.now()
    };
    try {
        localStorage.setItem(storageKey, JSON.stringify(state));
        dispatchTwitchLeaderboardStorageChanged(storageKey);
    } catch (e) {
        console.error("Failed to save game state to localStorage:", e);
    }
}

function dispatchTwitchLeaderboardStorageChanged(storageKey = null) {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent(TWITCH_LEADERBOARD_STORAGE_EVENT, {
        detail: { storageKey }
    }));
}

function clearStoredGameState(storageKey) {
    if (!storageKey) return;
    try {
        localStorage.removeItem(storageKey);
        dispatchTwitchLeaderboardStorageChanged(storageKey);
    } catch (err) {
        console.warn("Cannot remove game state from localStorage:", err);
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
        const storageKey = getCurrentGameStateKey();
        if (!storageKey) return;

        const guessCountElem = document.getElementById("guessCount");
        const howToPlayBlock = document.getElementById("howToPlayBlock");
        const privacyPolicyBlock = document.getElementById("privacyPolicyBlock");
        let savedState = null;

        try {
            savedState = localStorage.getItem(storageKey);
        } catch (e) {
            console.warn("Cannot read game state from localStorage:", e);
            resetUIForActiveGame();
            if (howToPlayBlock) howToPlayBlock.style.display = "";
            if (privacyPolicyBlock) privacyPolicyBlock.style.display = "";
            return;
        }

        if (!savedState) {
            resetUIForActiveGame();
            if (howToPlayBlock) howToPlayBlock.style.display = ""; // Show on new game
            if (privacyPolicyBlock) privacyPolicyBlock.style.display = ""; // Show on new game
        return;
    }

    try {
        const state = JSON.parse(savedState);
        if (currentCustomGameId && (state?.didWin || state?.didGiveUp)) {
            const winningGuess = findStoredTwitchWinningGuess(state);
            if (winningGuess) {
                upsertTwitchLeaderboardSolve({
                    channel: state.twitchChannel || state.twitch_channel || "",
                    gameKey: deriveGameKeyFromStoredGameStateKey(storageKey),
                    userLogin: winningGuess.submittedByLogin || "",
                    userName: winningGuess.submittedByName || winningGuess.submittedBy || "",
                    solvedAt: parseStoredTimestamp(winningGuess.createdAt)
                        || parseStoredTimestamp(state.updatedAt)
                        || getStoredGameSolvedAt(storageKey, state, winningGuess)
                });
            }
            clearStoredGameState(storageKey);
            resetRuntimeGameState();
            resetUIForActiveGame();
            if (guessCountElem) guessCountElem.textContent = "0";
            updateHintCountDisplay();
            renderCurrentGuesses();
            if (howToPlayBlock) howToPlayBlock.style.display = "";
            if (privacyPolicyBlock) privacyPolicyBlock.style.display = "";
            return;
        }

        gameState.guesses = enrichEntriesWithRankingData(hydrateStoredEntries(state.guesses));
        gameState.hints = enrichEntriesWithRankingData(hydrateStoredEntries(state.hints));
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
        renderCurrentGuesses();

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
            clearStoredGameState(storageKey);
            resetUIForActiveGame();
            if (howToPlayBlock) howToPlayBlock.style.display = "";
            if (privacyPolicyBlock) privacyPolicyBlock.style.display = "";
        }
    }

function showWinMessageUI() {
    const congratsBlock = document.getElementById("congratsBlock");
    const guessInput = document.getElementById("guessInput");
    const hintButton = document.getElementById("hintButton");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const giveUpBtn = document.getElementById("giveUpBtn");

    if (congratsBlock) {
        const congratsTitle = document.getElementById("congratsTitle");
        if (congratsTitle) congratsTitle.textContent = "Вітаємо!";

        const gameNum = getCurrentGameNumber();
        setCongratsMessage(
            gameNum
                ? `Ви знайшли секретне слово #${gameNum} за ${gameState.guessCount} спроб(и)!`
                : `Ви знайшли кастомне слово за ${gameState.guessCount} спроб(и)!`
        );
        congratsBlock.classList.remove("hidden");
    }
    if (guessInput) guessInput.disabled = true;
    if (hintButton) hintButton.disabled = true;
    if (giveUpBtn) giveUpBtn.disabled = true;
    if (closestWordsBtn) closestWordsBtn.classList.remove("hidden");
}

function showLoseMessageUI(secretWord) {
    const congratsBlock = document.getElementById("congratsBlock");
    const guessInput = document.getElementById("guessInput");
    const hintButton = document.getElementById("hintButton");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const giveUpBtn = document.getElementById("giveUpBtn");

    if (!congratsBlock) return;
    const congratsTitle = document.getElementById("congratsTitle");
    if (congratsTitle) congratsTitle.textContent = "Нехай щастить наступного разу!";

    const gameNum = getCurrentGameNumber();
    setCongratsMessage(
        gameNum
            ? `Ви здалися на слові #${gameNum} за ${gameState.guessCount} спроб(и).\nСлово було: "${secretWord}".`
            : `Ви здалися в кастомній грі за ${gameState.guessCount} спроб(и).\nСлово було: "${secretWord}".`
    );
    congratsBlock.classList.remove("hidden");

    if (guessInput) guessInput.disabled = true;
    if (hintButton) hintButton.disabled = true;
    if (giveUpBtn) giveUpBtn.disabled = true;
    if (closestWordsBtn) closestWordsBtn.classList.remove("hidden");
}

function resetUIForActiveGame() {
    const congratsBlock = document.getElementById("congratsBlock");
    const guessInput = document.getElementById("guessInput");
    const hintButton = document.getElementById("hintButton");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const giveUpBtn = document.getElementById("giveUpBtn");
    // const authorshipBtn = document.getElementById("authorshipBtn"); // Зазвичай завжди активна

    if (congratsBlock) congratsBlock.classList.add("hidden");
    if (closestWordsBtn) closestWordsBtn.classList.add("hidden");

    if (guessInput) guessInput.disabled = false;
    if (hintButton) hintButton.disabled = false;
    if (giveUpBtn) giveUpBtn.disabled = false;
    // if (authorshipBtn) authorshipBtn.disabled = false;
}

function endGameAsWin() {
    if (didWin) return;
    didWin = true;
    didGiveUp = false;
    showWinMessageUI();
    if (currentCustomGameId) {
        clearStoredGameState(getCurrentGameStateKey());
        return;
    }
    saveGameState();
}

function endGameAsGiveUp(secretWord) {
    if (didWin || didGiveUp) return;
    didGiveUp = true;
    didWin = false;
    giveUpWord = secretWord;
    showLoseMessageUI(secretWord);
    if (currentCustomGameId) {
        clearStoredGameState(getCurrentGameStateKey());
        return;
    }
    saveGameState();
}

document.addEventListener("DOMContentLoaded", async () => {
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/static/sw.js', { updateViaCache: 'none' })
                .then(registration => {
                    registration.update().catch(() => {});
                    console.log('Service Worker зареєстровано успішно:', registration);
                })
                .catch(error => console.log('Помилка реєстрації Service Worker:', error));
        });
    }

    const guessInput = document.getElementById("guessInput");
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
    const pageContainer = document.querySelector(".container");
    const shareButton = document.getElementById("shareButton");
    const createGameBtn = document.getElementById("createGameBtn");
    const settingsBtn = document.getElementById("settingsBtn");
    const settingsModal = document.getElementById("settingsModal");
    const closeSettingsModal = document.getElementById("closeSettingsModal");
    const twitchHeaderControls = document.getElementById("twitchHeaderControls");
    const twitchInlineControls = document.getElementById("twitchInlineControls");
    const twitchInlineStreamer = document.getElementById("twitchInlineStreamer");
    const twitchConnectButton = document.getElementById("twitchConnectButton");
    const twitchDisconnectButton = document.getElementById("twitchDisconnectButton");
    const twitchChatToggleRow = document.getElementById("twitchChatToggleRow");
    const twitchChatToggle = document.getElementById("twitchChatToggle");
    const twitchLeaderboardInline = document.getElementById("twitchLeaderboardInline");
    const twitchLeaderboardInlineTitle = document.getElementById("twitchLeaderboardInlineTitle");
    const twitchLeaderboardInlineList = document.getElementById("twitchLeaderboardInlineList");
    const twitchLeaderboardSidebar = document.getElementById("twitchLeaderboardSidebar");
    const twitchLeaderboardSidebarTitle = document.getElementById("twitchLeaderboardSidebarTitle");
    const twitchLeaderboardSidebarList = document.getElementById("twitchLeaderboardSidebarList");
    // const readMoreBtn = document.getElementById("readMoreBtn"); // Закоментовано, якщо не використовується

    const authorshipBtn = document.getElementById('authorshipBtn');
    const authorshipModal = document.getElementById('authorshipModal');
    const closeAuthorshipModalBtn = document.getElementById('closeAuthorshipModal');
    const urlParams = new URLSearchParams(window.location.search);
    const customGameIdFromUrl = normalizeGameId(urlParams.get("game"));
    const requestedDateFromUrl = normalizeDateParam(urlParams.get("date"));
    const legacyCustomWordFromUrl = normalizeWord(urlParams.get("custom"));
    const twitchModeFromUrl = urlParams.get("twitch") === "1";
    const twitchChannelFromUrl = normalizeTwitchChannel(urlParams.get("twitch_channel"));
    twitchConnectionState.oauthEnabled = pageContainer?.dataset?.twitchOauthEnabled === "true";

    if (randomGameBtn) randomGameBtn.textContent = "🔀 Випадкова";
    loadSettings();
    applySettingsToForm();

    function syncDropdownPageSpace() {
        if (!pageContainer) return;

        if (!pageContainer.dataset.basePaddingBottom) {
            pageContainer.dataset.basePaddingBottom = window.getComputedStyle(pageContainer).paddingBottom;
        }

        const basePaddingBottom = parseFloat(pageContainer.dataset.basePaddingBottom) || 0;
        pageContainer.style.paddingBottom = `${basePaddingBottom}px`;
        if (!dropdownMenu || dropdownMenu.classList.contains("hidden")) return;

        const containerBottom = pageContainer.getBoundingClientRect().bottom + window.scrollY;
        const dropdownBottom = dropdownMenu.getBoundingClientRect().bottom + window.scrollY;
        const extraSpace = Math.max(0, Math.ceil(dropdownBottom - containerBottom + 16));

        if (extraSpace > 0) {
            pageContainer.style.paddingBottom = `${basePaddingBottom + extraSpace}px`;
        }
    }

    function closeDropdownMenu() {
        if (!dropdownMenu) return;
        dropdownMenu.classList.add("hidden");
        syncDropdownPageSpace();
        if (menuButton) menuButton.setAttribute("aria-expanded", "false");
    }

    function positionDropdownMenu() {
        if (!menuButton || !dropdownMenu || dropdownMenu.classList.contains("hidden")) return;
        if (menuButton) menuButton.setAttribute("aria-expanded", "true");
        syncDropdownPageSpace();
    }

    function toggleDropdownMenu() {
        if (!dropdownMenu) return;

        if (dropdownMenu.classList.contains("hidden")) {
            dropdownMenu.classList.remove("hidden");
            positionDropdownMenu();
            return;
        }

        closeDropdownMenu();
    }

    function applyLoadedRanking(newRanking) {
        rankedWords = newRanking;
        rankedWordLookup = new Map(
            rankedWords
                .filter(item => item && typeof item.word === "string")
                .map(item => [item.word, item])
        );
        MAX_RANK = rankedWords.length > 0 ? Math.max(...rankedWords.map(w => w.rank)) : 0;
    }

    async function startCustomGameByWord(rawWord) {
        const word = normalizeWord(rawWord);
        if (!word) return false;

        saveGameState();
        if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Генеруємо live-гру...</p>';
        if (lastGuessWrapper) lastGuessWrapper.classList.add("hidden");
        if (guessCountElem) guessCountElem.textContent = "...";
        const labelElem = document.getElementById("gameDateLabel");
        if (labelElem) labelElem.textContent = "Генеруємо...";

        try {
            const payload = await fetchRankedWordsByWord(word);
            if (!payload.ok) {
                const errMsg = payload?.data?.error || "Не вдалося згенерувати live-гру.";
                alert(errMsg);
                if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Помилка генерації.</p>';
                return false;
            }

            if (!payload.data || !Array.isArray(payload.data.ranking)) {
                throw new Error("Invalid custom ranking payload");
            }

            const gameId = normalizeGameId(payload.data.game_id);
            if (!/^[0-9a-f]{64}$/.test(gameId)) {
                throw new Error("Invalid custom game id");
            }

            applyLoadedRanking(payload.data.ranking);
            currentCustomGameId = gameId;
            currentGameDate = null;
            resetRuntimeGameState();
            updateGameDateLabel();
            updateUrlForCurrentGame();

            if (guessCountElem) guessCountElem.textContent = "0";
            updateHintCountDisplay();
            if (guessesContainer) guessesContainer.innerHTML = "";
            if (lastGuessWrapper) lastGuessWrapper.classList.add("hidden");
            showInitialInfoBlocks();
            loadGameState();
            if (twitchChatState.enabled) await syncTwitchChatScope();
            if (guessInput) guessInput.focus();
            return true;
        } catch (err) {
            console.error("[Error] startCustomGame failed:", err);
            alert("Помилка генерації live-гри. Див. консоль для деталей.");
            if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Помилка генерації.</p>';
            return false;
        }
    }

    async function startCustomGameByGameId(rawGameId) {
        const gameId = normalizeGameId(rawGameId);
        if (!/^[0-9a-f]{64}$/.test(gameId)) return false;

        saveGameState();
        if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Генеруємо live-гру...</p>';
        if (lastGuessWrapper) lastGuessWrapper.classList.add("hidden");
        if (guessCountElem) guessCountElem.textContent = "...";
        const labelElem = document.getElementById("gameDateLabel");
        if (labelElem) labelElem.textContent = "Генеруємо...";

        try {
            const payload = await fetchRankedWordsByGameId(gameId);
            if (!payload.ok) {
                const errMsg = payload?.data?.error || "Не вдалося завантажити гру.";
                alert(errMsg);
                if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Помилка генерації.</p>';
                return false;
            }

            if (!payload.data || !Array.isArray(payload.data.ranking)) {
                throw new Error("Invalid custom ranking payload");
            }

            applyLoadedRanking(payload.data.ranking);
            currentCustomGameId = gameId;
            currentGameDate = null;
            resetRuntimeGameState();
            updateGameDateLabel();
            updateUrlForCurrentGame();

            if (guessCountElem) guessCountElem.textContent = "0";
            updateHintCountDisplay();
            if (guessesContainer) guessesContainer.innerHTML = "";
            if (lastGuessWrapper) lastGuessWrapper.classList.add("hidden");
            showInitialInfoBlocks();
            loadGameState();
            if (twitchChatState.enabled) await syncTwitchChatScope();
            if (guessInput) guessInput.focus();
            return true;
        } catch (err) {
            console.error("[Error] startCustomGameByGameId failed:", err);
            alert("Помилка генерації live-гри. Див. консоль для деталей.");
            if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Помилка генерації.</p>';
            return false;
        }
    }

    function setTwitchChatStatus(message, state = "idle") {
        if (!twitchInlineControls) return;

        twitchInlineControls.classList.remove("hidden", "live", "connected", "error");
        if (state === "connected") twitchInlineControls.classList.add("connected");
        if (state === "live") twitchInlineControls.classList.add("live");
        if (state === "error") twitchInlineControls.classList.add("error");
        if (message) {
            twitchInlineControls.title = message;
        } else {
            twitchInlineControls.removeAttribute("title");
        }
    }

    function setTwitchChatLastEvent(message) {
        if (!twitchInlineControls) return;
        if (message) {
            twitchInlineControls.title = message;
        }
    }

    function getTwitchLeaderboardChannel() {
        return twitchConnectionState.connection?.twitch_login || twitchChatState.channel || null;
    }

    function getTwitchLeaderboardTitle(channel) {
        if (!channel) {
            return "Лідери чату";
        }
        return `Лідери ${channel}`;
    }

    function getTwitchLeaderboardViews() {
        return [
            {
                container: twitchLeaderboardInline,
                title: twitchLeaderboardInlineTitle,
                list: twitchLeaderboardInlineList,
                limit: TWITCH_INLINE_LEADERBOARD_LIMIT
            },
            {
                container: twitchLeaderboardSidebar,
                title: twitchLeaderboardSidebarTitle,
                list: twitchLeaderboardSidebarList,
                limit: TWITCH_SIDEBAR_LEADERBOARD_LIMIT
            }
        ];
    }

    function getCurrentTwitchLeaderboardGameKey() {
        if (currentCustomGameId) {
            return `custom:${currentCustomGameId}`;
        }
        if (currentGameDate) {
            return `date:${currentGameDate}`;
        }
        return "";
    }

    function isStoredGameStateKey(storageKey) {
        return typeof storageKey === "string" && storageKey.startsWith("gameState_");
    }

    function isTwitchLeaderboardTrackedStorageKey(storageKey) {
        return storageKey === TWITCH_LEADERBOARD_LOG_STORAGE_KEY || isStoredGameStateKey(storageKey);
    }

    function normalizeStoredWinnerLabel(value) {
        return typeof value === "string" ? value.replace(/^@+/, "").trim() : "";
    }

    function parseStoredTimestamp(value) {
        const numericValue = Number(value);
        if (Number.isFinite(numericValue) && numericValue > 0) {
            return numericValue;
        }

        if (typeof value === "string") {
            const parsedValue = Date.parse(value);
            if (Number.isFinite(parsedValue) && parsedValue > 0) {
                return parsedValue;
            }
        }

        return 0;
    }

    function getStoredGameSolvedAt(storageKey, state, winningGuess) {
        const explicitTimestamp = parseStoredTimestamp(winningGuess?.createdAt)
            || parseStoredTimestamp(state?.updatedAt);
        if (explicitTimestamp > 0) {
            return explicitTimestamp;
        }

        const dailyMatch = /^gameState_(\d{4}-\d{2}-\d{2})$/.exec(storageKey || "");
        if (!dailyMatch) {
            return 0;
        }

        const parsedDate = Date.parse(`${dailyMatch[1]}T23:59:59`);
        return Number.isFinite(parsedDate) ? parsedDate : 0;
    }

    function deriveGameKeyFromStoredGameStateKey(storageKey) {
        const dailyMatch = /^gameState_(\d{4}-\d{2}-\d{2})$/.exec(storageKey || "");
        if (dailyMatch) {
            return `date:${dailyMatch[1]}`;
        }

        const customMatch = /^gameState_custom_(.+)$/.exec(storageKey || "");
        if (customMatch) {
            return `custom:${customMatch[1].trim().toLowerCase()}`;
        }

        return "";
    }

    function findStoredTwitchWinningGuess(state) {
        if (!state || !Array.isArray(state.guesses)) {
            return null;
        }

        return state.guesses.find(entry => {
            if (!entry || entry.error || Number(entry.rank) !== 1) {
                return false;
            }

            return entry.source === "twitch"
                || Boolean(entry.submittedByLogin || entry.submittedByName || entry.submittedBy);
        }) || null;
    }

    function isCustomTwitchLeaderboardGameKey(gameKey) {
        return typeof gameKey === "string" && gameKey.startsWith("custom:");
    }

    function getTwitchLeaderboardEntryId(entry) {
        const channel = normalizeTwitchChannel(entry?.channel || "");
        const gameKey = (entry?.gameKey || "").trim().toLowerCase();
        const userKey = normalizeTwitchChannel(entry?.userLogin || "")
            || normalizeTwitchChannel(entry?.userName || "");
        if (!gameKey || !userKey) {
            return "";
        }

        if (isCustomTwitchLeaderboardGameKey(gameKey)) {
            const solvedAt = parseStoredTimestamp(entry?.solvedAt);
            if (solvedAt > 0) {
                return `${channel}|${gameKey}|${userKey}|${solvedAt}`;
            }
        }

        return `${channel}|${gameKey}|${userKey}`;
    }

    function normalizeTwitchLeaderboardEntry(entry) {
        if (!entry || typeof entry !== "object") {
            return null;
        }

        const gameKey = (entry.gameKey || "").trim().toLowerCase();
        const userLogin = normalizeTwitchChannel(entry.userLogin || "");
        const userName = normalizeStoredWinnerLabel(entry.userName) || userLogin || "";
        if (!gameKey || (!userLogin && !userName)) {
            return null;
        }

        return {
            channel: normalizeTwitchChannel(entry.channel || ""),
            gameKey,
            userLogin,
            userName,
            solvedAt: parseStoredTimestamp(entry.solvedAt)
        };
    }

    function readTwitchLeaderboardLogEntries() {
        try {
            const raw = localStorage.getItem(TWITCH_LEADERBOARD_LOG_STORAGE_KEY);
            if (!raw) {
                return [];
            }

            const parsed = JSON.parse(raw);
            if (!Array.isArray(parsed)) {
                return [];
            }

            return parsed
                .map(normalizeTwitchLeaderboardEntry)
                .filter(Boolean);
        } catch (err) {
            console.warn("Cannot read Twitch leaderboard log from localStorage:", err);
            return [];
        }
    }

    function writeTwitchLeaderboardLogEntries(entries) {
        try {
            localStorage.setItem(TWITCH_LEADERBOARD_LOG_STORAGE_KEY, JSON.stringify(entries));
            dispatchTwitchLeaderboardStorageChanged(TWITCH_LEADERBOARD_LOG_STORAGE_KEY);
        } catch (err) {
            console.warn("Cannot write Twitch leaderboard log to localStorage:", err);
        }
    }

    function upsertTwitchLeaderboardSolve(entry) {
        const normalizedEntry = normalizeTwitchLeaderboardEntry(entry);
        if (!normalizedEntry) {
            return false;
        }

        const entryId = getTwitchLeaderboardEntryId(normalizedEntry);
        if (!entryId) {
            return false;
        }

        const entries = readTwitchLeaderboardLogEntries();
        const existingIndex = entries.findIndex(item => getTwitchLeaderboardEntryId(item) === entryId);
        if (existingIndex >= 0) {
            const existingEntry = entries[existingIndex];
            entries[existingIndex] = {
                ...existingEntry,
                channel: normalizedEntry.channel || existingEntry.channel,
                userLogin: normalizedEntry.userLogin || existingEntry.userLogin,
                userName: normalizedEntry.userName || existingEntry.userName,
                solvedAt: Math.max(existingEntry.solvedAt || 0, normalizedEntry.solvedAt || 0)
            };
            writeTwitchLeaderboardLogEntries(entries);
            return true;
        }

        entries.push(normalizedEntry);
        writeTwitchLeaderboardLogEntries(entries);
        return true;
    }

    function syncLegacyTwitchLeaderboardLogFromGameStates() {
        const entries = readTwitchLeaderboardLogEntries();
        const existingIds = new Set(entries.map(getTwitchLeaderboardEntryId).filter(Boolean));
        let didChange = false;

        for (let index = 0; index < localStorage.length; index++) {
            const storageKey = localStorage.key(index);
            if (!isStoredGameStateKey(storageKey)) {
                continue;
            }

            let state = null;
            try {
                const rawState = localStorage.getItem(storageKey);
                state = rawState ? JSON.parse(rawState) : null;
            } catch (err) {
                console.warn("Cannot parse stored game state for Twitch leaderboard migration:", err);
                continue;
            }

            if (!state || typeof state !== "object") {
                continue;
            }

            const winningGuess = findStoredTwitchWinningGuess(state);
            if (!winningGuess) {
                continue;
            }

            const normalizedEntry = normalizeTwitchLeaderboardEntry({
                channel: state.twitchChannel || state.twitch_channel || "",
                gameKey: deriveGameKeyFromStoredGameStateKey(storageKey),
                userLogin: winningGuess.submittedByLogin || "",
                userName: winningGuess.submittedByName || winningGuess.submittedBy || "",
                solvedAt: parseStoredTimestamp(winningGuess.createdAt)
                    || parseStoredTimestamp(state.updatedAt)
                    || getStoredGameSolvedAt(storageKey, state, winningGuess)
            });
            if (!normalizedEntry) {
                continue;
            }

            const entryId = getTwitchLeaderboardEntryId(normalizedEntry);
            if (!entryId || existingIds.has(entryId)) {
                continue;
            }

            existingIds.add(entryId);
            entries.push(normalizedEntry);
            didChange = true;
        }

        if (didChange) {
            writeTwitchLeaderboardLogEntries(entries);
        }
    }

    function getTwitchLeaderboardSolversFromStorage(channel) {
        const normalizedChannel = normalizeTwitchChannel(channel);
        syncLegacyTwitchLeaderboardLogFromGameStates();
        const solverMap = new Map();

        for (const entry of readTwitchLeaderboardLogEntries()) {
            const entryChannel = normalizeTwitchChannel(entry.channel || "");
            if (normalizedChannel && entryChannel && entryChannel !== normalizedChannel) {
                continue;
            }

            const userLogin = normalizeTwitchChannel(entry.userLogin || "");
            const userName = normalizeStoredWinnerLabel(entry.userName) || userLogin || "чатер";
            const solverKey = userLogin || normalizeTwitchChannel(userName);
            if (!solverKey) {
                continue;
            }

            const solvedAtTs = parseStoredTimestamp(entry.solvedAt);
            const existingSolver = solverMap.get(solverKey);
            if (existingSolver) {
                existingSolver.solved_count += 1;
                if (userLogin && !existingSolver.user_login) {
                    existingSolver.user_login = userLogin;
                }
                if (userName) {
                    existingSolver.user_name = userName;
                }
                existingSolver.last_solved_at_ts = Math.max(existingSolver.last_solved_at_ts, solvedAtTs);
                continue;
            }

            solverMap.set(solverKey, {
                user_login: userLogin || solverKey,
                user_name: userName,
                solved_count: 1,
                last_solved_at_ts: solvedAtTs
            });
        }

        return Array.from(solverMap.values())
            .sort((left, right) => {
                if (right.solved_count !== left.solved_count) {
                    return right.solved_count - left.solved_count;
                }
                if (right.last_solved_at_ts !== left.last_solved_at_ts) {
                    return right.last_solved_at_ts - left.last_solved_at_ts;
                }
                return left.user_login.localeCompare(right.user_login, "uk");
            })
            .map(item => ({
                user_login: item.user_login,
                user_name: item.user_name,
                solved_count: item.solved_count
            }));
    }

    function recordCurrentTwitchSolveForLeaderboard(guessEntry) {
        const channel = getTwitchLeaderboardChannel();
        const gameKey = getCurrentTwitchLeaderboardGameKey();
        if (!channel || !gameKey || !guessEntry) {
            return false;
        }

        return upsertTwitchLeaderboardSolve({
            channel,
            gameKey,
            userLogin: guessEntry.submittedByLogin || "",
            userName: guessEntry.submittedByName || guessEntry.submittedBy || "",
            solvedAt: guessEntry.createdAt || Date.now()
        });
    }

    function formatUkrainianWordCount(count) {
        const safeCount = Number.isFinite(Number(count)) ? Math.max(0, Math.trunc(Number(count))) : 0;
        const lastTwoDigits = safeCount % 100;
        const lastDigit = safeCount % 10;
        if (lastTwoDigits >= 11 && lastTwoDigits <= 14) {
            return `${safeCount} слів`;
        }
        if (lastDigit === 1) {
            return `${safeCount} слово`;
        }
        if (lastDigit >= 2 && lastDigit <= 4) {
            return `${safeCount} слова`;
        }
        return `${safeCount} слів`;
    }

    function createTwitchWinnerRow(solver, index) {
        const row = document.createElement("div");
        row.className = "twitchWinnerRow";

        const rank = document.createElement("span");
        rank.className = "twitchWinnerPlace";
        rank.textContent = `${index + 1}.`;

        const name = document.createElement("span");
        name.className = "twitchWinnerName";
        name.textContent = solver?.user_name || solver?.user_login || "чатер";

        const count = document.createElement("span");
        count.className = "twitchWinnerCount";
        const solvedCount = Number.isFinite(Number(solver?.solved_count)) ? Number(solver.solved_count) : 0;
        count.textContent = formatUkrainianWordCount(solvedCount);

        row.append(rank, name, count);
        return row;
    }

    function renderTwitchWinnersListInto(listElem, titleElem, solvers, channel, options = {}) {
        const {
            limit = null,
            emptyText = "Ще ніхто не відгадав слово."
        } = options;

        if (titleElem) {
            titleElem.textContent = getTwitchLeaderboardTitle(channel);
        }
        if (!listElem) return;

        listElem.innerHTML = "";
        const visibleSolvers = Number.isFinite(limit) ? solvers.slice(0, limit) : solvers;

        if (!Array.isArray(visibleSolvers) || visibleSolvers.length === 0) {
            listElem.innerHTML = `<p class="twitchWinnersEmpty">${emptyText}</p>`;
            return;
        }

        visibleSolvers.forEach((solver, index) => {
            listElem.appendChild(createTwitchWinnerRow(solver, index));
        });
    }

    function renderTwitchLeaderboardStatus(message, channel, options = {}) {
        const { statusClass = "twitchWinnersLoading" } = options;
        getTwitchLeaderboardViews().forEach(view => {
            if (view.title) {
                view.title.textContent = getTwitchLeaderboardTitle(channel);
            }
            if (view.list) {
                view.list.innerHTML = `<p class="${statusClass}">${message}</p>`;
            }
        });
    }

    function renderTwitchLeaderboard(solvers, channel) {
        getTwitchLeaderboardViews().forEach(view => {
            renderTwitchWinnersListInto(
                view.list,
                view.title,
                solvers,
                channel,
                {
                    limit: view.limit
                }
            );
        });
    }

    function updateTwitchLeaderboardVisibility() {
        const shouldShow = Boolean(twitchConnectionState.connected && getTwitchLeaderboardChannel());
        if (twitchLeaderboardInline) {
            twitchLeaderboardInline.classList.toggle("hidden", !shouldShow);
        }
        if (twitchLeaderboardSidebar) {
            twitchLeaderboardSidebar.classList.toggle("hidden", !shouldShow);
        }
    }

    function stopTwitchLeaderboardRefreshLoop() {
        if (twitchLeaderboardState.refreshTimerId) {
            window.clearInterval(twitchLeaderboardState.refreshTimerId);
            twitchLeaderboardState.refreshTimerId = null;
        }
    }

    async function refreshTwitchLeaderboard(options = {}) {
        const {
            force = false,
            showLoading = false
        } = options;

        const channel = getTwitchLeaderboardChannel();
        updateTwitchLeaderboardVisibility();

        if (!channel || !twitchConnectionState.connected) {
            twitchLeaderboardState.channel = null;
            twitchLeaderboardState.solvers = [];
            twitchLeaderboardState.lastLoadedAt = 0;
            return;
        }

        if (twitchLeaderboardState.isLoading && !force) return;

        const channelChanged = twitchLeaderboardState.channel !== channel;
        if (
            !force
            && !channelChanged
            && twitchLeaderboardState.lastLoadedAt
            && (Date.now() - twitchLeaderboardState.lastLoadedAt) < 8000
        ) {
            return;
        }

        twitchLeaderboardState.isLoading = true;
        if (showLoading && (channelChanged || twitchLeaderboardState.solvers.length === 0)) {
            renderTwitchLeaderboardStatus("Завантаження рейтингу…", channel);
        }

        try {
            twitchLeaderboardState.channel = channel;
            twitchLeaderboardState.solvers = getTwitchLeaderboardSolversFromStorage(channel);
            twitchLeaderboardState.lastLoadedAt = Date.now();
            renderTwitchLeaderboard(twitchLeaderboardState.solvers, channel);
        } catch (err) {
            console.error("[Error] Failed to build Twitch leaderboard from localStorage:", err);
            if (twitchLeaderboardState.solvers.length === 0 || channelChanged) {
                renderTwitchLeaderboardStatus("Не вдалося завантажити рейтинг.", channel, {
                    statusClass: "twitchWinnersEmpty"
                });
            }
        } finally {
            twitchLeaderboardState.isLoading = false;
        }
    }

    function restartTwitchLeaderboardRefreshLoop() {
        stopTwitchLeaderboardRefreshLoop();
        if (!twitchConnectionState.connected || !getTwitchLeaderboardChannel()) return;

        refreshTwitchLeaderboard({
            showLoading: twitchLeaderboardState.solvers.length === 0
        });
        twitchLeaderboardState.refreshTimerId = window.setInterval(() => {
            refreshTwitchLeaderboard();
        }, TWITCH_LEADERBOARD_REFRESH_MS);
    }

    function stopTwitchChatMode() {
        twitchChatState.enabled = false;
        twitchChatState.channel = null;
        twitchChatState.gameScope = null;
        twitchChatState.lastEventId = 0;
        twitchChatState.errorCount = 0;
        twitchChatState.isPolling = false;

        if (twitchChatState.pollTimerId) {
            window.clearInterval(twitchChatState.pollTimerId);
            twitchChatState.pollTimerId = null;
        }
        if (twitchChatState.targetHeartbeatId) {
            window.clearInterval(twitchChatState.targetHeartbeatId);
            twitchChatState.targetHeartbeatId = null;
        }

        if (twitchHeaderControls && !twitchConnectionState.connected) {
            twitchHeaderControls.classList.add("hidden");
        }
        if (twitchInlineControls && !twitchConnectionState.connected) {
            twitchInlineControls.classList.add("hidden");
            twitchInlineControls.classList.remove("live", "connected", "error");
            twitchInlineControls.removeAttribute("title");
        }
        if (twitchChatToggleRow && !twitchConnectionState.connected) {
            twitchChatToggleRow.classList.add("hidden");
        }
        setTwitchChatLastEvent("");
        updateTwitchConnectPanel();
    }

    function updateTwitchConnectPanel() {
        const connection = twitchConnectionState.connection;
        const activeForConnectedChannel = Boolean(
            twitchChatState.enabled &&
            connection?.twitch_login &&
            twitchChatState.channel === connection.twitch_login
        );

        if (twitchConnectionState.connected && connection) {
            if (twitchConnectButton) twitchConnectButton.classList.add("hidden");
            if (twitchDisconnectButton) twitchDisconnectButton.classList.remove("hidden");
            if (twitchHeaderControls) twitchHeaderControls.classList.remove("hidden");
            if (twitchInlineControls) twitchInlineControls.classList.remove("hidden");
            if (twitchChatToggleRow) twitchChatToggleRow.classList.remove("hidden");
            if (twitchInlineStreamer) twitchInlineStreamer.textContent = connection.twitch_login;
            if (twitchChatToggle) {
                twitchChatToggle.checked = activeForConnectedChannel;
                twitchChatToggle.disabled = false;
            }
            if (!twitchChatState.enabled) {
                setTwitchChatStatus(`Twitch chat для #${connection.twitch_login} вимкнено`, "connected");
                setTwitchChatLastEvent("");
            }
            return;
        }

        if (twitchConnectionState.oauthEnabled) {
            if (twitchConnectButton) {
                twitchConnectButton.classList.remove("hidden");
                twitchConnectButton.href = twitchConnectionState.connectUrl || "/auth/twitch/start";
                setButtonTextPreservingIcon(twitchConnectButton, "Підключити Twitch");
                twitchConnectButton.title = twitchConnectionState.workerReady
                    ? "Підключити Twitch для чату стріму"
                    : "OAuth готовий, але worker на сервері ще не запущений";
            }
            if (twitchDisconnectButton) twitchDisconnectButton.classList.add("hidden");
            if (twitchHeaderControls) twitchHeaderControls.classList.add("hidden");
            if (twitchInlineControls) {
                twitchInlineControls.classList.add("hidden");
                twitchInlineControls.classList.remove("live", "connected", "error");
                twitchInlineControls.removeAttribute("title");
            }
            if (twitchChatToggleRow) twitchChatToggleRow.classList.add("hidden");
            if (twitchChatToggle) {
                twitchChatToggle.checked = false;
                twitchChatToggle.disabled = true;
            }
            return;
        }

        if (twitchConnectButton) twitchConnectButton.classList.add("hidden");
        if (twitchDisconnectButton) twitchDisconnectButton.classList.add("hidden");
        if (twitchHeaderControls) twitchHeaderControls.classList.add("hidden");
        if (twitchInlineControls) {
            twitchInlineControls.classList.add("hidden");
            twitchInlineControls.classList.remove("live", "connected", "error");
            twitchInlineControls.removeAttribute("title");
        }
        if (twitchChatToggleRow) twitchChatToggleRow.classList.add("hidden");
        if (twitchChatToggle) {
            twitchChatToggle.checked = false;
            twitchChatToggle.disabled = true;
        }
    }

    async function loadTwitchConnectionState() {
        try {
            const next = `${window.location.pathname}${window.location.search}`;
            const payload = await fetchTwitchConnectionStatus(next);
            if (!payload.ok) {
                throw new Error(payload?.data?.error || `HTTP ${payload.status}`);
            }

            twitchConnectionState.oauthEnabled = Boolean(payload?.data?.oauth_enabled);
            twitchConnectionState.workerReady = Boolean(payload?.data?.worker_ready);
            twitchConnectionState.connected = Boolean(payload?.data?.connected);
            twitchConnectionState.connection = payload?.data?.connection || null;
            twitchConnectionState.connectUrl = payload?.data?.connect_url || null;
        } catch (err) {
            console.error("[Error] Failed to load Twitch connection state:", err);
        } finally {
            updateTwitchConnectPanel();
            updateTwitchLeaderboardVisibility();
            if (twitchConnectionState.connected) {
                restartTwitchLeaderboardRefreshLoop();
            } else {
                stopTwitchLeaderboardRefreshLoop();
            }
        }
    }

    function refreshTwitchPollTimer() {
        if (twitchChatState.pollTimerId) {
            window.clearInterval(twitchChatState.pollTimerId);
            twitchChatState.pollTimerId = null;
        }

        if (!twitchChatState.enabled) return;
        twitchChatState.pollTimerId = window.setInterval(
            pollTwitchChatEventsLoop,
            twitchChatState.pollIntervalMs
        );
    }

    async function enableTwitchChatMode(channel) {
        const normalizedChannel = normalizeTwitchChannel(channel);
        if (!normalizedChannel) {
            throw new Error("Не вдалося визначити Twitch-канал.");
        }

        twitchChatState.enabled = true;
        twitchChatState.channel = normalizedChannel;
        setTwitchChatStatus("Twitch chat: підключення…");
        setTwitchChatLastEvent("");

        await syncTwitchChatScope();
        restartTwitchTargetHeartbeat();
        await pollTwitchChatEventsLoop();
        refreshTwitchPollTimer();
        updateTwitchConnectPanel();
    }

    function getCurrentTwitchGameScope() {
        if (currentCustomGameId) {
            return normalizeTwitchGameScope(`custom:${currentCustomGameId}`);
        }

        const todayStr = getCurrentKyivDateString();
        if (currentGameDate && currentGameDate !== todayStr) {
            return normalizeTwitchGameScope(`date:${currentGameDate}`);
        }

        return "daily:current";
    }

    function getTwitchGameScopeLabel(gameScope) {
        if (!gameScope) return "усі активні ігри";
        if (gameScope === "daily:current") return "daily";
        if (gameScope.startsWith("date:")) return gameScope.slice(5);
        if (gameScope.startsWith("custom:")) return "custom";
        return gameScope;
    }

    function formatTwitchChatterLabel(chatterName, chatterLogin) {
        const label = (chatterName || chatterLogin || "").trim();
        return label ? `@${label}` : "чат";
    }

    async function submitGuessWord(rawWord, options = {}) {
        const {
            source = "manual",
            chatterName = "",
            chatterLogin = ""
        } = options;

        if (didWin || didGiveUp) return false;

        const isTwitchSource = source === "twitch";
        let word = normalizeWord(rawWord);
        if (!word) return false;

        hideInitialInfoBlocks();
        await fetchAllowedWords();

        const resolvedGuess = await resolveGuessWord(word);
        word = resolvedGuess.resolvedWord;
        if (!word) return false;

        if (!isTwitchSource && resolvedGuess.wasChanged && guessInput) {
            guessInput.value = word;
        }

        if (getCombinedEntries().some(entry => entry.word === word)) {
            if (isTwitchSource) {
                setTwitchChatLastEvent(`Пропущено дубль: ${formatTwitchChatterLabel(chatterName, chatterLogin)} -> ${word}`);
            } else if (lastGuessDisplay && lastGuessWrapper) {
                const errorMsgElement = document.createElement("div");
                errorMsgElement.textContent = `Слово "${word}" вже вгадано`;
                if (guessInput) guessInput.value = "";
                errorMsgElement.style.cssText = "color: #ffffff; padding: 0px 12px; text-align: left; font-style: italic;";
                lastGuessDisplay.innerHTML = "";
                lastGuessDisplay.appendChild(errorMsgElement);
                lastGuessWrapper.classList.remove("hidden");
            }

            if (!isTwitchSource && guessInput) guessInput.focus();
            return false;
        }

        if (allowedWordsLoaded && !allowedWords.has(word)) {
            if (isTwitchSource) {
                setTwitchChatLastEvent(`Пропущено невідоме слово: ${formatTwitchChatterLabel(chatterName, chatterLogin)} -> ${word}`);
            } else if (lastGuessDisplay && lastGuessWrapper) {
                const errorMsgElement = document.createElement("div");
                errorMsgElement.textContent = "Вибачте, я не знаю цього слова";
                errorMsgElement.style.cssText = "color: #ffffff; padding: 0px 12px; text-align: left; font-style: italic;";
                lastGuessDisplay.innerHTML = "";
                lastGuessDisplay.appendChild(errorMsgElement);
                lastGuessWrapper.classList.remove("hidden");
            }

            if (!isTwitchSource && guessInput) guessInput.focus();
            return false;
        }

        const match = getRankedWordEntry(word);
        const data = match
            ? { rank: match.rank, similarity: match.similarity }
            : { rank: Infinity, error: true, errorMessage: "Цього слова немає у рейтингу цього дня." };

        gameState.guessCount++;
        if (guessCountElem) guessCountElem.textContent = gameState.guessCount;
        lastWord = word;
        gameState.guesses.push(createGameEntry({
            word,
            rank: data.rank,
            similarity: data.similarity,
            error: data.error || false,
            errorMessage: data.errorMessage,
            source: isTwitchSource ? "twitch" : "manual",
            submittedBy: isTwitchSource ? formatTwitchChatterLabel(chatterName, chatterLogin) : null,
            submittedByLogin: isTwitchSource ? (normalizeTwitchChannel(chatterLogin) || null) : null,
            submittedByName: isTwitchSource ? ((chatterName || chatterLogin || "").trim() || null) : null,
            createdAt: new Date().toISOString()
        }));

        if (isTwitchSource) {
            setTwitchChatLastEvent(`${formatTwitchChatterLabel(chatterName, chatterLogin)} -> ${word}`);
        }

        if (!data.error) {
            if (data.rank < bestRank) bestRank = data.rank;
            if (data.rank === 1) {
                if (isTwitchSource) {
                    recordCurrentTwitchSolveForLeaderboard(gameState.guesses[gameState.guesses.length - 1]);
                }
                renderCurrentGuesses();
                endGameAsWin();
                return true;
            }
        }

        renderCurrentGuesses();
        if (!isTwitchSource && guessInput) {
            guessInput.value = "";
            guessInput.focus();
        }
        saveGameState();
        return true;
    }

    function enqueueGuessSubmission(rawWord, options = {}) {
        guessSubmissionQueue = guessSubmissionQueue
            .then(() => submitGuessWord(rawWord, options))
            .catch(err => {
                console.error("[Error] submitGuessWord failed:", err);
                return false;
            });

        return guessSubmissionQueue;
    }

    async function pollTwitchChatEventsLoop() {
        if (!twitchChatState.enabled || twitchChatState.isPolling) return;

        twitchChatState.isPolling = true;
        try {
            const payload = await fetchTwitchChatEvents(
                twitchChatState.lastEventId,
                twitchChatState.channel,
                twitchChatState.gameScope,
                25
            );

            if (!payload.ok) {
                throw new Error(payload?.data?.error || `HTTP ${payload.status}`);
            }

            const events = Array.isArray(payload?.data?.events) ? payload.data.events : [];
            for (const event of events) {
                const eventId = Number(event?.id);
                if (Number.isFinite(eventId) && eventId > twitchChatState.lastEventId) {
                    twitchChatState.lastEventId = eventId;
                }

                const word = normalizeWord(event?.word);
                if (!word) continue;

                await enqueueGuessSubmission(word, {
                    source: "twitch",
                    chatterName: event?.user_name || "",
                    chatterLogin: event?.user_login || ""
                });
            }

            twitchChatState.errorCount = 0;
            const channelLabel = twitchChatState.channel ? `#${twitchChatState.channel}` : "активного каналу";
            setTwitchChatStatus(
                `Twitch chat: ${channelLabel} · ${getTwitchGameScopeLabel(twitchChatState.gameScope)}`,
                "live"
            );
        } catch (err) {
            twitchChatState.errorCount += 1;
            console.error("[Error] Twitch chat polling failed:", err);
            setTwitchChatStatus("Twitch chat тимчасово недоступний. Пробую перепідключитися…", "error");
        } finally {
            twitchChatState.isPolling = false;
        }
    }

    async function syncTwitchChatScope() {
        if (!twitchChatState.enabled) return;

        const nextGameScope = getCurrentTwitchGameScope();
        twitchChatState.gameScope = nextGameScope;

        const pageUrl = `${window.location.origin}${window.location.pathname}${window.location.search}`;
        const payload = await registerTwitchChatTarget(
            twitchChatState.channel,
            nextGameScope,
            pageUrl
        );
        if (!payload.ok) {
            throw new Error(payload?.data?.error || `HTTP ${payload.status}`);
        }

        twitchChatState.pollIntervalMs = Number.isFinite(payload?.data?.poll_interval_ms)
            ? payload.data.poll_interval_ms
            : twitchChatState.pollIntervalMs;
        const targetTtlSeconds = Number.isFinite(payload?.data?.target_ttl_seconds)
            ? payload.data.target_ttl_seconds
            : 90;
        twitchChatState.targetRefreshMs = Math.max(15000, Math.floor(targetTtlSeconds * 500));
        const latestEventId = Number.isFinite(payload?.data?.latest_event_id)
            ? payload.data.latest_event_id
            : 0;
        if (!Number.isFinite(twitchChatState.lastEventId) || twitchChatState.lastEventId <= 0) {
            twitchChatState.lastEventId = latestEventId;
        }

        const channelLabel = twitchChatState.channel ? `#${twitchChatState.channel}` : "активного каналу";
        setTwitchChatStatus(`Twitch chat: ${channelLabel} · ${getTwitchGameScopeLabel(nextGameScope)}`, "live");
        setTwitchChatLastEvent("");
    }

    function restartTwitchTargetHeartbeat() {
        if (twitchChatState.targetHeartbeatId) {
            window.clearInterval(twitchChatState.targetHeartbeatId);
            twitchChatState.targetHeartbeatId = null;
        }

        if (!twitchChatState.enabled) return;

        twitchChatState.targetHeartbeatId = window.setInterval(async () => {
            try {
                await syncTwitchChatScope();
            } catch (err) {
                console.error("[Error] Twitch target heartbeat failed:", err);
                setTwitchChatStatus("Не вдалося оновити активну Twitch-гру. Пробую ще раз…", "error");
            }
        }, twitchChatState.targetRefreshMs);
    }

    async function initializeTwitchChatMode() {
        if (!twitchModeFromUrl) return;

        try {
            const fallbackConnectedChannel = twitchConnectionState.connection?.twitch_login || null;
            await enableTwitchChatMode(twitchChannelFromUrl || fallbackConnectedChannel || null);
        } catch (err) {
            console.error("[Error] initializeTwitchChatMode failed:", err);
            setTwitchChatStatus("Не вдалося запустити Twitch chat mode.", "error");
            setTwitchChatLastEvent("Перевірте Twitch підключення і worker на сервері.");
        }
    }

    const todayStr = getCurrentKyivDateString();
    if (!currentGameDate && !currentCustomGameId) {
        currentGameDate = requestedDateFromUrl || todayStr;
        resetRuntimeGameState();
    }
    dayNumber = computeGameNumber(todayStr);
    updateGameDateLabel(); // Показуємо номер гри одразу, без очікування API

    // Великий словник вантажимо у фоні, щоб не блокувати перший рендер.
    fetchAllowedWords();

    let loadedInitialGame = false;
    if (customGameIdFromUrl) {
        loadedInitialGame = await startCustomGameByGameId(customGameIdFromUrl);
    } else if (legacyCustomWordFromUrl) {
        loadedInitialGame = await startCustomGameByWord(legacyCustomWordFromUrl);
    }

    if (!loadedInitialGame) {
        try {
            currentCustomGameId = null;
            const dateParam = currentGameDate === todayStr ? null : currentGameDate;
            const response = await fetchRankedWords(dateParam);
            if (!Array.isArray(response)) throw new Error("Ranked words data is not an array");
            applyLoadedRanking(response);
            updateUrlForCurrentGame();
            console.log(`Loaded ${rankedWords.length} ranked words for ${currentGameDate}. Max rank: ${MAX_RANK}`);
        } catch (err) {
            console.error(`[Error] fetchRankedWords failed for ${currentGameDate}:`, err);
            if (guessInput) guessInput.disabled = true;
            if (document.getElementById("gameDateLabel")) document.getElementById("gameDateLabel").textContent = "Помилка слів";
            return;
        }
    }

    loadGameState(); // Це оновить видимість howToPlayBlock та privacyPolicyBlock
    await loadTwitchConnectionState();
    await initializeTwitchChatMode();

    async function handleSubmit() {
        if (!guessInput) return;
        await enqueueGuessSubmission(guessInput.value, { source: "manual" });
    }

    async function loadArchive(game_date) {
        saveGameState();
        console.log(`Loading archive for date: ${game_date}`);

        if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Завантаження гри...</p>';
        if (lastGuessWrapper) lastGuessWrapper.classList.add('hidden');
        if (guessCountElem) guessCountElem.textContent = '...';
        if (document.getElementById("gameDateLabel")) document.getElementById("gameDateLabel").textContent = 'Завантаження...';

        try {
            let archiveData = null;
            let responseError = null;

            for (let attempt = 0; attempt < 2; attempt++) {
                const response = await fetch(`/archive/${game_date}`, { cache: "no-store" });
                if (response.ok) {
                    archiveData = await response.json();
                    break;
                }

                responseError = response;
                if (response.status >= 500 && attempt === 0) {
                    await sleep(300);
                    continue;
                }
                break;
            }

            if (!archiveData) {
                const status = responseError ? responseError.status : 0;
                alert(status === 404 ? `Архів для дати ${game_date} не знайдено.` : "Помилка завантаження архіву.");
                if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Помилка завантаження.</p>';
                return;
            }

            if (!archiveData || !Array.isArray(archiveData.ranking)) throw new Error("Invalid archive data format");

            applyLoadedRanking(archiveData.ranking);
            currentGameDate = game_date;
            currentCustomGameId = null;
            resetRuntimeGameState();
            updateUrlForCurrentGame();

            console.log(`Loaded ${rankedWords.length} words for ${game_date}. Max rank: ${MAX_RANK}`);
            updateGameDateLabel();
            if (guessCountElem) guessCountElem.textContent = '0';
            updateHintCountDisplay(); // Оновлюємо лічільник підказок
            if (guessesContainer) guessesContainer.innerHTML = '';
            if (lastGuessWrapper) lastGuessWrapper.classList.add('hidden');

            showInitialInfoBlocks(); // Показуємо інфо-блоки для нової гри
            loadGameState(); // Завантажуємо стан, якщо він є, або ініціалізуємо
            if (twitchChatState.enabled) await syncTwitchChatScope();

        } catch (err) {
            console.error("Error loading archive for date", game_date, err);
            alert("Помилка завантаження архіву. Див. консоль для деталей.");
            if (guessesContainer) guessesContainer.innerHTML = '<p style="text-align: center;">Помилка завантаження.</p>';
        } finally {
            if (previousGamesModal) previousGamesModal.classList.add("hidden");
            closeDropdownMenu();
            if (guessInput) guessInput.focus();
        }
    }

    if (guessInput) guessInput.addEventListener("keypress", e => e.key === "Enter" && handleSubmit());

    if (createGameBtn) {
        createGameBtn.addEventListener("click", () => {
            closeDropdownMenu();
            window.location.href = "/create-game";
        });
    }

    if (settingsBtn) {
        settingsBtn.addEventListener("click", () => {
            if (settingsModal) settingsModal.classList.remove("hidden");
            closeDropdownMenu();
        });
    }

    if (closeSettingsModal) {
        closeSettingsModal.addEventListener("click", () => {
            if (settingsModal) settingsModal.classList.add("hidden");
        });
    }

    if (settingsModal) {
        settingsModal.addEventListener("click", e => {
            if (e.target === settingsModal) settingsModal.classList.add("hidden");
        });
    }

    document.querySelectorAll('input[name="hintMode"]').forEach(input => {
        input.addEventListener("change", event => {
            settings.hintMode = normalizeHintMode(event.target.value);
            saveSettings();
            applySettingsToForm();
        });
    });

    document.querySelectorAll('input[name="sortMode"]').forEach(input => {
        input.addEventListener("change", event => {
            settings.sortMode = normalizeSortMode(event.target.value);
            saveSettings();
            applySettingsToForm();
            renderCurrentGuesses();
        });
    });

    if (hintButton) {
        hintButton.addEventListener("click", () => {
            if (didWin || didGiveUp) return;
            hideInitialInfoBlocks(); // Ховаємо інфо-блоки при дії
            if (rankedWords.length === 0) {
                alert("Список слів ще не завантажено!");
                return;
            }
            const nextHintRank = getNextHintRank(bestRank, getCombinedEntries(), rankedWords, MAX_RANK, settings.hintMode);
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
            gameState.hints.push(createGameEntry({
                word: hintWordObj.word,
                rank: hintWordObj.rank,
                similarity: hintWordObj.similarity,
                error: false,
                isHint: true
            }));
            if (hintWordObj.rank < bestRank) bestRank = hintWordObj.rank;
            renderCurrentGuesses();
            if (hintWordObj.rank === 1) endGameAsWin();
            else saveGameState();
            closeDropdownMenu();
        });
    }

    if (giveUpBtn) giveUpBtn.addEventListener("click", () => {
        if (didWin || didGiveUp) return;
        if (giveUpModal) giveUpModal.classList.remove("hidden");
        closeDropdownMenu();
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

            gameState.guesses.push(createGameEntry({
                word: secretWord,
                rank: 1,
                similarity: secretWordObj?.similarity,
                error: false,
                gaveUp: true
            }));
            lastWord = secretWord;
            bestRank = 1;
            renderCurrentGuesses();
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
            closeDropdownMenu();
            if (previousGamesList) previousGamesList.innerHTML = "<p>Завантаження архіву...</p>";
            try {
                const filtered = await fetchArchiveDates();

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
            closeDropdownMenu();
            try {
                const validDates = await fetchArchiveDates();
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
        else topN.forEach(item => closestWordsList.appendChild(createGuessItem({
            word: item.word,
            rank: item.rank,
            similarity: item.similarity,
            error: false
        }, MAX_RANK)));
        if (closestWordsModal) closestWordsModal.classList.remove("hidden");
    }
    if (closestWordsBtn) closestWordsBtn.addEventListener("click", showClosestWords);
    if (closeModalBtn) closeModalBtn.addEventListener("click", () => closestWordsModal && closestWordsModal.classList.add("hidden"));
    if (closestWordsModal) closestWordsModal.addEventListener('click', e => e.target === closestWordsModal && closestWordsModal.classList.add('hidden'));

    if (twitchChatToggle) {
        twitchChatToggle.addEventListener("change", async () => {
            if (twitchChatToggle.checked) {
                const channel = twitchConnectionState.connection?.twitch_login || null;
                if (!channel) {
                    twitchChatToggle.checked = false;
                    return;
                }

                try {
                    await enableTwitchChatMode(channel);
                } catch (err) {
                    console.error("[Error] Failed to enable Twitch chat for current game:", err);
                    twitchChatToggle.checked = false;
                    setTwitchChatStatus("Не вдалося увімкнути чат для цієї гри.", "error");
                    setTwitchChatLastEvent("Спробуй ще раз або перевір worker на сервері.");
                }
                return;
            }

            stopTwitchChatMode();
        });
    }

    if (twitchDisconnectButton) {
        twitchDisconnectButton.addEventListener("click", async () => {
            try {
                const payload = await disconnectTwitchConnection();
                if (!payload.ok) {
                    throw new Error(payload?.data?.error || `HTTP ${payload.status}`);
                }

                twitchConnectionState.connected = false;
                twitchConnectionState.connection = null;
                stopTwitchChatMode();
                updateTwitchConnectPanel();
                stopTwitchLeaderboardRefreshLoop();
                updateTwitchLeaderboardVisibility();
                twitchLeaderboardState.channel = null;
                twitchLeaderboardState.solvers = [];
                twitchLeaderboardState.lastLoadedAt = 0;
            } catch (err) {
                console.error("[Error] Failed to disconnect Twitch:", err);
                alert("Не вдалося відключити Twitch.");
            }
        });
    }

    window.addEventListener(TWITCH_LEADERBOARD_STORAGE_EVENT, event => {
        const storageKey = event?.detail?.storageKey;
        if (storageKey && !isTwitchLeaderboardTrackedStorageKey(storageKey)) {
            return;
        }
        refreshTwitchLeaderboard({ force: true });
    });

    window.addEventListener("storage", event => {
        if (event.key && !isTwitchLeaderboardTrackedStorageKey(event.key)) {
            return;
        }
        refreshTwitchLeaderboard({ force: true });
    });

    if (menuButton && dropdownMenu) {
        menuButton.setAttribute("aria-haspopup", "menu");
        menuButton.setAttribute("aria-expanded", "false");
        menuButton.addEventListener("click", e => {
            e.stopPropagation();
            toggleDropdownMenu();
        });
    }
    document.addEventListener("click", e => {
        if (menuButton && dropdownMenu && !menuButton.contains(e.target) && !dropdownMenu.contains(e.target)) {
            closeDropdownMenu();
        }
    });
    window.addEventListener("resize", positionDropdownMenu);
    window.addEventListener("beforeunload", () => {
        if (twitchChatState.pollTimerId) {
            window.clearInterval(twitchChatState.pollTimerId);
            twitchChatState.pollTimerId = null;
        }
        if (twitchChatState.targetHeartbeatId) {
            window.clearInterval(twitchChatState.targetHeartbeatId);
            twitchChatState.targetHeartbeatId = null;
        }
        stopTwitchLeaderboardRefreshLoop();
    });

    if (shareButton) {
        shareButton.addEventListener('click', async () => {
            if (!didWin && !didGiveUp) {
                alert("Ви ще не завершили гру, щоб поділитися результатом!");
                return;
            }
            const gameNum = currentGameDate ? computeGameNumber(currentGameDate) : null;
            const shareTitle = gameNum ? `Словозв'яз #${gameNum}` : "Словозв'яз (кастом)";
            let shareText = `${shareTitle}\nСпроб: ${gameState.guessCount}\nПідказок: ${gameState.hintCount}\n`;
            const closestGuessRank = gameState.guesses.filter(g => !g.error && g.rank !== 1 && g.rank !== Infinity).reduce((minRank, g) => Math.min(minRank, g.rank), Infinity);
            if (didWin) shareText += "✅ Знайдено!\n";
            else if (didGiveUp) shareText += `🏳️ Здався. Найближче слово: ${closestGuessRank !== Infinity ? `(ранг ${closestGuessRank})` : '(немає)'}\n`;
            shareText += `\n${window.location.href}`;
            try {
                if (navigator.share) await navigator.share({ title: shareTitle, text: shareText });
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
            closeDropdownMenu();
        });
        closeAuthorshipModalBtn.addEventListener('click', () => authorshipModal.classList.add('hidden'));
        authorshipModal.addEventListener('click', e => e.target === authorshipModal && authorshipModal.classList.add('hidden'));
    }
});
