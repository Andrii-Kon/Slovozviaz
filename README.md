

# Slovozviaz

[](https://www.python.org/)
[](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[](https://flask.palletsprojects.com/)
[](https://opensource.org/licenses/MIT)

**Slovozviaz** is a Ukrainian-language semantic puzzle game where the player must guess a secret daily word by relying on the semantic (meaning-based) similarity of their guesses. Unlike games based on spelling, this game focuses on the meaning of words.


### ➡️ **[Play Slovozviaz](https://andriikon.pythonanywhere.com)** ⬅️


## 📜 Description

The main objective of the game is to guess a secret word by entering other words as guesses. After each guess, the system displays its rank—its position in a list sorted by semantic similarity to the target word]. The lower the rank, the closer your guess is in meaning.

The uniqueness of this project lies in its use of state-of-the-art Natural Language Processing (NLP) models from OpenAI to calculate semantic similarity, which provides high accuracy and intuitive rankings.

## ✨ Features

  * **Semantic Gameplay:** Guess the word based on its meaning, not its spelling.
  * **Ranking System:** Get instant feedback in the form of a rank that shows the semantic similarity of your guess.
  * **Unlimited Guesses:** You can keep guessing until you find the correct word.
  * **Game Archive:** Play games from previous days by selecting them from a list.
  * **Hint System:** If you get stuck, the game can offer a word to help you get closer to the answer.
  * **Progress Saving:** Your game state for each date is saved locally in your browser.
  * **Responsive Design:** The game features a dark theme and displays correctly on various devices.

## 🧠 How It Works: The Technology

Unlike similar popular games that use older models like Word2Vec or GloVe, "Slovozviaz" employs an innovative approach to achieve more accurate semantic similarity.

1.  **Definition Generation:** For each word in the game's corpus (\~14,000 Ukrainian nouns), a short and precise semantic definition is generated using the `gpt-3.5-turbo` model.
2.  **Embedding Creation:** Instead of vectorizing the word itself, its definition is vectorized. This helps to avoid ambiguity (polysemy) and focus on the word's primary meaning. This is done using OpenAI's most powerful model, `text-embedding-3-large`.
3.  **Similarity Calculation:** The closeness between the secret word's vector and the guess's vector is calculated using **cosine similarity**.
4.  **Pre-computation:** All rankings for all possible game days are calculated in advance (offline). The web application only needs to quickly fetch the pre-computed ranking from a database, ensuring an instant response for the user.

## 🛠️ Tech Stack

  * **Backend:**
      * **Language:** Python 3
      * **Framework:** Flask 
      * **Server:** Gunicorn
      * **Database:** SQLite
      * **ORM:** SQLAlchemy
  * **Frontend:**
      * **Languages:** HTML, CSS, JavaScript (Vanilla JS)
  * **APIs & Services:**
      * **NLP Models:** OpenAI API (`text-embedding-3-large`, `gpt-3.5-turbo`)

## 🗂️ Project Structure

```
├── app.py                  # Main Flask application file, API logic
├── generate_definitions.py   # Script to generate definitions via OpenAI
├── generate_rankings.py    # Script to create embeddings and calculate rankings
├── generate_archives.py    # Script to populate the SQLite database with games
├── requirements.txt        # Python project dependencies
├── wordlist.txt            # The main dictionary of nouns used in the game
├── definitions.json        # Generated words definitions
├── embeddings_cache.json   # Cache file for the retrieved word embeddings
├── instance/
│   └── games.db            # SQLite database with the game archive
├── precomputed/            # Directory for temporarily storing JSON rankings
├── static/                 # Frontend assets
│   ├── css/style.css
│   └── js/main.js
└── templates/
    └── index.html          # Main HTML template for the game
```

## 🚀 Installation and Setup

To run this project locally, follow these steps:

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/Andrii-Kon/Slovozviaz.git
    cd Slovozviaz
    ```

2.  **Create and activate a virtual environment:**

    ```bash
    python -m venv venv
    # For Windows
    venv\Scripts\activate
    # For macOS/Linux
    source venv/bin/activate
    ```

3.  **Install the dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Set up your OpenAI API key:**

      * Create a `.env` file in the project's root directory.
      * Add your OpenAI API key to it:
        ```
        OPENAI_API_KEY='sk-...'
        ```

5.  **Generate the game data (one-time process):**

      * This step can be time-consuming and will incur costs on your OpenAI account.
      * First, generate the definitions:
        ```bash
        python generate_definitions.py
        ```
      * Next, generate the rankings for the words (you can add words to `daily_words.txt`):
        ```bash
        python generate_archives.py
        ```

6.  **Run the web server:**

    ```bash
    flask run
    ```

    Open `http://127.0.0.1:5000` in your browser.

## 🎲 How to Play

1.  Navigate to the game's website.
2.  Enter a guess in the input field and press "Submit".
3.  Analyze the rank of your guess. The lower the rank, the closer you are to the target.
4.  Continue guessing until you find the word with rank 1.
5.  Use hints if necessary, or play games from the archive.

-----

Developed by [Andrii Kononchuk](https://www.google.com/search?q=https://github.com/Andrii-Kon).
