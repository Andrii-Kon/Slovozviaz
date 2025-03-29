from flask import Flask, render_template, jsonify, request
import json

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ranked")
def get_ranked():
    with open("ranked_words.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)

@app.route("/guess", methods=["POST"])
def guess():
    data = request.get_json()
    user_word = data.get("word")

    with open("ranked_words.json", "r", encoding="utf-8") as f:
        ranked_words = json.load(f)

    for item in ranked_words:
        if item["word"] == user_word:
            return jsonify({"rank": item["rank"], "similarity": item["similarity"]})

    return jsonify({"error": "Слово не знайдено"}), 404

if __name__ == "__main__":
    app.run(debug=True)