from app import app, db, ArchivedGame

with app.app_context():
    games = ArchivedGame.query.all()
    for game in games:
        print(f"ID: {game.id}, Date: {game.game_date}, Word: {game.secret_word}, Created: {game.created_at}")
