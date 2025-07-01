def check_words_presence(daily_words_file="daily_words.txt", wordlist_file="wordlist.txt"):
    """
    Перевіряє наявність слів з daily_words.txt у wordlist.txt,
    а також шукає повтори у файлі daily_words.txt.
    Функція тільки читає файли і не змінює їх.
    """
    try:
        # 1. Читаємо daily_words.txt у список, щоб перевірити на повтори
        with open(daily_words_file, 'r', encoding='utf-8') as f:
            # Створюємо список, прибираючи зайві пробіли та переводячи у нижній регістр
            daily_words_list = [line.strip().lower() for line in f if line.strip()]

        # 2. ПЕРЕВІРКА НА ПОВТОРИ (ДУБЛІКАТИ) у daily_words.txt
        seen = set()
        duplicates = set()
        for word in daily_words_list:
            if word in seen:
                duplicates.add(word)
            seen.add(word)

        # Якщо знайшли дублікати, виводимо попередження
        if duplicates:
            print("⚠️ Увага! У файлі daily_words.txt знайдені однакові слова (повтори):")
            for word in sorted(list(duplicates)):
                print(f"  - {word}")
            print("-" * 20)  # Розділювач для кращої читабельності

        # 3. ПЕРЕВІРКА НАЯВНОСТІ СЛІВ у wordlist.txt
        # Створюємо множину унікальних слів з daily_words.txt
        daily_words_set = set(daily_words_list)

        # Читаємо основний список слів
        with open(wordlist_file, 'r', encoding='utf-8') as f:
            wordlist_set = {line.strip().lower() for line in f}

        # Знаходимо слова, яких немає в основному списку
        missing_words = daily_words_set.difference(wordlist_set)

        if not missing_words:
            print("✅ Успіх! Усі (унікальні) слова з daily_words.txt знайдено у wordlist.txt.")
        else:
            print(f"❌ Увага! Знайдено слова з daily_words.txt, яких немає у wordlist.txt:")
            for word in sorted(list(missing_words)):
                print(f"  - {word}")

    except FileNotFoundError as e:
        print(f"Помилка: Не вдалося знайти файл. Перевірте назву та шлях: {e.filename}")
    except Exception as e:
        print(f"Сталася непередбачувана помилка: {e}")

# --- Просто запустіть цей виклик ---
# Він виконає перевірку ваших існуючих файлів.
check_words_presence()