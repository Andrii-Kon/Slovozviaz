function normalizeWord(value) {
    return (value || "").trim().toLowerCase();
}

function formatSimilarityPercent(value) {
    const num = Number(value);
    if (Number.isNaN(num)) return "—";
    return (num * 100).toFixed(2);
}

async function postJson(url, body) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(body),
    });

    let data = null;
    try {
        data = await response.json();
    } catch (_error) {
        data = null;
    }

    return { ok: response.ok, status: response.status, data };
}

function renderTopWords(tbody, ranking) {
    tbody.innerHTML = "";
    const topWords = Array.isArray(ranking) ? ranking : [];

    for (const item of topWords) {
        const tr = document.createElement("tr");

        const rankTd = document.createElement("td");
        rankTd.textContent = String(item.rank ?? "");

        const wordTd = document.createElement("td");
        wordTd.textContent = String(item.word ?? "");

        const similarityTd = document.createElement("td");
        similarityTd.textContent = formatSimilarityPercent(item.similarity);

        tr.appendChild(rankTd);
        tr.appendChild(wordTd);
        tr.appendChild(similarityTd);
        tbody.appendChild(tr);
    }
}

function formatDateRelation(value) {
    if (value === "past") return "минула дата";
    if (value === "future") return "майбутня дата";
    return "сьогодні";
}

function formatPreviewAction(value) {
    return value === "replace" ? "заміна існуючої гри" : "створення нової гри";
}

function buildPreviewSummary(payload) {
    const actionText = payload.action === "replace"
        ? `На ${payload.game_date} вже є гра`
        : `На ${payload.game_date} ще немає гри`;

    const relationText = formatDateRelation(payload.date_relation);
    const normalizationText = payload.word_was_normalized && payload.requested_word !== payload.secret_word
        ? ` Введене слово нормалізовано до "${payload.secret_word}".`
        : "";

    if (payload.action === "replace" && payload.existing_game?.secret_word) {
        return `${actionText} "${payload.existing_game.secret_word}", її буде замінено. Обрана дата: ${relationText}.${normalizationText}`;
    }

    return `${actionText}, буде створено новий запис. Обрана дата: ${relationText}.${normalizationText}`;
}

function buildSaveSuccessText(payload) {
    if (payload.save_action === "replaced") {
        if (payload.previous_secret_word) {
            return `Гру на ${payload.game_date} оновлено: "${payload.previous_secret_word}" -> "${payload.secret_word}".`;
        }
        return `Гру на ${payload.game_date} успішно замінено.`;
    }

    return `Гру на ${payload.game_date} успішно створено.`;
}

