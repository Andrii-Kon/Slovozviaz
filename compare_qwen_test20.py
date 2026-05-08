import json
from pathlib import Path
from typing import Dict, List

import numpy as np


TEST_WORDS_PATH = Path("data/qwen_test_words_20.txt")
QWEN_VECTORS_PATH = Path("data/word_vectors_qwen3_test20_fp16.npz")
FASTTEXT_VECTORS_PATH = Path("data/word_vectors_fasttext_uk_wordlist_fp16.npz")
OUTPUT_JSON_PATH = Path("data/qwen_test20_ranking_comparison.json")
OUTPUT_MD_PATH = Path("data/qwen_test20_ranking_comparison.md")


def read_words(path: Path) -> List[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_vectors(path: Path, required_words: List[str]) -> Dict[str, np.ndarray]:
    required_set = set(required_words)
    with np.load(path, allow_pickle=False) as payload:
        words = [str(word) for word in payload["words"].tolist()]
        vectors = payload["vectors"].astype(np.float32, copy=False)

    loaded = {
        word: vectors[idx]
        for idx, word in enumerate(words)
        if word in required_set
    }
    missing = [word for word in required_words if word not in loaded]
    if missing:
        raise RuntimeError(f"{path} is missing words: {', '.join(missing)}")
    return loaded


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def rank_words(target: str, words: List[str], vectors: Dict[str, np.ndarray]) -> List[dict]:
    target_vector = vectors[target]
    ranking = [
        {
            "word": word,
            "similarity": cosine(target_vector, vectors[word]),
        }
        for word in words
    ]
    ranking.sort(key=lambda item: item["similarity"], reverse=True)
    for rank, item in enumerate(ranking, start=1):
        item["rank"] = rank
    return ranking


def compact_top(ranking: List[dict], limit: int = 8) -> str:
    return ", ".join(
        f"{item['rank']}. {item['word']} ({item['similarity']:.3f})"
        for item in ranking[:limit]
    )


def main() -> None:
    words = read_words(TEST_WORDS_PATH)
    qwen_vectors = load_vectors(QWEN_VECTORS_PATH, words)
    fasttext_vectors = load_vectors(FASTTEXT_VECTORS_PATH, words)

    comparison = []
    for target in words:
        qwen_ranking = rank_words(target, words, qwen_vectors)
        fasttext_ranking = rank_words(target, words, fasttext_vectors)
        comparison.append(
            {
                "target": target,
                "qwen3_top_20": qwen_ranking,
                "fasttext_top_20": fasttext_ranking,
            }
        )

    OUTPUT_JSON_PATH.write_text(
        json.dumps(
            {
                "words": words,
                "qwen_vectors": str(QWEN_VECTORS_PATH),
                "fasttext_vectors": str(FASTTEXT_VECTORS_PATH),
                "comparison": comparison,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Qwen3 vs fastText 20-word ranking comparison",
        "",
        f"Words: {', '.join(words)}",
        "",
        "Each row ranks only within this 20-word test set.",
        "",
        "| Target | Qwen3 top 8 | fastText top 8 |",
        "|---|---|---|",
    ]
    for item in comparison:
        target = item["target"]
        qwen_top = compact_top(item["qwen3_top_20"])
        fasttext_top = compact_top(item["fasttext_top_20"])
        lines.append(f"| {target} | {qwen_top} | {fasttext_top} |")

    OUTPUT_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Generated comparison for {len(words)} words:")
    print(", ".join(words))
    print(f"JSON: {OUTPUT_JSON_PATH}")
    print(f"Markdown: {OUTPUT_MD_PATH}")


if __name__ == "__main__":
    main()
