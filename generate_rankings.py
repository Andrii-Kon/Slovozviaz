import argparse
import bz2
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional

import numpy as np

BASE_DATE = date(2025, 6, 2)
PRECOMPUTED_DIR = "precomputed"
DEFAULT_MODEL_PATH = os.getenv(
    "LOCAL_EMBEDDINGS_PATH",
    os.path.join("models", "ubercorpus.cased.lemmatized.glove.300d"),
)


@dataclass
class EmbeddingResources:
    model_path: str
    vectors: Dict[str, np.ndarray]
    words_available: List[str]
    matrix: np.ndarray
    matrix_norms: np.ndarray
    missing_words: List[str]


def ensure_precomputed_dir() -> None:
    os.makedirs(PRECOMPUTED_DIR, exist_ok=True)


def _normalize_path(path: str) -> str:
    return os.path.normpath(path.replace("\\", os.sep))


def resolve_model_path(model_path: Optional[str] = None) -> str:
    raw_path = model_path or DEFAULT_MODEL_PATH
    normalized = _normalize_path(raw_path)

    if os.path.isfile(normalized):
        return normalized

    bz2_path = f"{normalized}.bz2"
    if os.path.isfile(bz2_path):
        return bz2_path

    raise FileNotFoundError(
        f"Не знайдено файл моделі: '{normalized}' або '{bz2_path}'."
    )


def _open_model_file(model_path: str):
    if model_path.endswith(".bz2"):
        return bz2.open(model_path, mode="rt", encoding="utf-8", errors="ignore")
    return open(model_path, mode="r", encoding="utf-8", errors="ignore")


def _load_required_vectors(model_path: str, required_words: Iterable[str]) -> Dict[str, np.ndarray]:
    required_set = {w for w in required_words if w}
    found: Dict[str, np.ndarray] = {}
    vector_dim: Optional[int] = None

    print(
        f"[MODEL] Завантаження векторів із '{model_path}' "
        f"(потрібно слів: {len(required_set)})"
    )

    with _open_model_file(model_path) as f:
        for line in f:
            parts = line.rstrip().split()
            if len(parts) < 2:
                continue

            word = parts[0]
            if word not in required_set or word in found:
                continue

            try:
                vec = np.asarray(parts[1:], dtype=np.float32)
            except ValueError:
                continue

            if vec.size == 0:
                continue
            if vector_dim is None:
                vector_dim = vec.size
            elif vec.size != vector_dim:
                continue

            found[word] = vec
            if len(found) == len(required_set):
                break

    return found


def load_embedding_resources(
    words: List[str],
    daily_words: Optional[List[str]] = None,
    model_path: Optional[str] = None,
) -> EmbeddingResources:
    resolved_model_path = resolve_model_path(model_path)
    required_words = set(words)
    if daily_words:
        required_words.update(daily_words)

    vectors = _load_required_vectors(resolved_model_path, required_words)
    missing_words = sorted(required_words - set(vectors))

    words_available = [w for w in words if w in vectors]
    if not words_available:
        raise RuntimeError("Жодного слова зі словника не знайдено в моделі.")

    matrix = np.vstack([vectors[w] for w in words_available]).astype(np.float32)
    matrix_norms = np.linalg.norm(matrix, axis=1)
    matrix_norms = np.where(matrix_norms == 0.0, 1e-12, matrix_norms)

    print(
        f"[MODEL] Доступно слів: {len(words_available)}/{len(words)}. "
        f"Відсутніх: {len(missing_words)}"
    )
    if missing_words:
        preview = ", ".join(missing_words[:10])
        suffix = " ..." if len(missing_words) > 10 else ""
        print(f"[MODEL] Приклади відсутніх: {preview}{suffix}")

    return EmbeddingResources(
        model_path=resolved_model_path,
        vectors=vectors,
        words_available=words_available,
        matrix=matrix,
        matrix_norms=matrix_norms,
        missing_words=missing_words,
    )


