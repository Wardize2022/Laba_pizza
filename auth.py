import hashlib
import re
import sqlite3
import time
from flask import Blueprint, render_template, request, session, redirect, url_for, flash, abort, g
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db

bp = Blueprint('auth', __name__)
LOCAL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+")
LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
DUMMY_HASH = generate_password_hash('not-a-user-password')


def normalize_email(value):
    email = value.strip().lower()
    if len(email) > 254 or email.count('@') != 1:
        raise ValueError('Укажите почту длиной до 254 символов: name@example.com.')
    local, domain = email.split('@')
    labels = domain.split('.')
    if (not 1 <= len(local) <= 64 or not LOCAL.fullmatch(local)
            or local.startswith('.') or local.endswith('.') or '..' in local
            or len(labels) < 2 or not all(LABEL.fullmatch(x) for x in labels)
            or not re.fullmatch(r'[a-z]{2,63}', labels[-1])):
        raise ValueError('Некорректная почта. Используйте латиницу, адрес без пробелов и домен вида example.com.')
    return email


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('orders.mine'))
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            if not 2 <= len(name) <= 60 or any(ord(c) < 32 for c in name):
                raise ValueError('Имя должно содержать от 2 до 60 символов.')
            email = normalize_email(request.form.get('email', ''))
            password = request.form.get('password', '')
            if not 8 <= len(password) <= 128 or password.isspace():
                raise ValueError('Пароль должен содержать от 8 до 128 символов и не состоять из пробелов.')
            if password != request.form.get('confirm_password'):
                raise ValueError('Пароли не совпадают.')
            db = get_db()
            with db:
                db.execute('INSERT INTO users(name,email,password_hash) VALUES(?,?,?)',
                           (name, email, generate_password_hash(password)))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error, ValueError) else 'Эта почта уже зарегистрирована.', 'error')
            return render_template('register.html'), 400
        flash('Аккаунт создан. Войдите с вашей почтой и паролем.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('register.html')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('admin.dashboard' if g.user['is_admin'] else 'orders.mine'))
    if request.method == 'POST':
        identity = request.form.get('email', '').strip().lower()
        if identity != 'admin':
            try:
                identity = normalize_email(identity)
            except ValueError as error:
                flash(str(error), 'error')
                return render_template('login.html'), 400
        db = get_db()
        key = hashlib.sha256(f'{request.remote_addr}|{identity}'.encode()).hexdigest()
        now = time.time()
        with db:
            db.execute('DELETE FROM login_attempts WHERE started < ?', (now - 60,))
        attempt = db.execute('SELECT * FROM login_attempts WHERE key=?', (key,)).fetchone()
        if attempt and attempt['failures'] >= 5:
            abort(429, 'Слишком много попыток входа. Подождите одну минуту.')
        user = db.execute('SELECT * FROM users WHERE email=?', (identity,)).fetchone()
        password = request.form.get('password', '')
        valid = len(password) <= 128 and check_password_hash(user['password_hash'] if user else DUMMY_HASH, password)
        if not user or not valid:
            with db:
                db.execute('INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET failures=failures+1', (key, now))
            flash('Неверная почта / логин или пароль.', 'error')
            return render_template('login.html'), 400
        with db:
            db.execute('DELETE FROM login_attempts WHERE key=?', (key,))
        cart = session.get('cart', {})
        session.clear()
        session['user_id'] = user['id']
        session['cart'] = cart
        return redirect(url_for('admin.dashboard' if user['is_admin'] else ('shop.cart' if cart else 'orders.mine')))
    return render_template('login.html')


@bp.post('/logout')
def logout():
    session.clear()
    flash('Вы вышли из аккаунта.', 'info')
    return redirect(url_for('index'))
