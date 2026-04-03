// static/js/ui.js

/**
 * Обчислює CSS-клас і відсоток ширини для прогрес-бара
 * на основі рангу слова з використанням логарифмічної шкали.
 *
 * @param {number} rank    Ранг слова (або Infinity для помилок)
 * @param {number} maxRank Максимальний ранг у поточній грі
 * @returns {{cssClass: string, width: number}} Клас для стилю та ширина прогрес-бара (%)
 */
function calculateProgressBarStyle(rank, maxRank) {
    const thresholds = {
        veryClose: 50,   // rank-very-close
        close: 150,      // rank-close
        medium: 500,     // rank-medium
        far: 1000        // rank-far, все більше — rank-very-far
    };

    let cssClass = 'rank-very-far';
    let widthPercent = 5; // мінімальна ширина

    // Некоректний ранг → спеціальний клас помилки
    if (rank === Infinity || rank > maxRank || rank <= 0 || isNaN(rank)) {
        cssClass = 'rank-error';
        widthPercent = 5;
    } else if (rank === 1) {
        cssClass = 'rank-exact'; // секретне слово
        widthPercent = 100;
    } else {
        if (rank <= thresholds.veryClose) cssClass = 'rank-very-close';
        else if (rank <= thresholds.close) cssClass = 'rank-close';
        else if (rank <= thresholds.medium) cssClass = 'rank-medium';
        else if (rank <= thresholds.far) cssClass = 'rank-far';

        // Логарифмічна шкала для плавної зміни ширини
        const minWidth = 10;
        const maxWidth = 98;
        const logMax = Math.log(maxRank > 1 ? maxRank : 2);
        const logRank = Math.log(rank);

        if (logMax > 0 && logRank >= 0) {
            const scale = Math.max(0, (logMax - logRank) / logMax);
            widthPercent = minWidth + scale * (maxWidth - minWidth);
        } else {
            widthPercent = minWidth;
        }
        widthPercent = Math.max(minWidth, Math.min(maxWidth, widthPercent));
    }

    return { cssClass: cssClass, width: widthPercent };
}

function formatSimilarity(value) {
    if (!Number.isFinite(value)) return null;
    return value.toFixed(2);
}

/**
 * Створює DOM-елемент для одного слова (спроби або підказки).
 *
 * @param {Object} guessObj  Об’єкт із даними про спробу {word, rank, similarity?, error, errorMessage?}
 * @param {number} maxRank   Максимальний ранг у грі
 * @returns {HTMLElement}    Елемент списку спроб
 */
export function createGuessItem(guessObj, maxRank) {
    const guessItem = document.createElement("div");
    guessItem.classList.add("guessItem");

    if (guessObj.isHint) {
        guessItem.classList.add("guessItemHint");
    }

    const styleInfo = calculateProgressBarStyle(guessObj.rank, maxRank);
    guessItem.classList.add(styleInfo.cssClass);

    // Виведення помилкової спроби
    if (guessObj.error) {
        const guessText = document.createElement("div");
        guessText.classList.add("guessText");
        guessText.style.color = '#ff8a80';

        const wordSpan = document.createElement("span");
        wordSpan.textContent = guessObj.word;
        wordSpan.style.fontStyle = 'italic';

        const errorSpan = document.createElement("span");
        errorSpan.textContent = guessObj.errorMessage || "не знайдено";
        errorSpan.style.fontSize = '0.85em';
        errorSpan.style.opacity = '0.8';

        guessText.appendChild(wordSpan);
        guessText.appendChild(errorSpan);
        guessItem.appendChild(guessText);

        return guessItem;
    }

    // Прогрес-бар для валідних спроб
    const fillBar = document.createElement("div");
    fillBar.classList.add("fillBar");
    fillBar.style.width = styleInfo.width + "%";

    // Текстовий блок (слово + ранг)
    const guessText = document.createElement("div");
    guessText.classList.add("guessText");

    const wordSpan = document.createElement("span");
    wordSpan.classList.add("word");
    wordSpan.textContent = guessObj.word;

    const metaWrap = document.createElement("div");
    metaWrap.classList.add("guessMeta");

    const rankSpan = document.createElement("span");
    rankSpan.classList.add("rank");
    rankSpan.textContent = guessObj.rank;
    metaWrap.appendChild(rankSpan);

    const formattedSimilarity = formatSimilarity(guessObj.similarity);
    if (formattedSimilarity !== null) {
        const similaritySpan = document.createElement("span");
        similaritySpan.classList.add("similarity");
        similaritySpan.textContent = formattedSimilarity;
        metaWrap.appendChild(similaritySpan);
    }

    guessText.appendChild(wordSpan);
    guessText.appendChild(metaWrap);

    guessItem.appendChild(fillBar);
    guessItem.appendChild(guessText);

    return guessItem;
}

/**
 * Рендерить усі спроби у контейнері та відображає останню правильну спробу окремо.
 *
 * @param {Array} guesses             Масив спроб і підказок
 * @param {string|null} lastWord      Останнє введене слово
 * @param {number} maxRank            Максимальний ранг у грі
 * @param {HTMLElement} container     Контейнер для списку спроб
 * @param {HTMLElement} lastGuessWrapper  Обгортка для останньої спроби
 * @param {HTMLElement} lastGuessDisplay  Контейнер для останньої правильної спроби
 * @param {string} sortMode           Режим сортування: similarity | guess-order
 */
export function renderGuesses(guesses, lastWord, maxRank, container, lastGuessWrapper, lastGuessDisplay, sortMode = "similarity") {
    container.innerHTML = "";

    const sortedGuesses = [...guesses].sort((a, b) => {
        if (sortMode === "guess-order") {
            const seqA = Number.isFinite(a.sequence) ? a.sequence : Number.MAX_SAFE_INTEGER;
            const seqB = Number.isFinite(b.sequence) ? b.sequence : Number.MAX_SAFE_INTEGER;
            return seqA - seqB;
        }

        // Помилки вгорі, далі — за зростанням рангу
        if (a.error && !b.error) return -1;
        if (!a.error && b.error) return 1;
        if (a.error && b.error) return 0;
        return a.rank - b.rank;
    });

    sortedGuesses.forEach(guessObj => {
        const guessItem = createGuessItem(guessObj, maxRank);
        if (!guessObj.error && guessObj.word === lastWord) {
            guessItem.classList.add("highlightGuess");
        }
        container.appendChild(guessItem);
    });

    // Окремий блок для останньої правильної спроби
    const lastCorrectGuessObj = guesses.find(g => g.word === lastWord && !g.error);
    if (lastCorrectGuessObj) {
        const cloned = createGuessItem(lastCorrectGuessObj, maxRank);
        cloned.classList.add("highlightGuess");
        lastGuessDisplay.innerHTML = "";
        lastGuessDisplay.appendChild(cloned);
        lastGuessWrapper.classList.remove("hidden");
    } else {
        lastGuessWrapper.classList.add("hidden");
    }
}
