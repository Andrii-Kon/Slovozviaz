// static/js/main.js
import { fetchRankedWords, submitGuess } from "./api.js";
import { renderGuesses, createGuessItem } from "./ui.js";

/**
 * Глобальний прапорець: поки не дійдемо до rank=2, рухаємося "до низу" (діленням на 2).
 * Як тільки дійшли до rank=2, ставимо isGoingUp = true, і рухаємося вгору (3,4,5...).
 */
let isGoingUp = false;

/**
 * Функція для визначення наступного рангу підказки.
 * Логіка:
 *   1) Якщо bestRank === Infinity => повертаємо 500 (якщо є).
 *   2) Якщо bestRank === 1 => повертаємо null (слово відгадане).
 *   3) Якщо isGoingUp === false => намагаємося поділити bestRank на 2,
 *      доки не дістанемося до 2. Якщо вже нижчі ранги вгадані, йдемо ще нижче.
 *      Коли дійшли до 2 — виставляємо isGoingUp=true і далі даємо rank=3,4,5...
 *   4) Якщо isGoingUp === true => рухаємося вгору: bestRank+1, +2 і т. д.
 */
function getNextHintRank(bestRank, guesses, rankedWords, maxRank) {
    // Якщо спроб ще не було – повертаємо 500, якщо таке є
    if (bestRank === Infinity) {
        const wordObj = rankedWords.find(x => x.rank === 500);
        if (wordObj) return 500;
        return null;
    }

    // Якщо вже відгадали секретне слово
    if (bestRank === 1) {
        return null;
    }

    // Перевіряємо, чи ранг уже вгаданий
    const isGuessed = (r) => guesses.some(g => g.rank === r);

    // Якщо ми ще не дійшли до 2 (isGoingUp === false), продовжуємо "ділення"
    if (!isGoingUp) {
        // Якщо bestRank > 2, пробуємо взяти floor(bestRank/2)
        if (bestRank > 2) {
            let candidate = Math.floor(bestRank / 2);
            while (candidate >= 2) {
                if (!isGuessed(candidate)) {
                    return candidate;
                }
                candidate = Math.floor(candidate / 2);
            }
            // Якщо не знайшли нічого нижчого за 2,
            // можна "проскочити" відразу до rank=2 (якщо воно ще не вгадане)
            if (!isGuessed(2)) {
                return 2;
            }
            // Якщо 2 вже вгадане, тоді починаємо рухатися вгору
            isGoingUp = true;
        }

        // Якщо bestRank = 2 або ми з'ясували, що 2 теж вгадане,
        // переходимо в "зворотний" режим (isGoingUp = true).
        if (bestRank === 2) {
            isGoingUp = true;
        }
    }

    // Якщо дійшли сюди, значить isGoingUp = true
    // => рухаємося вгору (bestRank+1, +2, +3 ...)
    let bigger = bestRank + 1;
    while (bigger <= maxRank) {
        if (!isGuessed(bigger)) {
            return bigger;
        }
        bigger++;
    }

    return null; // Нічого не знайшли
}