def _rank_words(target_word: str, resources: EmbeddingResources) -> List[dict]:
    target_vector = resources.vectors.get(target_word)
    if target_vector is None:
        raise ValueError(f"Слово '{target_word}' відсутнє у векторній моделі.")

    target_norm = float(np.linalg.norm(target_vector))
    if target_norm == 0.0:
        raise ValueError(f"Нульова норма вектора для слова '{target_word}'.")

    similarities = (resources.matrix @ target_vector) / (resources.matrix_norms * target_norm)
    order = np.argsort(similarities)[::-1]

    ranked_words = [
        {
            "word": resources.words_available[idx],
            "similarity": float(similarities[idx]),
            "rank": rank,
        }
        for rank, idx in enumerate(order, start=1)
    ]
    return ranked_words


def generate_rankings(
    target_word,
    target_date,
    definitions,
    words,
    resources: Optional[EmbeddingResources] = None,
    output_path: Optional[str] = None,
):
    """
    Будує рейтинг схожості слів зі списку `words` до `target_word`
    на основі локальної GloVe-моделі.

    Параметр `definitions` залишено для сумісності зі старими викликами,
    але він більше не використовується.
    """
    _ = definitions  # backward compatibility
    ensure_precomputed_dir()

    if resources is None:
        resources = load_embedding_resources(words=words, daily_words=[target_word])

    print(f"[{target_date}] Обробка слова: {target_word}")
    ranked_words = _rank_words(target_word, resources)

    filename = output_path or os.path.join(PRECOMPUTED_DIR, f"{target_date}.json")
    output_dir = os.path.dirname(filename)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(ranked_words, f, ensure_ascii=False, indent=2)

    print(f"Збережено у {filename} ({len(ranked_words)} слів)")
    return ranked_words


def _read_nonempty_lines(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"Некоректна дата '{value}'. Формат має бути YYYY-MM-DD."
        ) from e


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Генерує рейтинги семантичної схожості на основі "
            "локальної GloVe-моделі. Без аргументів працює пакетно "
            "для всіх слів із data/daily_words.txt."
        )
    )
    parser.add_argument(
        "--word",
        type=str,
        help="Згенерувати рейтинг для одного довільного слова.",
    )
    parser.add_argument(
        "--date",
        type=_parse_iso_date,
        help="Дата для імені файлу у режимі --word (формат: YYYY-MM-DD).",
    )
    parser.add_argument(
        "--output",
        type=str,
        help=(
            "Явний шлях до JSON-файлу в режимі --word. "
            "Якщо не вказано, використовується precomputed/<date>.json."
        ),
    )
    parser.add_argument(
        "--wordlist",
        type=str,
        default="data/wordlist.txt",
        help="Шлях до wordlist (за замовчуванням: data/wordlist.txt).",
    )
    parser.add_argument(
        "--daily-words",
        type=str,
        default="data/daily_words.txt",
        help="Шлях до daily_words для пакетного режиму.",
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default=None,
        help=(
            "Шлях до векторної моделі. "
            "За замовчуванням береться LOCAL_EMBEDDINGS_PATH або models/..."
        ),
    )
    return parser


def _run_single_word_mode(args: argparse.Namespace) -> None:
    word = args.word.strip() if args.word else ""
    if not word:
        raise SystemExit("Помилка: передано порожнє значення для --word.")

    words = _read_nonempty_lines(args.wordlist)
    target_date = args.date or date.today()
    output_path = _normalize_path(args.output) if args.output else None

    resources = load_embedding_resources(
        words=words,
        daily_words=[word],
        model_path=args.model_path,
    )

    generate_rankings(
        target_word=word,
        target_date=target_date,
        definitions=None,
        words=words,
        resources=resources,
        output_path=output_path,
    )


def _run_batch_mode(args: argparse.Namespace) -> None:
    ensure_precomputed_dir()
    daily_words = _read_nonempty_lines(args.daily_words)
    words = _read_nonempty_lines(args.wordlist)
    resources = load_embedding_resources(
        words=words,
        daily_words=daily_words,
        model_path=args.model_path,
    )

    for i, target_word in enumerate(daily_words):
        day = BASE_DATE + timedelta(days=i)
        output_file = os.path.join(PRECOMPUTED_DIR, f"{day}.json")

        if os.path.exists(output_file):
            print(f"Пропущено {target_word} (вже існує)")
            continue

        try:
            generate_rankings(target_word, day, definitions=None, words=words, resources=resources)
        except Exception as e:
            print(f"[ERR ] Не вдалося згенерувати рейтинг для '{target_word}' ({day}): {e}")


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    if args.word is not None:
        _run_single_word_mode(args)
    else:
        _run_batch_mode(args)
