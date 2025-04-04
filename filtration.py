import re
import json

# Ім'я вхідного лог-файлу та вихідного JSON-файлу
LOG_FILE = "log.txt"
OUTPUT_FILE = "definitions.json"

# Регулярний вираз для рядків з успішними визначеннями.
# Припускаємо, що рядок має формат:
# 📌 'слово': визначення
pattern = re.compile(r"📌\s*'([^']+)':\s*(.+)")

definitions = {}

# Зчитуємо лог-файл
with open(LOG_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        match = pattern.match(line)
        if match:
            word = match.group(1).strip()
            definition = match.group(2).strip()
            definitions[word] = definition

# Записуємо отриманий словник у JSON-файл
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(definitions, f, ensure_ascii=False, indent=2)

print(f"Виділено {len(definitions)} визначень. Результат збережено у файл: {OUTPUT_FILE}")