function renderMetaGrid(container, payload) {
    container.innerHTML = "";

    const items = [
        ["Дата", payload.game_date],
        ["Дія", formatPreviewAction(payload.action)],
        ["Статус дати", formatDateRelation(payload.date_relation)],
        ["Секретне слово", payload.secret_word],
        ["Всього слів у рейтингу", String(payload.total_ranking_words ?? "—")],
    ];

    if (payload.word_was_normalized && payload.requested_word && payload.requested_word !== payload.secret_word) {
        items.splice(4, 0, ["Введене слово", payload.requested_word]);
    }

    if (payload.existing_game?.secret_word) {
        items.push(["Поточне слово в БД", payload.existing_game.secret_word]);
    }

    for (const [label, value] of items) {
        const item = document.createElement("div");
        item.className = "dev-archive-meta-item";

        const labelEl = document.createElement("span");
        labelEl.className = "dev-archive-meta-label";
        labelEl.textContent = label;

        const valueEl = document.createElement("span");
        valueEl.className = "dev-archive-meta-value";
        valueEl.textContent = value;

        item.appendChild(labelEl);
        item.appendChild(valueEl);
        container.appendChild(item);
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const body = document.body;
    const isAuthenticated = body?.dataset?.devAuthenticated === "true";
    const loginUrl = body?.dataset?.devLoginUrl || "";
    const logoutUrl = body?.dataset?.devLogoutUrl || "";
    const previewUrl = body?.dataset?.devPreviewUrl || "";
    const saveUrl = body?.dataset?.devSaveUrl || "";

    const loginSection = document.getElementById("devLoginSection");
    const loginForm = document.getElementById("devLoginForm");
    const passwordInput = document.getElementById("devPasswordInput");
    const loginSubmitBtn = document.getElementById("devLoginSubmitBtn");
    const loginError = document.getElementById("devLoginError");

    const logoutBtn = document.getElementById("devLogoutBtn");
    const managerSection = document.getElementById("devManagerSection");
    const archiveForm = document.getElementById("devArchiveForm");
    const dateInput = document.getElementById("devGameDateInput");
    const wordInput = document.getElementById("devSecretWordInput");
    const previewBtn = document.getElementById("devPreviewBtn");
    const saveBtn = document.getElementById("devSaveBtn");
    const errorEl = document.getElementById("devArchiveError");
    const successEl = document.getElementById("devArchiveSuccess");
    const previewSection = document.getElementById("devArchivePreview");
    const previewSummary = document.getElementById("devPreviewSummary");
    const previewMeta = document.getElementById("devPreviewMeta");
    const previewTableBody = document.getElementById("devPreviewTopWordsBody");
    const openGameLink = document.getElementById("devOpenGameLink");

    let latestPreview = null;

    if (
        !loginSection || !loginForm || !passwordInput || !loginSubmitBtn || !loginError ||
        !logoutBtn || !managerSection || !archiveForm || !dateInput || !wordInput ||
        !previewBtn || !saveBtn || !errorEl || !successEl || !previewSection ||
        !previewSummary || !previewMeta || !previewTableBody || !openGameLink ||
        !loginUrl || !logoutUrl || !previewUrl || !saveUrl
    ) {
        return;
    }

    const showLoginError = (message) => {
        loginError.textContent = message;
        loginError.classList.remove("hidden");
    };

    const hideLoginError = () => {
        loginError.textContent = "";
        loginError.classList.add("hidden");
    };

    const showError = (message) => {
        errorEl.textContent = message;
        errorEl.classList.remove("hidden");
    };

    const hideError = () => {
        errorEl.textContent = "";
        errorEl.classList.add("hidden");
    };

    const showSuccess = (message) => {
        successEl.textContent = message;
        successEl.classList.remove("hidden");
    };

    const hideSuccess = () => {
        successEl.textContent = "";
        successEl.classList.add("hidden");
    };

    const applyPreview = (payload) => {
        latestPreview = payload;
        previewSummary.textContent = buildPreviewSummary(payload);
        renderMetaGrid(previewMeta, payload);
        renderTopWords(previewTableBody, payload.ranking_preview);
        previewSection.classList.remove("hidden");

        if (payload.public_game_url) {
            openGameLink.href = payload.public_game_url;
            openGameLink.classList.remove("hidden");
        } else {
            openGameLink.href = "#";
            openGameLink.classList.add("hidden");
        }

        saveBtn.disabled = false;
    };

    const invalidatePreview = () => {
        latestPreview = null;
        saveBtn.disabled = true;
        hideSuccess();
    };

    const handleExpiredSession = () => {
        invalidatePreview();
        showError("Dev-сесію втрачено. Оновіть сторінку і увійдіть знову.");
    };

    if (!isAuthenticated) {
        managerSection.classList.add("hidden");
        logoutBtn.classList.add("hidden");
        loginSection.classList.remove("hidden");
    }

    loginForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        hideLoginError();

        const password = passwordInput.value;
        if (!password) {
            showLoginError("Введіть dev пароль.");
            passwordInput.focus();
            return;
        }

        loginSubmitBtn.disabled = true;
        loginSubmitBtn.textContent = "Входимо...";

        try {
            const response = await postJson(loginUrl, { password });
            if (!response.ok) {
                showLoginError(response.data?.error || "Не вдалося увійти.");
                return;
            }

            window.location.reload();
        } catch (error) {
            console.error("[dev-archive] login failed:", error);
            showLoginError("Не вдалося увійти. Спробуйте ще раз.");
        } finally {
            loginSubmitBtn.disabled = false;
            loginSubmitBtn.textContent = "Увійти";
        }
    });

    logoutBtn.addEventListener("click", async () => {
        logoutBtn.disabled = true;
        logoutBtn.textContent = "Виходимо...";

        try {
            await postJson(logoutUrl, {});
        } catch (error) {
            console.error("[dev-archive] logout failed:", error);
        } finally {
            window.location.reload();
        }
    });

    const markPreviewOutdated = () => {
        hideError();
        hideSuccess();
        saveBtn.disabled = true;
    };

    dateInput.addEventListener("input", markPreviewOutdated);
    wordInput.addEventListener("input", markPreviewOutdated);

    archiveForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        hideError();
        hideSuccess();

        const word = normalizeWord(wordInput.value);
        const gameDate = dateInput.value;

        if (!gameDate) {
            showError("Вкажіть дату гри.");
            dateInput.focus();
            return;
        }

        if (!word) {
            showError("Введіть секретне слово.");
            wordInput.focus();
            return;
        }

        previewBtn.disabled = true;
        previewBtn.textContent = "Готуємо preview...";
        saveBtn.disabled = true;

        try {
            const response = await postJson(previewUrl, {
                game_date: gameDate,
                word,
            });

            if (response.status === 401) {
                handleExpiredSession();
                return;
            }

            if (!response.ok || !response.data) {
                showError(response.data?.error || "Не вдалося побудувати preview.");
                return;
            }

            applyPreview(response.data);
            previewSection.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) {
            console.error("[dev-archive] preview failed:", error);
            showError("Не вдалося побудувати preview. Спробуйте ще раз.");
        } finally {
            previewBtn.disabled = false;
            previewBtn.textContent = "Показати preview";
        }
    });

    saveBtn.addEventListener("click", async () => {
        hideError();
        hideSuccess();

        const gameDate = dateInput.value;
        const word = normalizeWord(wordInput.value);

        if (!latestPreview) {
            showError("Спочатку згенеруйте preview.");
            return;
        }

        saveBtn.disabled = true;
        saveBtn.textContent = "Зберігаємо...";

        try {
            const response = await postJson(saveUrl, {
                game_date: gameDate,
                word,
            });

            if (response.status === 401) {
                handleExpiredSession();
                return;
            }

            if (!response.ok || !response.data) {
                showError(response.data?.error || "Не вдалося зберегти гру.");
                saveBtn.disabled = false;
                return;
            }

            applyPreview(response.data);
            showSuccess(buildSaveSuccessText(response.data));
        } catch (error) {
            console.error("[dev-archive] save failed:", error);
            showError("Не вдалося зберегти гру. Спробуйте ще раз.");
            saveBtn.disabled = false;
        } finally {
            saveBtn.textContent = "Зберегти в архів";
        }
    });
});
