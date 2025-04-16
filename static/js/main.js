import { fetchRankedWords /* remove submitGuess if not used directly */ } from "./api.js";
import { renderGuesses, createGuessItem } from "./ui.js";

// --- Global State Variables ---
let isGoingUp = false;
let allowedWords = new Set();
let guessCount = 0;
let bestRank = Infinity;
let rankedWords = [];
let guesses = [];
let lastWord = null;
let MAX_RANK = 0;
let dayNumber = null; // Number for the current day's game (from API)
let currentGameDate = null; // YYYY-MM-DD string for the currently loaded game
let didWin = false;
let didGiveUp = false;
let giveUpWord = null; // Stores the secret word if the user gave up

const gameStates = {}; // Cache for states of previously loaded archive games in this session

// --- Helper Functions ---

// Обчислює наступний можливий ранг для підказки
function getNextHintRank(currentBestRank, currentGuesses, currentRankedWords, currentMaxRank) {
    // Need a local 'isGoingUp' or pass it if it should be per-game state dependent
    let localIsGoingUp = isGoingUp; // Assuming hint logic uses global 'isGoingUp' for now

    if (currentBestRank === Infinity) {
        const wordObj = currentRankedWords.find(x => x.rank === 500);
        if (wordObj) return 500;
        return null; // Or maybe suggest rank 1000? Or middle rank?
    }
    if (currentBestRank === 1) return null; // Already found the best word

    const isGuessed = r => currentGuesses.some(g => g.rank === r);

    if (!localIsGoingUp) {
        if (currentBestRank > 2) {
            let candidate = Math.floor(currentBestRank / 2);
            while (candidate >= 2) {
                if (!isGuessed(candidate)) return candidate;
                candidate = Math.floor(candidate / 2);
            }
            // If all halves are guessed, try rank 2 if not guessed
            if (!isGuessed(2)) return 2;
            // If even rank 2 is guessed, switch direction
            isGoingUp = true; // Update global state if switching
            localIsGoingUp = true;
        }
        if (currentBestRank === 2) { // Must go up from rank 2
            isGoingUp = true; // Update global state
            localIsGoingUp = true;
        }
    }

    // If going up (either initially or switched)
    if (localIsGoingUp) {
        let bigger = currentBestRank + 1;
        while (bigger <= currentMaxRank) {
            if (!isGuessed(bigger)) return bigger;
            bigger++;
        }
    }
    return null; // No suitable hint found
}


// Обчислює номер гри для заданої дати (формат "YYYY-MM-DD")
function computeGameNumber(dateStr) {
    const baseDate = new Date(2025, 3, 15); // Month is 0-indexed (3 = April)
    const [year, month, day] = dateStr.split("-").map(Number);
    const currentDate = new Date(year, month - 1, day);
    const diffMs = currentDate - baseDate;
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    return diffDays + 1;
}

// Оновлює підпис гри
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
        // Optionally display an error to the user
    }
}

// --- State Management ---

// Зберігає поточний стан гри в localStorage
function saveGameState() {
    if (!currentGameDate) return; // Only save if we have a specific game date
    const state = {
        guesses,
        guessCount,
        bestRank,
        isGoingUp, // Save hint direction state
        lastWord,
        didWin, // Crucial for persistence
        didGiveUp, // Crucial for persistence
        giveUpWord // Needed if didGiveUp is true
    };
    try {
        localStorage.setItem(`gameState_${currentGameDate}`, JSON.stringify(state));
    } catch (e) {
        console.error("Failed to save game state to localStorage:", e);
        // Maybe inform the user storage is full or unavailable
    }
}