document.addEventListener("DOMContentLoaded", async () => {
    console.log("[Debug] DOMContentLoaded triggered");

    // Отримуємо посилання на елементи
    const guessInput = document.getElementById("guessInput");
    const submitGuessBtn = document.getElementById("submitGuess");
    const guessesContainer = document.getElementById("guessesContainer");
    const guessCountElem = document.getElementById("guessCount");
    const lastGuessWrapper = document.getElementById("lastGuessWrapper");
    const lastGuessDisplay = document.getElementById("lastGuessDisplay");
    const howToPlayBlock = document.getElementById("howToPlayBlock");

    // Модальне вікно та кнопки
    const closestWordsBtn = document.getElementById("closestWordsBtn");
    const closestWordsModal = document.getElementById("closestWordsModal");
    const closestWordsList = document.getElementById("closestWordsList");
    const closeModalBtn = document.getElementById("closeModalBtn");

    console.log("[Debug] guessInput =", guessInput);
    console.log("[Debug] submitGuessBtn =", submitGuessBtn);
    console.log("[Debug] guessesContainer =", guessesContainer);
    console.log("[Debug] guessCountElem =", guessCountElem);
    console.log("[Debug] lastGuessWrapper =", lastGuessWrapper);
    console.log("[Debug] lastGuessDisplay =", lastGuessDisplay);
    console.log("[Debug] howToPlayBlock =", howToPlayBlock);
    console.log("[Debug] closestWordsBtn =", closestWordsBtn);
    console.log("[Debug] closestWordsModal =", closestWordsModal);
    console.log("[Debug] closestWordsList =", closestWordsList);
    console.log("[Debug] closeModalBtn =", closeModalBtn);

    let guessCount = 0;
    let bestRank = Infinity;
    let rankedWords = [];
    let guesses = [];
    let lastWord = null;
    let MAX_RANK = 0;

    // Завантажуємо список слів
    try {
        rankedWords = await fetchRankedWords();
        console.log("[Debug] fetched rankedWords, length =", rankedWords.length);
    } catch (err) {
        console.error("[Error] fetchRankedWords failed:", err);
    }

    // Визначаємо MAX_RANK (якщо список відсортований – беремо .length)
    MAX_RANK = rankedWords.length;
    console.log("[Debug] MAX_RANK =", MAX_RANK);

    // Функція відправки слова
    async function handleSubmit() {
        console.log("[Debug] handleSubmit called");

        const word = guessInput.value.trim().toLowerCase();
        console.log("[Debug] user typed word =", word);
        if (!word) return;

        // Ховаємо блок "Як грати?" після першої спроби
        if (howToPlayBlock && howToPlayBlock.style.display !== "none") {
            howToPlayBlock.style.display = "none";
            console.log("[Debug] hide howToPlayBlock");
        }

        let data;
        try {
            data = await submitGuess(word);
            console.log("[Debug] response from /guess =", data);
        } catch (err) {
            console.error("[Error] submitGuess failed:", err);
            return;
        }

        guessCount++;
        guessCountElem.textContent = guessCount;
        lastWord = word;

        if (data.error) {
            guesses.push({ word, rank: Infinity, error: true });
            console.log("[Debug] word not found in the list");
        } else {
            guesses.push({ word, rank: data.rank, error: false });
            console.log("[Debug] push guess:", { word, rank: data.rank });

            if (data.rank < bestRank) {
                bestRank = data.rank;
                console.log("[Debug] new bestRank =", bestRank);
            }

            // Якщо відгадали слово (rank === 1)
            if (data.rank === 1) {
                console.log("[Debug] user guessed the secret word!");
                const congratsBlock = document.getElementById("congratsBlock");
                if (congratsBlock) {
                    congratsBlock.classList.remove("hidden");
                    console.log("[Debug] show congratsBlock");
                }
                // Оновлюємо кількість спроб у блоці привітання
                const guessesUsedElem = document.getElementById("guessesUsed");
                if (guessesUsedElem) {
                    guessesUsedElem.textContent = guessCount;
                }
                guessInput.disabled = true;
                submitGuessBtn.disabled = true;

                // Вимикаємо кнопку Hint після виграшу
                const hintButton = document.getElementById("hintButton");
                if (hintButton) {
                    hintButton.disabled = true;
                }

                // Показуємо кнопку "Closest words"
                closestWordsBtn.classList.remove("hidden");
                console.log("[Debug] show closestWordsBtn");
            }
        }

        // Рендеримо всі здогадки
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

    // Слухачі подій (Enter + клік)
    guessInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            handleSubmit();
        }
    });
    submitGuessBtn.addEventListener("click", handleSubmit);

    // Додаємо кнопку Hint динамічно
    const hintButton = document.createElement("button");
    hintButton.textContent = "Hint";
    hintButton.id = "hintButton";
    document.querySelector(".input-section").appendChild(hintButton);

    hintButton.addEventListener("click", () => {
        console.log("[Debug] hintButton clicked");

        // Ховаємо блок "Як грати?" при натисканні Hint
        if (howToPlayBlock && howToPlayBlock.style.display !== "none") {
            howToPlayBlock.style.display = "none";
            console.log("[Debug] hide howToPlayBlock on hint");
        }

        if (rankedWords.length === 0) {
            alert("Список слів порожній або не завантажений!");
            return;
        }
        // Використовуємо функцію для визначення наступного рангу
        const nextHintRank = getNextHintRank(bestRank, guesses, rankedWords, MAX_RANK);
        console.log("[Debug] nextHintRank =", nextHintRank);

        if (!nextHintRank) {
            alert("Немає підходящої підказки.");
            return;
        }

        const hintWordObj = rankedWords.find(item => item.rank === nextHintRank);
        console.log("[Debug] hintWordObj =", hintWordObj);

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
        console.log("[Debug] added hint guess:", hintWordObj.word);

        // Оновлюємо bestRank, якщо отриманий ранг є кращим (меншим)
        if (hintWordObj.rank < bestRank) {
            bestRank = hintWordObj.rank;
            console.log("[Debug] updated bestRank =", bestRank);
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

    // Модальне вікно "Closest words"
    function showClosestWords() {
        console.log("[Debug] showClosestWords called");

        // Очищаємо контейнер
        closestWordsList.innerHTML = "";

        // Беремо перші 500 (або менше)
        const top500 = rankedWords.slice(0, 500);
        console.log("[Debug] top500.length =", top500.length);

        // Рендеримо кожне слово
        top500.forEach(item => {
            const guessItem = createGuessItem(
                { word: item.word, rank: item.rank, error: false },
                MAX_RANK
            );
            closestWordsList.appendChild(guessItem);
        });

        console.log("[Debug] removing 'hidden' from #closestWordsModal");
        closestWordsModal.classList.remove("hidden");
    }

    // При натисканні на кнопку "Closest words" — відкриваємо модалку
    closestWordsBtn.addEventListener("click", showClosestWords);

    // При натисканні на "Х" — закриваємо модалку
    closeModalBtn.addEventListener("click", () => {
        console.log("[Debug] adding 'hidden' to #closestWordsModal");
        closestWordsModal.classList.add("hidden");
    });
});
