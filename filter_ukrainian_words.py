import re
import os

# Дозволені українські літери
ukrainian_letters = "абвгґдежзийіїклмнопрстуфхцчшщьюяАБВГҐДЕЖЗИЙІЇКЛМНОПРСТУФХЦЧШЩЬЮЯ"
allowed_special = "'-"

# Регулярний вираз: дозволені символи
valid_word_regex = re.compile(rf"^[{ukrainian_letters}{allowed_special}]+$")

def is_valid_word(word):
    # Повністю складається з дозволених символів
    if not valid_word_regex.match(word):
        return False

    # Має не більше однієї великої літери (тобто не абревіатура)
    uppercase_count = sum(1 for c in word if c.isupper())
    if uppercase_count > 1:
        return False

    return True

def append_new_words(input_file="wordlistold.txt", output_file="wordlist.txt"):
    # Завантажуємо вже наявні слова з output_file
    existing_words = set()
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            existing_words = set(line.strip() for line in f if line.strip())

    with open(input_file, "r", encoding="utf-8") as f:
        raw_words = [line.strip() for line in f if line.strip()]

    # Фільтруємо лише валідні і нові слова
    new_words = [w for w in raw_words if is_valid_word(w) and w not in existing_words]

    # Дозаписуємо нові слова в кінець файлу
    with open(output_file, "a", encoding="utf-8") as f:
        for word in new_words:
            f.write(word + "\n")

    print(f"✅ Додано нових слів: {len(new_words)} (з {len(raw_words)} перевірених). Всього слів тепер у '{output_file}': {len(existing_words) + len(new_words)}.")

if __name__ == "__main__":
    append_new_words()