// Завантажує стан гри з localStorage для currentGameDate
function loadGameState() {
    if (!currentGameDate) return; // Should not happen if called correctly

    const savedState = localStorage.getItem(`gameState_${currentGameDate}`);
    if (!savedState) {
        // No saved state for this date, ensure UI is in active game mode
        resetUIForActiveGame();
        return; // Exit, use default initialized state
    }

    try {
        const state = JSON.parse(savedState);
        guesses = state.guesses || [];
        guessCount = state.guessCount || 0;
        bestRank = state.bestRank !== undefined ? state.bestRank : Infinity;
        isGoingUp = state.isGoingUp || false; // Load hint direction
        lastWord = state.lastWord || null;
        didWin = state.didWin || false; // Load win flag
        didGiveUp = state.didGiveUp || false; // Load give up flag
        giveUpWord = state.giveUpWord || null; // Load the give up word

        // Update basic UI elements
        const guessCountElem = document.getElementById("guessCount");
        const guessesContainer = document.getElementById("guessesContainer");
        const lastGuessWrapper = document.getElementById("lastGuessWrapper");
        const lastGuessDisplay = document.getElementById("lastGuessDisplay");

        if (guessCountElem) guessCountElem.textContent = guessCount;
        // Render guesses based on loaded state
        renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);

        // Hide "How to Play" if there are guesses
        const howToPlayBlock = document.getElementById("howToPlayBlock");
        if (howToPlayBlock) {
            howToPlayBlock.style.display = guesses.length > 0 ? "none" : "";
        }

        // --- Apply Final Game State UI ---
        if (didGiveUp && giveUpWord) {
            // Game was lost (gave up) - Show final state
            showLoseMessageUI(giveUpWord);
        } else if (didWin) {
            // Game was won - Show final state
            showWinMessageUI();
        } else {
            // Game is still active - Ensure final state elements are hidden, input enabled
            resetUIForActiveGame();
        }
    } catch (e) {
        console.error("Failed to parse or apply saved game state:", e);
        localStorage.removeItem(`gameState_${currentGameDate}`); // Clear corrupted state
        resetUIForActiveGame(); // Reset to active state
    }
}

// --- UI Update Functions for Final States ---

function showWinMessageUI() {
    const congratsBlock = document.getElementById("congratsBlock");
    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const hintButton = document.getElementById("hintButton");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const giveUpBtn = document.getElementById("giveUpBtn"); // Also disable give up

    if (congratsBlock) {
        const congratsTitle = document.getElementById("congratsTitle");
        if (congratsTitle) congratsTitle.textContent = "Вітаємо!"; // Ensure correct title

        const congratsMessageElem = document.getElementById("congratsMessage");
        const guessesUsedElem = document.getElementById("guessesUsed");
        const gameNumberElem = document.getElementById("gameNumber");
        if (congratsMessageElem && guessesUsedElem && gameNumberElem) {
            const gameNum = currentGameDate ? computeGameNumber(currentGameDate) : dayNumber;
            guessesUsedElem.textContent = guessCount;
            gameNumberElem.textContent = gameNum;
            // Standard win message format
            congratsMessageElem.textContent = `Ви знайшли секретне слово #${gameNum} за ${guessCount} спроб(и)!`;
        }
        congratsBlock.classList.remove("hidden");
    }
    if (guessInput) guessInput.disabled = true;
    if (submitGuessBtn) submitGuessBtn.disabled = true;
    if (hintButton) hintButton.disabled = true;
    if (giveUpBtn) giveUpBtn.disabled = true; // Disable give up button on win
    if (closestWordsBtn) closestWordsBtn.classList.remove("hidden"); // Show closest words button
}

function showLoseMessageUI(secretWord) {
    const congratsBlock = document.getElementById("congratsBlock");
    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const hintButton = document.getElementById("hintButton");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const giveUpBtn = document.getElementById("giveUpBtn"); // Also disable give up

    if (!congratsBlock) return;

    const congratsTitle = document.getElementById("congratsTitle");
    if (congratsTitle) {
        congratsTitle.textContent = "Нехай щастить наступного разу!"; // Set lose title
    }

    const congratsMessageElem = document.getElementById("congratsMessage");
    if (congratsMessageElem) {
        const gameNum = currentGameDate ? computeGameNumber(currentGameDate) : dayNumber;
        // Standard lose message format
        const message = `Ви здалися на слові #${gameNum} за ${guessCount} спроб(и).\nСлово було: "${secretWord}".`;
        // Use white-space: pre-wrap; in CSS for congratsMessageElem if using \n
        congratsMessageElem.textContent = message;
    }

    congratsBlock.classList.remove("hidden");

    if (guessInput) guessInput.disabled = true;
    if (submitGuessBtn) submitGuessBtn.disabled = true;
    if (hintButton) hintButton.disabled = true;
    if (giveUpBtn) giveUpBtn.disabled = true; // Disable give up button on lose
    if (closestWordsBtn) closestWordsBtn.classList.remove("hidden"); // Show closest words button
}

