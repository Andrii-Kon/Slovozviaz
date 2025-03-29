import { getFillPercent, getBarColor } from "./utils.js";

/**
 * Створює DOM-елемент для одного слова (здогадки).
 */
export function createGuessItem(guessObj, maxRank) {
    const guessItem = document.createElement("div");
    guessItem.classList.add("guessItem");

    // Якщо слово не знайдено
    if (guessObj.error) {
        guessItem.textContent = `${guessObj.word} — не знайдено у списку`;
        return guessItem;
    }

    // Смуга (рівень близькості)
    const fillBar = document.createElement("div");
    fillBar.classList.add("fillBar");
    const fillPercent = getFillPercent(guessObj.rank, maxRank);
    fillBar.style.width = fillPercent + "%";
    fillBar.style.backgroundColor = getBarColor(guessObj.rank);

    // Текст зліва (слово) і справа (ранг)
    const guessText = document.createElement("div");
    guessText.classList.add("guessText");

    const wordSpan = document.createElement("span");
    wordSpan.textContent = guessObj.word;

    const rankSpan = document.createElement("span");
    rankSpan.textContent = guessObj.rank;

    guessText.appendChild(wordSpan);
    guessText.appendChild(rankSpan);

    guessItem.appendChild(fillBar);
    guessItem.appendChild(guessText);

    return guessItem;
}

/**
 * Відтворює всі спроби у списку та показує останню спробу зверху.
 */
export function renderGuesses(guesses, lastWord, maxRank, container, lastGuessWrapper, lastGuessDisplay) {
    container.innerHTML = "";

    // Сортуємо за зростанням рангу
    guesses.sort((a, b) => a.rank - b.rank);

    guesses.forEach(guessObj => {
        const guessItem = createGuessItem(guessObj, maxRank);
        // Якщо це останнє введене слово і НЕ було помилки
        if (guessObj.word === lastWord && !guessObj.error) {
            guessItem.classList.add("highlightGuess");
        }
        container.appendChild(guessItem);
    });

    // Знаходимо останню коректну спробу
    const lastGuessObj = guesses.find(g => g.word === lastWord && !g.error);
    if (lastGuessObj) {
        const cloned = createGuessItem(lastGuessObj, maxRank);
        cloned.classList.add("highlightGuess");
        lastGuessDisplay.innerHTML = "";
        lastGuessDisplay.appendChild(cloned);
        lastGuessWrapper.classList.remove("hidden");
    } else {
        lastGuessWrapper.classList.add("hidden");
    }
}
