def load_words(filename):
    """
    Завантажує слова з файлу та повертає їх як множину (для швидкого пошуку).
    """
    with open(filename, "r", encoding="utf-8") as f:
        # Перетворюємо кожен рядок в нижній регістр для уніфікації
        return {line.strip().lower() for line in f if line.strip()}

def main():
    daily_words = load_words("daily_words.txt")
    full_words = load_words("wordlist.txt")

    # Знаходимо слова, яких немає у wordlist.txt
    missing_words = daily_words - full_words

    if missing_words:
        print("Слова, яких не знайдено у wordlist.txt:")
        for word in sorted(missing_words):
            print(word)
    else:
        print("Всі слова з daily_words.txt присутні у wordlist.txt.")

if __name__ == "__main__":
    main()