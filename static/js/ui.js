// static/js/ui.js
// REMOVED: import { getFillPercent, getBarColor } from "./utils.js";
// We will replace this logic with the new function below.

// --- New Style Calculation Function (incorporating color and width logic) ---

/**
 * Розраховує CSS-клас для кольору та відсоток ширини для прогрес-бару
 * на основі рангу слова, використовуючи логарифмічну шкалу для плавності.
 * @param {number} rank - Ранг слова (або Infinity для помилки).
 * @param {number} maxRank - Максимальний ранг у грі.
 * @returns {{cssClass: string, width: number}} Об'єкт з CSS-класом та шириною у відсотках.
 */
function calculateProgressBarStyle(rank, maxRank) {
    // Пороги для визначення кольору (можна налаштувати)
    // Оновлено кольори з CSS
    const thresholds = {
        veryClose: 50,  // rank-very-close (Насичений зелений)
        close: 150,     // rank-close (Світліший зелений)
        medium: 500,    // rank-medium (Жовтий/Помаранчевий)
        far: 1000,      // rank-far (Помаранчевий)
                        // rank-very-far (Червоний) за замовчуванням
    };

    let cssClass = 'rank-very-far'; // Клас за замовчуванням
    let widthPercent = 5; // Мінімальна ширина (наприклад, для помилок)

    // Перевірка некоректних рангів
    if (rank === Infinity || rank > maxRank || rank <= 0 || isNaN(rank)) {
        cssClass = 'rank-error'; // Спеціальний стиль для помилок (можна додати в CSS)
        widthPercent = 5;
    } else if (rank === 1) {
        // Точне співпадіння
        cssClass = 'rank-exact'; // Спеціальний стиль для рангу 1 (можна додати в CSS)
        widthPercent = 100;
    } else {
        // Визначення класу кольору на основі порогів
        if (rank <= thresholds.veryClose) cssClass = 'rank-very-close';
        else if (rank <= thresholds.close) cssClass = 'rank-close';
        else if (rank <= thresholds.medium) cssClass = 'rank-medium';
        else if (rank <= thresholds.far) cssClass = 'rank-far';
        // Інакше залишається 'rank-very-far'

        // --- Плавний розрахунок ширини (Логарифмічна шкала) ---
        const minWidth = 10; // Мінімальна ширина для найгіршого рангу
        const maxWidth = 98; // Максимальна ширина для рангу 2

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


// --- UI Element Creation and Rendering ---

/**
 * Створює DOM-елемент для одного слова (здогадки).
 */
export function createGuessItem(guessObj, maxRank) {
    const guessItem = document.createElement("div");
    guessItem.classList.add("guessItem"); // Базовий клас залишається

    // --- ОНОВЛЕНО: Використовуємо нову функцію для стилів ---
    const styleInfo = calculateProgressBarStyle(guessObj.rank, maxRank);

    // Додаємо клас для кольору до батьківського елемента guessItem
    guessItem.classList.add(styleInfo.cssClass);

    if (guessObj.error) {
        // Замість простого тексту, створюємо структуру для помилки теж,
        // щоб зберегти вигляд, але без прогрес-бару
        const guessText = document.createElement("div");
        guessText.classList.add("guessText"); // Використовуємо той же клас для відступів
        guessText.style.color = '#ff8a80'; // Червонуватий колір для помилки

        const wordSpan = document.createElement("span");
        wordSpan.textContent = guessObj.word;
        wordSpan.style.fontStyle = 'italic';

        const errorSpan = document.createElement("span");
        errorSpan.textContent = guessObj.errorMessage || "не знайдено"; // Показуємо повідомлення про помилку або стандартне
        errorSpan.style.fontSize = '0.85em';
        errorSpan.style.opacity = '0.8';

        guessText.appendChild(wordSpan);
        guessText.appendChild(errorSpan);
        guessItem.appendChild(guessText);
        // Не додаємо fillBar для помилок

        return guessItem; // Повертаємо елемент помилки
    }

    // --- Створення fillBar (для коректних спроб) ---
    const fillBar = document.createElement("div");
    fillBar.classList.add("fillBar");
    // Ширина встановлюється за допомогою нової функції
    fillBar.style.width = styleInfo.width + "%";
    // Колір тепер визначається через CSS-клас на guessItem
    // fillBar.style.backgroundColor = getBarColor(guessObj.rank); // ЦЕ БІЛЬШЕ НЕ ПОТРІБНО

    // --- Створення guessText (як і раніше) ---
    const guessText = document.createElement("div");
    guessText.classList.add("guessText");

    const wordSpan = document.createElement("span");
    wordSpan.classList.add("word"); // Додаємо клас, якщо він є в CSS
    wordSpan.textContent = guessObj.word;

    const rankSpan = document.createElement("span");
    rankSpan.classList.add("rank"); // Додаємо клас, якщо він є в CSS
    rankSpan.textContent = guessObj.rank;

    guessText.appendChild(wordSpan);
    guessText.appendChild(rankSpan);

    // Додаємо елементи в правильному порядку для z-index
    guessItem.appendChild(fillBar);
    guessItem.appendChild(guessText);

    return guessItem;
}

/**
 * Відтворює всі спроби у списку та показує останню спробу зверху.
 * (Логіка сортування та рендеру залишається без змін)
 */
export function renderGuesses(guesses, lastWord, maxRank, container, lastGuessWrapper, lastGuessDisplay) {
    container.innerHTML = ""; // Очищуємо контейнер

    // Сортуємо: помилки першими (якщо є), потім за рангом
    guesses.sort((a, b) => {
        if (a.error && !b.error) return -1;
        if (!a.error && b.error) return 1;
        if (a.error && b.error) return 0; // Можна додати сортування за порядком введення для помилок
        return a.rank - b.rank; // Сортування за рангом
    });

    // Відтворюємо кожен елемент
    guesses.forEach(guessObj => {
        const guessItem = createGuessItem(guessObj, maxRank); // Використовуємо оновлену функцію
        // Підсвічуємо останню введену *коректну* спробу
        if (!guessObj.error && guessObj.word === lastWord) {
            guessItem.classList.add("highlightGuess");
        }
        container.appendChild(guessItem);
    });

    // Відображаємо останню *коректну* спробу окремо
    const lastCorrectGuessObj = guesses.find(g => g.word === lastWord && !g.error);
    if (lastCorrectGuessObj) {
        const cloned = createGuessItem(lastCorrectGuessObj, maxRank); // Використовуємо оновлену функцію
        cloned.classList.add("highlightGuess"); // Підсвічуємо
        lastGuessDisplay.innerHTML = ""; // Очищуємо попередній
        lastGuessDisplay.appendChild(cloned); // Додаємо новий
        lastGuessWrapper.classList.remove("hidden"); // Показуємо блок
    } else {
        lastGuessWrapper.classList.add("hidden"); // Ховаємо, якщо остання спроба була помилкою
    }
}