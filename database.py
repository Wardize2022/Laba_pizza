import sqlite3
from flask import current_app, g
from werkzeug.security import generate_password_hash


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def init_app(app):
    @app.teardown_appcontext
    def close_db(error=None):
        db = g.pop('db', None)
        if db is not None:
            db.close()
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql') as f:
            db.executescript(f.read().decode('utf-8'))
        if not db.execute('SELECT id FROM users WHERE email = ?', ('admin',)).fetchone():
            db.execute('INSERT INTO users(name,email,password_hash,is_admin) VALUES(?,?,?,1)',
                       ('Администратор', 'admin', generate_password_hash(app.config['ADMIN_PASSWORD'])))
            db.commit()
