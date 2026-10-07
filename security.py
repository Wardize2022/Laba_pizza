import secrets
from functools import wraps
from flask import abort, g, session, request, redirect, url_for, flash
from database import get_db
from catalog import status_label, next_statuses
from location import PIZZERIA_ADDRESS, MAP_EMBED_URL, MAP_LINK


def csrf_token():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_hex(32)
    return session['_csrf']


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash('Войдите в аккаунт, чтобы продолжить.', 'info')
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user['is_admin']:
            abort(403, 'Эта страница доступна только администратору.')
        return view(*args, **kwargs)
    return wrapped


def init_security(app):
    @app.before_request
    def load_user_and_check_csrf():
        g.user = get_db().execute('SELECT * FROM users WHERE id = ?', (session.get('user_id'),)).fetchone()
        if request.method == 'POST':
            token = request.form.get('csrf_token', '')
            if not token or not secrets.compare_digest(token.encode(), session.get('_csrf', '').encode()):
                abort(400, 'Форма устарела. Обновите страницу и повторите действие.')

    @app.context_processor
    def helpers():
        return dict(pizzeria_address=PIZZERIA_ADDRESS, map_embed_url=MAP_EMBED_URL, map_link=MAP_LINK,
                    csrf_token=csrf_token, status_label=status_label, next_statuses=next_statuses,
                    cart_count=sum(session.get('cart', {}).values()))