// Resets UI elements for an active (ongoing) game
function resetUIForActiveGame() {
    const congratsBlock = document.getElementById("congratsBlock");
    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const hintButton = document.getElementById("hintButton");
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const giveUpBtn = document.getElementById("giveUpBtn");

    if (congratsBlock) congratsBlock.classList.add("hidden");
    if (closestWordsBtn) closestWordsBtn.classList.add("hidden");

    if (guessInput) guessInput.disabled = false;
    if (submitGuessBtn) submitGuessBtn.disabled = false;
    if (hintButton) hintButton.disabled = false;
    if (giveUpBtn) giveUpBtn.disabled = false; // Ensure give up is enabled
}


// --- Game Ending Functions ---

function endGameAsWin() {
    if (didWin) return; // Prevent multiple calls
    didWin = true; // Set flag *before* saving and UI update
    didGiveUp = false; // Cannot be both win and give up
    showWinMessageUI(); // Update UI to win state
    saveGameState(); // Save the final state including didWin = true
}

function endGameAsGiveUp(secretWord) {
    if (didWin || didGiveUp) return; // Prevent if already won or given up
    didGiveUp = true; // Set flag *before* saving
    didWin = false;
    giveUpWord = secretWord; // Store the word
    showLoseMessageUI(secretWord); // Update UI to lose state
    saveGameState(); // Save the final state including didGiveUp = true and giveUpWord
}

// --- Initialization and Event Listeners ---

