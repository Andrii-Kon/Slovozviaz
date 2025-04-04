import json
import re

input_filename = "definitions2.json"
output_filename = "old.json"

# Завантажуємо оригінальні визначення
with open(input_filename, "r", encoding="utf-8") as f:
    definitions = json.load(f)

clean_definitions = {}
for word, definition in definitions.items():
    # Створюємо регулярний вираз, який шукає слово на початку,
    # за яким можуть йти пробіли і розділовий знак (—, -, :)
    pattern = re.compile(r'^\s*' + re.escape(word) + r'\s*(?:—|-|:)?\s*', flags=re.IGNORECASE)
    cleaned = pattern.sub("", definition, count=1)
    clean_definitions[word] = cleaned

# Записуємо очищені визначення у новий файл
with open(output_filename, "w", encoding="utf-8") as f:
    json.dump(clean_definitions, f, ensure_ascii=False, indent=2)

print(f"Clean definitions saved to {output_filename}")
