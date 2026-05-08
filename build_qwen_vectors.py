import argparse
import os
from pathlib import Path
from typing import List

import numpy as np
from sentence_transformers import SentenceTransformer


DEFAULT_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_CACHE_PATH = Path("models/sentence-transformers")
DEFAULT_WORDLIST_PATH = Path("data/wordlist.txt")
DEFAULT_OUTPUT_PATH = Path("data/word_vectors_qwen3_0_6b_wordlist_fp16.npz")
DEFAULT_TEMPLATE = (
    "Instruct: Represent the meaning of this single Ukrainian word for "
    "semantic similarity search. Query: {word}"
)


def _read_nonempty_lines(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _normalize_path(path: Path) -> Path:
    return Path(os.path.normpath(str(path).replace("\\", os.sep)))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build live word vectors for Slovozviaz with Qwen3 Embedding."
    )
    parser.add_argument(
        "--wordlist",
        type=Path,
        default=DEFAULT_WORDLIST_PATH,
        help=f"Path to wordlist (default: {DEFAULT_WORDLIST_PATH}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Where to save the .npz live vectors (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_NAME,
        help=f"Hugging Face model name/path (default: {DEFAULT_MODEL_NAME}).",
    )
    parser.add_argument(
        "--cache-folder",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help=f"SentenceTransformers cache folder (default: {DEFAULT_CACHE_PATH}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Encoding batch size (default: 32). Lower this if RAM is tight.",
    )
    parser.add_argument(
        "--float32",
        action="store_true",
        help="Save vectors as float32 instead of float16.",
    )
    parser.add_argument(
        "--template",
        default=DEFAULT_TEMPLATE,
        help="Embedding template. Must contain {word}.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    wordlist_path = _normalize_path(args.wordlist)
    output_path = _normalize_path(args.output)
    cache_folder = _normalize_path(args.cache_folder)

    if "{word}" not in args.template:
        raise ValueError("--template must contain {word}.")
    if not wordlist_path.is_file():
        raise FileNotFoundError(f"wordlist not found: '{wordlist_path}'")

    words = _read_nonempty_lines(wordlist_path)
    if not words:
        raise RuntimeError(f"wordlist is empty: '{wordlist_path}'")

    print(f"[QWEN] Loading model: {args.model}")
    print(f"[QWEN] Cache folder: {cache_folder}")
    model = SentenceTransformer(
        args.model,
        cache_folder=str(cache_folder),
        device="cpu",
    )

    texts = [args.template.format(word=word) for word in words]
    print(f"[QWEN] Encoding {len(words)} words; batch_size={args.batch_size}")
    vectors = model.encode(
        texts,
        batch_size=args.batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1).astype(np.float32)
    norms = np.where(norms == 0.0, 1e-12, norms)

    vectors_dtype = np.float32 if args.float32 else np.float16
    vectors_to_save = vectors.astype(vectors_dtype)
    words_arr = np.asarray(words, dtype=np.str_)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        words=words_arr,
        vectors=vectors_to_save,
        norms=norms,
        model=np.asarray(args.model),
        template=np.asarray(args.template),
    )

    saved_bytes = output_path.stat().st_size
    raw_bytes = vectors_to_save.nbytes + norms.nbytes + words_arr.nbytes

    print(f"[QWEN] Saved: {output_path}")
    print(
        f"[QWEN] Words: {len(words_arr)}; dim={vectors_to_save.shape[1]}; "
        f"dtype={vectors_to_save.dtype}"
    )
    print(f"[QWEN] Uncompressed arrays: {raw_bytes / (1024 * 1024):.2f} MB")
    print(f"[QWEN] .npz file: {saved_bytes / (1024 * 1024):.2f} MB")


if __name__ == "__main__":
    main()