document.addEventListener("DOMContentLoaded", async () => {
    // --- Get Core DOM Elements ---
    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const guessesContainer = document.getElementById("guessesContainer");
    const guessCountElem = document.getElementById("guessCount");
    const lastGuessWrapper = document.getElementById("lastGuessWrapper");
    const lastGuessDisplay = document.getElementById("lastGuessDisplay");
    const howToPlayBlock = document.getElementById("howToPlayBlock");
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

    // --- Initial Setup ---
    randomGameBtn.textContent = "🔀 Випадкова"; // Set text content maybe based on locale later

    await fetchAllowedWords(); // Load allowed words early

    // --- Fetch Daily Game Info ---
    try {
        const response = await fetch("/api/daily-index");
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const dailyIndexData = await response.json();
        dayNumber = dailyIndexData.game_number;
        // Default to today's date if no specific game is loaded yet
        if (!currentGameDate) {
            currentGameDate = new Date().toISOString().split("T")[0];
            // Initialize state variables for a *new* game load
            // These might be overwritten by loadGameState if save data exists
            guesses = [];
            guessCount = 0;
            bestRank = Infinity;
            isGoingUp = false;
            lastWord = null;
            didWin = false;
            didGiveUp = false;
            giveUpWord = null;
        }
    } catch (err) {
        console.error("[Error] Failed to fetch daily index:", err);
        // Display an error message to the user? Fallback?
        if (document.getElementById("gameDateLabel")) {
            document.getElementById("gameDateLabel").textContent = "Помилка завантаження гри";
        }
        return; // Stop execution if essential daily info fails
    }

    // --- Fetch Ranked Words for the Current Game ---
    // Assuming fetchRankedWords gets the words for `currentGameDate` if needed,
    // or defaults to the daily game if `currentGameDate` corresponds to today.
    // Modify fetchRankedWords API/call if it needs the date explicitly.
    try {
        rankedWords = await fetchRankedWords(/* pass currentGameDate if needed */);
        if (!Array.isArray(rankedWords)) throw new Error("Ranked words data is not an array");
        MAX_RANK = rankedWords.length > 0 ? Math.max(...rankedWords.map(w => w.rank)) : 0; // More robust MAX_RANK
        console.log(`Loaded ${rankedWords.length} ranked words. Max rank: ${MAX_RANK}`);
    } catch (err) {
        console.error("[Error] fetchRankedWords failed:", err);
        // Display an error? Maybe disable guessing.
        if (guessInput) guessInput.disabled = true;
        if (submitGuessBtn) submitGuessBtn.disabled = true;
        return; // Stop if words can't be loaded
    }

    updateGameDateLabel(); // Update label with game number

    // --- Load Game State (Crucial: After date and words are set) ---
    loadGameState(); // This will apply saved state or reset UI for active game


    // --- Game Logic Handlers ---

    async function handleSubmit() {
        if (didWin || didGiveUp) return; // Prevent guesses after game end

        const word = guessInput.value.trim().toLowerCase();
        if (!word) return;

        if (guesses.some(g => g.word === word)) {
            alert(`Слово "${word}" вже було використано.`);
            return;
        }
        if (!allowedWords.has(word)) {
            alert(`Вибачте, слово "${word}" не знайдено у словнику.`);
            return;
        }

        // Hide "How to Play" block on first guess
        if (howToPlayBlock && howToPlayBlock.style.display !== "none") {
            howToPlayBlock.style.display = "none";
        }

        // Find the word's rank in the current game's list
        const match = rankedWords.find(item => item.word === word);
        let data;
        if (match) {
            data = { rank: match.rank };
        } else {
            // Word is allowed but not in *this specific day's* ranking
            data = { rank: Infinity, error: true, errorMessage: "Цього слова немає у рейтингу цього дня." };
        }

        guessCount++;
        if (guessCountElem) guessCountElem.textContent = guessCount;
        lastWord = word; // Update last word tried

        guesses.push({ word, rank: data.rank, error: data.error || false, errorMessage: data.errorMessage });

        if (!data.error) {
            if (data.rank < bestRank) {
                bestRank = data.rank;
                // Potentially reset hint direction if a much better word is found?
                // isGoingUp = false; // Optional: reset hint strategy
            }
            if (data.rank === 1) {
                renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay); // Render final guess
                endGameAsWin(); // Handle win condition
                return; // Stop further processing for this guess
            }
        }

        // Render guesses including the new one (win or not)
        renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);
        guessInput.value = ""; // Clear input
        guessInput.focus(); // Focus back on input

        saveGameState(); // Save state after a regular guess
    }

    async function loadArchive(game_date) {
        // 1. Save current game state (optional, but good practice)
        if (currentGameDate) {
            saveGameState(); // Save state before switching
            // Optionally cache in memory if needed:
            // gameStates[currentGameDate] = { /* current state */ };
        }

        console.log(`Loading archive for date: ${game_date}`);

        // 2. Fetch archive data
        try {
            const response = await fetch(`/archive/${game_date}`);
            if (!response.ok) {
                if (response.status === 404) {
                    alert(`Архів для дати ${game_date} не знайдено.`);
                } else {
                    alert(`Помилка завантаження архіву: ${response.statusText}`);
                }
                return; // Stop if fetch failed
            }
            const archiveData = await response.json();
            if (!archiveData || !Array.isArray(archiveData.ranking)) {
                throw new Error("Invalid archive data format");
            }

            // 3. Reset global state for the new game
            rankedWords = archiveData.ranking;
            MAX_RANK = rankedWords.length > 0 ? Math.max(...rankedWords.map(w => w.rank)) : 0;
            currentGameDate = game_date; // Update the current game date
            guesses = [];
            guessCount = 0;
            bestRank = Infinity;
            isGoingUp = false; // Reset hint direction for new game
            lastWord = null;
            didWin = false;
            didGiveUp = false;
            giveUpWord = null;

            console.log(`Loaded ${rankedWords.length} words for ${game_date}. Max rank: ${MAX_RANK}`);

            // 4. Update UI elements
            updateGameDateLabel();
            if (guessCountElem) guessCountElem.textContent = guessCount; // Reset count display
            // Clear previous guesses display before rendering potentially saved ones
            if (guessesContainer) guessesContainer.innerHTML = '';
            if (lastGuessWrapper) lastGuessWrapper.classList.add('hidden');


            // 5. Load saved state specifically for this archive date (if any)
            loadGameState(); // This will populate guesses, count, final state etc. if saved

            // 6. Ensure UI reflects the loaded state (loadGameState handles this)

        } catch (err) {
            console.error("Error loading archive for date", game_date, err);
            alert("Помилка завантаження архіву. Див. консоль для деталей.");
            // Maybe revert currentGameDate to previous value? Or to today?
        } finally {
            // Close modal regardless of success/failure
            if (previousGamesModal) previousGamesModal.classList.add("hidden");
            if (dropdownMenu) dropdownMenu.classList.add("hidden"); // Close menu
        }
    }

    // --- Event Listeners ---

    // Guess Input & Button
    guessInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            handleSubmit();
        }
    });
    submitGuessBtn.addEventListener("click", handleSubmit);

    // Hint Button
    hintButton.addEventListener("click", () => {
        if (didWin || didGiveUp) return; // No hints if game over

        if (howToPlayBlock && howToPlayBlock.style.display !== "none") {
            howToPlayBlock.style.display = "none"; // Hide help on first action
        }
        if (rankedWords.length === 0) {
            alert("Список слів ще не завантажено!");
            return;
        }

        const nextHintRank = getNextHintRank(bestRank, guesses, rankedWords, MAX_RANK);

        if (nextHintRank === null) {
            alert("Не вдалося знайти підходящу підказку (можливо, всі слова вже відгадані?).");
            return;
        }

        const hintWordObj = rankedWords.find(item => item.rank === nextHintRank);
        if (!hintWordObj) {
            // This case should ideally not happen if getNextHintRank is correct
            alert(`Помилка: Не знайдено слово з рангом ${nextHintRank}.`);
            console.error("Hint logic error: rank found but word missing?", nextHintRank, rankedWords);
            return;
        }

        console.log(`Hint: Providing word '${hintWordObj.word}' with rank ${hintWordObj.rank}`);

        // Treat hint as a guess
        guessCount++; // Increment guess count for hints
        if (guessCountElem) guessCountElem.textContent = guessCount;
        lastWord = hintWordObj.word; // Set hint word as last word
        guesses.push({ word: hintWordObj.word, rank: hintWordObj.rank, error: false, isHint: true }); // Add 'isHint' flag?

        if (hintWordObj.rank < bestRank) {
            bestRank = hintWordObj.rank;
            // Optional: reset hint direction 'isGoingUp' = false;
        }

        renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay);

        if (hintWordObj.rank === 1) {
            endGameAsWin(); // Handle win if hint was rank 1
        } else {
            saveGameState(); // Save state after hint
        }

        if (dropdownMenu) dropdownMenu.classList.add("hidden"); // Close menu
    });

    // Give Up Modal
    giveUpBtn.addEventListener("click", () => {
        if (didWin || didGiveUp) return; // Don't show if game already over
        if (giveUpModal) giveUpModal.classList.remove("hidden");
        if (dropdownMenu) dropdownMenu.classList.add("hidden"); // Close menu
    });
    closeGiveUpModal.addEventListener("click", () => {
        if (giveUpModal) giveUpModal.classList.add("hidden");
    });
    giveUpNoBtn.addEventListener("click", () => {
        if (giveUpModal) giveUpModal.classList.add("hidden");
    });
    giveUpYesBtn.addEventListener("click", () => {
        if (didWin || didGiveUp) { // Double check state before proceeding
            if (giveUpModal) giveUpModal.classList.add("hidden");
            return;
        }

        if (howToPlayBlock && howToPlayBlock.style.display !== "none") {
            howToPlayBlock.style.display = "none";
        }

        const secretWordObj = rankedWords.find(item => item.rank === 1);
        const secretWord = secretWordObj ? secretWordObj.word : (rankedWords.length > 0 ? rankedWords[0].word : "невідомо"); // Fallback word

        // Add the secret word as the final guess (optional, but shows it in the list)
        // This counts as a guess attempt
        guessCount++;
        if (guessCountElem) guessCountElem.textContent = guessCount;
        guesses.push({ word: secretWord, rank: 1, error: false, gaveUp: true }); // Mark this guess
        lastWord = secretWord; // Show the secret word as the last one
        bestRank = 1; // Set best rank to 1

        renderGuesses(guesses, lastWord, MAX_RANK, guessesContainer, lastGuessWrapper, lastGuessDisplay); // Update UI with final word

        // End the game as give up
        endGameAsGiveUp(secretWord); // This handles UI changes and saving state

        if (giveUpModal) giveUpModal.classList.add("hidden");
    });

    // Previous Games Modal
    previousGamesBtn.addEventListener("click", async () => {
        if (previousGamesModal) previousGamesModal.classList.remove("hidden");
        if (dropdownMenu) dropdownMenu.classList.add("hidden"); // Close menu

        if (previousGamesList) previousGamesList.innerHTML = "<p>Завантаження архіву...</p>"; // Loading indicator

        try {
            const response = await fetch("/archive");
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const dates = await response.json();
            if (!Array.isArray(dates)) throw new Error("Archive list format incorrect");

            if (previousGamesList) previousGamesList.innerHTML = ""; // Clear loading/previous list
            const today = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD format

            // Sort dates descending (newest first)
            dates.sort((a, b) => b.localeCompare(a));

            dates.forEach(dateStr => {
                if (dateStr > today) return; // Don't show future games

                const gameNumber = computeGameNumber(dateStr);
                const dateObj = new Date(dateStr + "T00:00:00"); // Ensure correct date parsing

                const weekday = dateObj.toLocaleDateString('uk-UA', { weekday: 'short' });
                const day = dateObj.getDate();
                const month = dateObj.toLocaleDateString('uk-UA', { month: 'short' });

                const btn = document.createElement("button");
                btn.className = "archive-button"; // Add class for styling
                btn.textContent = `#${gameNumber}⠀${weekday}, ${day} ${month.replace('.', '')}`; // Clean month abbreviation
                btn.dataset.date = dateStr; // Store date in data attribute

                btn.addEventListener("click", () => {
                    loadArchive(dateStr); // Load the selected archive game
                    // loadArchive handles closing the modal now
                });
                if (previousGamesList) previousGamesList.appendChild(btn);
            });

            if (dates.length === 0) {
                if (previousGamesList) previousGamesList.innerHTML = "<p>Архівних ігор не знайдено.</p>";
            }

        } catch (err) {
            console.error("[Error] Failed to fetch archive list:", err);
            if (previousGamesList) previousGamesList.innerHTML = "<p>Помилка завантаження архіву. Спробуйте пізніше.</p>";
        }
    });
    closePreviousGamesModal.addEventListener("click", () => {
        if (previousGamesModal) previousGamesModal.classList.add("hidden");
    });
    randomGameBtn.addEventListener("click", async () => {
        if (dropdownMenu) dropdownMenu.classList.add("hidden"); // Close menu
        try {
            const response = await fetch("/archive");
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const dates = await response.json();
            const today = new Date().toISOString().split("T")[0];
            const validDates = dates.filter(date => date <= today); // Only past/present games

            if (validDates.length === 0) {
                alert("Не знайдено доступних архівних ігор.");
                return;
            }
            const randomDate = validDates[Math.floor(Math.random() * validDates.length)];
            await loadArchive(randomDate); // Load the random game
            // loadArchive handles closing the modal
        } catch (err) {
            console.error("[Error] Failed to load random game:", err);
            alert("Помилка при завантаженні випадкової гри.");
        }
    });

    // Closest Words Modal
    function showClosestWords() {
        const closestWordsList = document.getElementById("closestWordsList");
        if (!closestWordsList) return;

        closestWordsList.innerHTML = ""; // Clear previous list
        // Show top N words, e.g., top 500 or all if less than 500
        const topN = rankedWords.slice(0, 500);

        if (topN.length === 0) {
            closestWordsList.innerHTML = "<p>Список слів порожній.</p>";
        } else {
            topN.forEach(item => {
                // Use the imported UI function to create the list item
                const guessItem = createGuessItem({ word: item.word, rank: item.rank, error: false }, MAX_RANK);
                closestWordsList.appendChild(guessItem);
            });
        }

        if (closestWordsModal) closestWordsModal.classList.remove("hidden");
    }
    closestWordsBtn.addEventListener("click", showClosestWords);
    closeModalBtn.addEventListener("click", () => {
        if (closestWordsModal) closestWordsModal.classList.add("hidden");
    });

    // Dropdown Menu
    menuButton.addEventListener("click", (event) => {
        event.stopPropagation(); // Prevent click from immediately closing menu
        if (dropdownMenu) dropdownMenu.classList.toggle("hidden");
    });
    // Close menu if clicking outside
    document.addEventListener("click", (event) => {
        if (menuButton && dropdownMenu && !menuButton.contains(event.target) && !dropdownMenu.contains(event.target)) {
            dropdownMenu.classList.add("hidden");
        }
    });

}); // End DOMContentLoaded