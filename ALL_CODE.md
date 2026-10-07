# Весь код новой версии «Пять сыров»

Готовые файлы находятся рядом в архиве. Запуск: START_HERE.md.

## .gitignore

```text
.venv/
venv/
__pycache__/
*.py[cod]
.env
.env.*
.idea/
.vscode/
.DS_Store
Thumbs.db
.pytest_cache/
instance/
*.sqlite3
*.sqlite3-*
```

## admin.py

```python
from flask import Blueprint, request, render_template, redirect, url_for, abort, g, flash
from database import get_db
from security import admin_required
from catalog import STATUSES, next_statuses

bp = Blueprint('admin', __name__, url_prefix='/admin')


@bp.get('')
@admin_required
def dashboard():
    status = request.args.get('status', '')
    if status and status not in STATUSES:
        abort(400, 'Неизвестный статус.')
    sql = 'SELECT orders.*, users.email FROM orders JOIN users ON users.id=orders.user_id'
    params = ()
    if status:
        sql += ' WHERE orders.status=?'
        params = (status,)
    orders = get_db().execute(sql + ' ORDER BY orders.id DESC', params).fetchall()
    counts = dict(get_db().execute('SELECT status,count(*) FROM orders GROUP BY status').fetchall())
    return render_template('admin.html', orders=orders, statuses=STATUSES, selected=status, counts=counts)


@bp.post('/orders/<int:order_id>/status')
@admin_required
def change_status(order_id):
    db = get_db()
    order = db.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if order is None:
        abort(404, 'Заказ не найден.')
    status = request.form.get('status')
    if request.form.get('previous_status') != order['status']:
        abort(409, 'Статус уже изменён. Обновите страницу.')
    if status not in next_statuses(order):
        abort(400, 'Недопустимый переход статуса. Следуйте порядку приготовления и доставки.')
    with db:
        changed = db.execute('UPDATE orders SET status=? WHERE id=? AND status=?', (status, order_id, order['status']))
        if changed.rowcount != 1:
            abort(409, 'Статус уже изменён. Обновите страницу.')
        db.execute('INSERT INTO order_events(order_id,status,actor_id) VALUES(?,?,?)', (order_id, status, g.user['id']))
    flash('Статус заказа обновлён.', 'success')
    return redirect(url_for('orders.detail', order_id=order_id))
```

## app.py

```python
import os
import secrets
from pathlib import Path
from flask import Flask, render_template, request


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config.update(
        DATABASE=str(Path(app.instance_path) / 'pizza.sqlite3'),
        SECRET_KEY=os.environ.get('SECRET_KEY'),
        ADMIN_PASSWORD=os.environ.get('ADMIN_PASSWORD', 'admin'),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        MAX_CONTENT_LENGTH=32 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config['SECRET_KEY']:
        key_path = Path(app.instance_path) / 'secret.key'
        try:
            with key_path.open('x', encoding='utf-8') as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        app.config['SECRET_KEY'] = key_path.read_text(encoding='utf-8')
    from database import init_app
    from security import init_security
    from routes import register_routes
    from auth import bp as auth_bp
    from shop import bp as shop_bp
    from orders import bp as orders_bp
    from admin import bp as admin_bp
    init_app(app)
    init_security(app)
    register_routes(app)
    for bp in (auth_bp, shop_bp, orders_bp, admin_bp):
        app.register_blueprint(bp)

    @app.template_filter('money')
    def money(kopecks):
        return f'{kopecks / 100:,.2f}'.replace(',', ' ').replace('.', ',') + ' ₽'

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        if not request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    for code in (400, 403, 404, 409, 413, 429):
        app.register_error_handler(code, lambda error: (render_template('error.html', error=error), error.code))
    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
```

## auth.py

```python
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
```

## catalog.py

```python
PIZZAS = {
    1: dict(name='Маргарита', tag='Классика', price=49000, ingredients='Томатный соус, моцарелла, свежий базилик.'),
    2: dict(name='Пепперони', tag='Мясная', price=59000, ingredients='Томатный соус, моцарелла, колбаски пепперони.'),
    3: dict(name='Пять сыров', tag='Фирменная', price=69000, ingredients='Сливочный соус, моцарелла, чеддер, пармезан, дорблю, гауда.'),
    4: dict(name='Курица и грибы', tag='С курицей', price=62000, ingredients='Сливочный соус, моцарелла, куриное филе, шампиньоны.'),
    5: dict(name='Овощная', tag='Овощная', price=55000, ingredients='Томатный соус, моцарелла, томаты, сладкий перец, шампиньоны, маслины.'),
    6: dict(name='Барбекю', tag='Сытная', price=65000, ingredients='Соус барбекю, моцарелла, куриное филе, бекон, красный лук.'),
}
STATUSES = {'accepted': 'Принят', 'cooking': 'Готовится', 'ready': 'Передан в доставку',
            'on_way': 'Курьер в пути', 'delivered': 'Доставлен', 'cancelled': 'Отменён'}


def status_label(status, fulfillment='delivery'):
    if fulfillment == 'pickup':
        return {'ready': 'Готов к выдаче', 'delivered': 'Выдан'}.get(status, STATUSES[status])
    return STATUSES[status]


def next_statuses(order):
    steps = ['accepted', 'cooking', 'ready', 'on_way', 'delivered']
    if order['fulfillment'] == 'pickup':
        steps.remove('on_way')
    if order['status'] in ('delivered', 'cancelled'):
        return []
    return [steps[steps.index(order['status']) + 1], 'cancelled']
```

## database.py

```python
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
```

## location.py

```python
from urllib.parse import quote

# Shared address for contacts, map and pickup orders.
PIZZERIA_ADDRESS = 'Санкт-Петербург, Будапештская улица, 38'
MAP_QUERY = quote(PIZZERIA_ADDRESS)
MAP_EMBED_URL = f'https://maps.google.com/maps?q={MAP_QUERY}&z=17&output=embed'
MAP_LINK = f'https://www.google.com/maps/search/?api=1&query={MAP_QUERY}'
```

## orders.py

```python
from flask import Blueprint, g, abort, render_template
from database import get_db
from security import login_required

bp = Blueprint('orders', __name__)


def get_order(order_id):
    order = get_db().execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if order is None or (order['user_id'] != g.user['id'] and not g.user['is_admin']):
        abort(404, 'Заказ не найден.')
    return order


@bp.get('/orders')
@login_required
def mine():
    orders = get_db().execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC', (g.user['id'],)).fetchall()
    return render_template('orders.html', orders=orders)


@bp.get('/orders/<int:order_id>')
@login_required
def detail(order_id):
    order = get_order(order_id)
    db = get_db()
    items = db.execute('SELECT * FROM order_items WHERE order_id=? ORDER BY id', (order_id,)).fetchall()
    events = db.execute('SELECT * FROM order_events WHERE order_id=? ORDER BY id', (order_id,)).fetchall()
    return render_template('order_detail.html', order=order, items=items, events=events)
```

## requirements.txt

```text
Flask==3.1.3
```

## routes.py

```python
from flask import render_template
from catalog import PIZZAS


def register_routes(app):
    """Регистрирует маршруты на переданном Flask-приложении."""

    # УЧАСТНИК 1: Главная — начало
    @app.route("/")
    def index():
        return render_template("index.html")
    # УЧАСТНИК 1: Главная — конец

    # УЧАСТНИК 2: Меню — начало
    @app.route("/menu")
    def menu():
        return render_template("menu.html", pizzas=PIZZAS)
    # УЧАСТНИК 2: Меню — конец

    # УЧАСТНИК 3: Акции — начало
    @app.route("/promotions")
    def promotions():
        return render_template("promotions.html")
    # УЧАСТНИК 3: Акции — конец

    # УЧАСТНИК 4: Доставка и оплата — начало
    @app.route("/delivery")
    def delivery():
        return render_template("delivery.html")
    # УЧАСТНИК 4: Доставка и оплата — конец

    # УЧАСТНИК 5: Контакты — начало
    @app.route("/contacts")
    def contacts():
        return render_template("contacts.html")
    # УЧАСТНИК 5: Контакты — конец
```

## schema.sql

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0 CHECK(is_admin IN (0,1))
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
    status TEXT NOT NULL DEFAULT 'accepted' CHECK(status IN ('accepted','cooking','ready','on_way','delivered','cancelled')),
    fulfillment TEXT NOT NULL CHECK(fulfillment IN ('delivery','pickup')),
    name TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL, comment TEXT NOT NULL,
    subtotal INTEGER NOT NULL, discount INTEGER NOT NULL, delivery_fee INTEGER NOT NULL,
    total INTEGER NOT NULL CHECK(total >= 0), checkout_token TEXT NOT NULL,
    UNIQUE(user_id, checkout_token)
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
    pizza_id INTEGER NOT NULL, name TEXT NOT NULL, unit_price INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 20)
);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
    status TEXT NOT NULL, actor_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
);
CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, started REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, id);
CREATE INDEX IF NOT EXISTS idx_events_order ON order_events(order_id, id);
```

## security.py

```python
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
```

## shop.py

```python
import re
import secrets
import sqlite3
from flask import Blueprint, session, request, render_template, redirect, url_for, flash, abort, g
from catalog import PIZZAS
from database import get_db
from security import login_required
from location import PIZZERIA_ADDRESS

bp = Blueprint('shop', __name__)


def cart_items():
    return [dict(id=int(key), quantity=qty, **PIZZAS[int(key)])
            for key, qty in session.get('cart', {}).items() if int(key) in PIZZAS and 1 <= qty <= 20]


def totals(items, fulfillment):
    subtotal = sum(p['price'] * p['quantity'] for p in items)
    # Prices are integer kopecks; offers never stack.
    discount = subtotal // 10 if fulfillment == 'pickup' or sum(p['quantity'] for p in items) >= 3 else 0
    net = subtotal - discount
    delivery_fee = 15000 if fulfillment == 'delivery' and net < 100000 else 0
    return dict(subtotal=subtotal, discount=discount, delivery_fee=delivery_fee, total=net + delivery_fee)


@bp.post('/cart/add/<int:pizza_id>')
def add(pizza_id):
    if pizza_id not in PIZZAS:
        abort(404)
    data = dict(session.get('cart', {}))
    key = str(pizza_id)
    if data.get(key, 0) >= 20:
        flash('Можно заказать не больше 20 пицц каждого вида.', 'error')
    else:
        data[key] = data.get(key, 0) + 1
        session['cart'] = data
        session.pop('checkout_token', None)
        flash(f'{PIZZAS[pizza_id]["name"]} добавлена в корзину.', 'success')
    return redirect(url_for('menu'))


@bp.route('/cart', methods=['GET', 'POST'])
def cart():
    if request.method == 'POST':
        updated = {}
        try:
            for p in cart_items():
                qty = int(request.form.get(f'quantity_{p["id"]}', p['quantity']))
                if not 0 <= qty <= 20:
                    raise ValueError
                if qty:
                    updated[str(p['id'])] = qty
        except (ValueError, TypeError):
            flash('Количество должно быть целым числом от 0 до 20.', 'error')
            return redirect(url_for('shop.cart'))
        session['cart'] = updated
        session.pop('checkout_token', None)
        flash('Корзина обновлена.', 'success')
        return redirect(url_for('shop.cart'))
    items = cart_items()
    return render_template('cart.html', items=items, subtotal=sum(p['price'] * p['quantity'] for p in items))


@bp.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    db = get_db()
    if request.method == 'POST':
        token = request.form.get('checkout_token', '')
        existing = db.execute('SELECT id FROM orders WHERE user_id=? AND checkout_token=?', (g.user['id'], token)).fetchone()
        if existing:
            return redirect(url_for('orders.detail', order_id=existing['id']))
        if not token or not secrets.compare_digest(token.encode(), session.get('checkout_token', '').encode()):
            abort(400, 'Корзина изменилась. Откройте оформление заказа заново.')
    items = cart_items()
    if not items:
        flash('Сначала добавьте пиццу в корзину.', 'info')
        return redirect(url_for('menu'))
    if 'checkout_token' not in session:
        session['checkout_token'] = secrets.token_hex(24)
    mode = request.form.get('fulfillment', 'delivery')
    values = {k: request.form.get(k, '').strip() for k in ('name', 'phone', 'address', 'comment')}
    if request.method == 'GET':
        values['name'] = g.user['name']
    quotes = {kind: totals(items, kind) for kind in ('delivery', 'pickup')}
    if request.method == 'POST':
        error = None
        if mode not in quotes:
            error = 'Выберите доставку или самовывоз.'
        elif not 2 <= len(values['name']) <= 60 or any(ord(c) < 32 for c in values['name']):
            error = 'Имя получателя: от 2 до 60 символов.'
        elif not re.fullmatch(r'\+?[0-9() -]{10,25}', values['phone']) or not 10 <= len(re.sub(r'\D', '', values['phone'])) <= 15:
            error = 'Укажите телефон: от 10 до 15 цифр, можно использовать +, пробелы, скобки и дефис.'
        elif mode == 'delivery' and (not 10 <= len(values['address']) <= 250 or any(ord(c) < 32 for c in values['address'])):
            error = 'Для доставки укажите адрес длиной от 10 до 250 символов.'
        elif len(values['comment']) > 500:
            error = 'Комментарий должен быть не длиннее 500 символов.'
        elif mode == 'delivery' and quotes[mode]['subtotal'] - quotes[mode]['discount'] < 50000:
            error = 'Минимальная сумма пицц для доставки — 500 ₽ после скидки. Добавьте пиццу или выберите самовывоз.'
        if error:
            flash(error, 'error')
            return render_template('checkout.html', items=items, quotes=quotes, values=values, mode=mode), 400
        quote = quotes[mode]
        try:
            with db:
                result = db.execute('''INSERT INTO orders(user_id,fulfillment,name,phone,address,comment,
                    subtotal,discount,delivery_fee,total,checkout_token) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    (g.user['id'], mode, values['name'], values['phone'], values['address'] if mode == 'delivery' else PIZZERIA_ADDRESS,
                     values['comment'], quote['subtotal'], quote['discount'], quote['delivery_fee'], quote['total'], session['checkout_token']))
                order_id = result.lastrowid
                db.executemany('INSERT INTO order_items(order_id,pizza_id,name,unit_price,quantity) VALUES(?,?,?,?,?)',
                               [(order_id, p['id'], p['name'], p['price'], p['quantity']) for p in items])
                db.execute('INSERT INTO order_events(order_id,status,actor_id) VALUES(?,?,?)', (order_id, 'accepted', g.user['id']))
        except sqlite3.IntegrityError:
            existing = db.execute('SELECT id FROM orders WHERE user_id=? AND checkout_token=?', (g.user['id'], token)).fetchone()
            if not existing:
                raise
            order_id = existing['id']
        session['cart'] = {}
        session.pop('checkout_token', None)
        flash('Учебный заказ принят! Оплата не списывается.', 'success')
        return redirect(url_for('orders.detail', order_id=order_id))
    return render_template('checkout.html', items=items, quotes=quotes, values=values, mode=mode)
```

## start_windows.bat

```bat
@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 (
    python -m venv .venv
) else (
    py -m venv .venv
)
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Open http://127.0.0.1:5000/ in your browser.
echo Admin login: admin / admin. Local classroom demo only.
echo.
".venv\Scripts\python.exe" app.py
pause
exit /b
:failed
echo Setup failed. Check Python 3.10+ and your internet connection.
pause
exit /b 1
```

## static/css/style.css

```css
:root {
    --paper: #fffaf2;
    --ink: #28251f;
    --muted: #625d53;
    --accent: #9f351c;
    --line: #e5dccd;
    --cream: #f4e7cd;
}
button, input, select, textarea { font: inherit; }
button { cursor: pointer; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
.account-bar { background: var(--cream); border-bottom: 1px solid var(--line); }
.account-inner { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; padding-block: 12px; font-size: 15px; }
.account-name { margin-left: auto; font-weight: bold; overflow-wrap: anywhere; }
.link-button { border: 0; background: transparent; padding: 0; color: var(--accent); text-decoration: underline; }
.form-card { max-width: 600px; display: flex; flex-direction: column; gap: 12px; }
.form-card label { font-weight: bold; }
input, select, textarea { max-width: 100%; width: 100%; border: 1px solid #a69a86; border-radius: 6px; background: #fff; color: var(--ink); padding: 10px 12px; }
textarea { resize: vertical; }
.form-card .button { margin-top: 10px; }
.hint { color: var(--muted); font-size: 14px; }
.flash { margin-top: 20px; padding: 14px 20px; border-radius: 8px; border: 1px solid var(--line); background: var(--cream); }
.flash.success { background: #e5f1df; border-color: #9dbc8c; }
.flash.error { background: #ffe5dd; border-color: #c8765e; }
.form-error { color: #922c19; font-weight: bold; }
.checkout-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); align-items: start; gap: 28px; }
.checkout-grid > *, .order-card > * { min-width: 0; }
.totals { display: grid; grid-template-columns: 1fr auto; gap: 8px 20px; border-top: 1px solid var(--line); padding-top: 18px; }
.totals dd { margin: 0; text-align: right; }
.quantity { min-width: 75px; max-width: 90px; }
.empty { padding: 40px; }
.order-list { display: grid; gap: 18px; }
.order-card { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.badge { display: inline-block; padding: 5px 14px; border-radius: 24px; font-weight: bold; font-size: 15px; background: var(--cream); }
.badge.delivered { background: #e0efdb; color: #285224; }
.badge.cancelled { background: #f7d9d2; color: #812b21; }
.badge.on_way, .badge.ready { background: #ddeaf7; color: #294e6e; }
.timeline { padding-left: 24px; }
.timeline li { padding: 0 0 20px 8px; }
.timeline li:last-child { padding-bottom: 0; }
.filters { display: flex; gap: 10px; flex-wrap: wrap; }
.chip { border: 1px solid var(--line); border-radius: 25px; padding: 7px 14px; font-size: 14px; text-decoration: none; }
.chip.selected { background: var(--accent); color: white; }
.pizza-card { display: flex; flex-direction: column; }
.pizza-card form { margin-top: auto; }
.pizza-mark { font-size: 44px; display: block; margin-bottom: 10px; }
.location-map { border: 1px solid var(--line); border-radius: 12px; }
@media(max-width: 700px) {
    .checkout-grid { grid-template-columns: 1fr; }
    .order-card { flex-direction: column; align-items: flex-start; }
    .account-name { margin-left: 0; }
    .form-card { max-width: 100%; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); color: var(--ink); font-family: Arial, sans-serif; font-size: 17px; line-height: 1.65; }
a { color: var(--accent); text-underline-offset: 4px; }
a:focus-visible { outline: 3px solid var(--accent); outline-offset: 5px; }
.container { width: min(1100px, 92%); margin-inline: auto; }
.site-header { border-bottom: 1px solid var(--line); background: #fffdf8; }
.header-inner { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding-block: 22px; }
.brand { color: var(--ink); font-size: 25px; font-weight: bold; line-height: 1.2; text-decoration: none; white-space: nowrap; }
.brand span { display: block; font-size: 12px; letter-spacing: 3px; color: var(--muted); margin-top: 5px; }
nav { display: flex; flex-wrap: wrap; gap: 10px 22px; }
nav a { color: var(--ink); font-size: 15px; text-decoration: none; padding-block: 4px; }
nav a:hover, nav a[aria-current="page"] { color: var(--accent); text-decoration: underline; }
main { min-height: 65vh; padding-bottom: 56px; }
h1, h2, h3 { line-height: 1.2; }
h1 { font-family: Georgia, serif; font-size: clamp(36px, 5vw, 62px); margin: 12px 0 22px; letter-spacing: -1px; }
h2 { font-size: 25px; margin-top: 0; }
h3 { font-size: 21px; margin-top: 0; }
p { margin: 0 0 16px; }
.hero { padding: 64px 0 38px; max-width: 830px; }
.page-heading { padding: 48px 0 28px; max-width: 850px; }
.eyebrow { color: var(--accent); font-size: 13px; text-transform: uppercase; letter-spacing: 2px; font-weight: bold; }
.lead { color: var(--muted); font-size: 20px; max-width: 760px; }
.actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
.button { display: inline-block; background: var(--accent); color: white; padding: 11px 22px; border: 1px solid var(--accent); border-radius: 6px; font-weight: bold; text-decoration: none; }
.button-secondary { background: transparent; color: var(--accent); }
.button:hover { filter: brightness(.9); }
.section { margin-top: 32px; }
.grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.grid-two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.card { border: 1px solid var(--line); border-radius: 10px; background: #fffdf8; padding: 25px; overflow-wrap: anywhere; }
.card p:last-child { margin-bottom: 0; }
.tag { color: var(--accent); font-size: 12px; letter-spacing: 1px; text-transform: uppercase; font-weight: bold; }
.price { font-size: 25px; font-weight: bold; }
.price span { font-size: 15px; font-weight: normal; color: var(--muted); }
.callout { background: var(--cream); border-radius: 10px; padding: 28px; }
.text-link { font-weight: bold; }
.table-wrap { overflow-x: auto; margin-bottom: 30px; }
table { width: 100%; border-collapse: collapse; background: #fffdf8; }
caption { text-align: left; color: var(--muted); padding-bottom: 10px; }
th, td { border: 1px solid var(--line); padding: 14px 18px; text-align: left; }
thead { background: var(--cream); }
tbody th { font-weight: normal; width: 45%; }
address { font-style: normal; }
.site-footer { border-top: 1px solid var(--line); color: var(--muted); font-size: 14px; padding: 28px 0; }
.site-footer p { margin-bottom: 6px; }
.skip-link { position: absolute; top: -100px; left: 10px; background: white; padding: 10px; z-index: 10; }
.skip-link:focus { top: 10px; }
@media (max-width: 800px) {
    .header-inner { align-items: flex-start; flex-direction: column; gap: 18px; }
    .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 540px) {
    .grid, .grid-two { grid-template-columns: 1fr; }
    .hero { padding-top: 36px; }
    .lead { font-size: 18px; }
    .card, .callout { padding: 21px; }
    th, td { padding: 10px; font-size: 15px; }
}
```

## templates/admin.html

```html
{% extends "base.html" %}{% block title %}Админ-панель{% endblock %}
{% block content %}<section class="page-heading"><p class="eyebrow">Управление пиццерией</p><h1>Все заказы</h1><p class="lead">От принятия заказа до вручения пиццы.</p></section>
<div class="filters"><a class="chip" href="{{ url_for('admin.dashboard') }}">Все · {{ counts.values()|sum }}</a>{% for key,label in statuses.items() %}<a class="chip {% if selected==key %}selected{% endif %}" href="{{ url_for('admin.dashboard',status=key) }}">{{ label }} · {{ counts.get(key,0) }}</a>{% endfor %}</div>
{% if orders %}<div class="table-wrap section"><table><thead><tr><th>Заказ / UTC</th><th>Покупатель</th><th>Получение</th><th>Сумма</th><th>Статус</th><th>Действие</th></tr></thead><tbody>{% for order in orders %}<tr><td>№ {{ order.id }}<br><small>{{ order.created_at }}</small></td><td>{{ order.name }}<br><small>{{ order.email }}</small></td><td>{{ 'Доставка' if order.fulfillment=='delivery' else 'Самовывоз' }}</td><td>{{ order.total|money }}</td><td>{{ status_label(order.status,order.fulfillment) }}</td><td><a href="{{ url_for('orders.detail',order_id=order.id) }}">Открыть / изменить</a></td></tr>{% endfor %}</tbody></table></div>
{% else %}<section class="card section empty"><h2>Заказов пока нет</h2><p>Здесь появятся заказы покупателей, соответствующие выбранному фильтру.</p></section>{% endif %}{% endblock %}
```

## templates/base.html

```html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Пять сыров{% endblock %} | Пиццерия</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    {% block head %}{% endblock %}
</head>
<body>
    <a class="skip-link" href="#content">Перейти к содержанию</a>
    <header class="site-header">
        <div class="container header-inner">
            <a class="brand" href="/">Пять сыров<span>пиццерия</span></a>
            <nav aria-label="Основная навигация">
                <a href="/" {% if request.path == '/' %}aria-current="page"{% endif %}>Главная</a>
                <a href="/menu" {% if request.path == '/menu' %}aria-current="page"{% endif %}>Меню</a>
                <a href="/promotions" {% if request.path == '/promotions' %}aria-current="page"{% endif %}>Акции</a>
                <a href="/delivery" {% if request.path == '/delivery' %}aria-current="page"{% endif %}>Доставка и оплата</a>
                <a href="/contacts" {% if request.path == '/contacts' %}aria-current="page"{% endif %}>Контакты</a>
            </nav>
        </div>
    </header>
    <div class="account-bar"><div class="container account-inner">
        <a href="{{ url_for('shop.cart') }}">Корзина · {{ cart_count }}</a>
        {% if g.user %}
        <a href="{{ url_for('orders.mine') }}">Мои заказы</a>
        {% if g.user.is_admin %}<a href="{{ url_for('admin.dashboard') }}">Админ-панель</a>{% endif %}
        <span class="account-name">{{ g.user.name }}</span>
        <form method="post" action="{{ url_for('auth.logout') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button class="link-button">Выйти</button></form>
        {% else %}<a href="{{ url_for('auth.login') }}">Войти</a><a href="{{ url_for('auth.register') }}">Регистрация</a>{% endif %}
    </div></div>
    <main id="content" class="container">
        {% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}" role="status">{{ message }}</div>{% endfor %}
        {% block content %}{% endblock %}
    </main>
    <footer class="site-footer">
        <div class="container">
            <p><strong>Пять сыров.</strong> Учебная пиццерия на Flask.</p>
            <p>Пиццерия вымышленная, точка на карте учебная. Заказы демонстрационные: без реальной оплаты и доставки.</p>
        </div>
    </footer>
</body>
</html>
```

## templates/cart.html

```html
{% extends "base.html" %}{% block title %}Корзина{% endblock %}
{% block content %}<section class="page-heading"><p class="eyebrow">Собираем компанию</p><h1>Ваша корзина</h1></section>
{% if items %}<form method="post"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<div class="table-wrap"><table><thead><tr><th>Пицца</th><th>Цена</th><th>Количество</th><th>Сумма</th></tr></thead><tbody>
{% for p in items %}<tr><th scope="row">{{ p.name }} · 30 см</th><td>{{ p.price|money }}</td><td><input class="quantity" type="number" min="0" max="20" name="quantity_{{ p.id }}" value="{{ p.quantity }}" aria-label="Количество: {{ p.name }}" required></td><td>{{ (p.price * p.quantity)|money }}</td></tr>{% endfor %}
</tbody></table></div><p class="hint">Чтобы удалить пиццу, укажите 0 и нажмите «Обновить корзину».</p><button class="button button-secondary">Обновить корзину</button></form>
<section class="section callout"><h2>Пиццы: {{ subtotal|money }}</h2><p>Скидка и стоимость доставки рассчитываются на следующем шаге. После изменения количества сначала обновите корзину.</p><a class="button" href="{{ url_for('shop.checkout') }}">Перейти к оформлению</a> <a href="/menu">Добавить ещё пиццу</a></section>
{% else %}<section class="card empty"><h2>Здесь пока пусто</h2><p>Выберите первую пиццу — остальное дело вкуса.</p><a class="button" href="/menu">Открыть меню</a></section>{% endif %}{% endblock %}
```

## templates/checkout.html

```html
{% extends "base.html" %}{% block title %}Оформление заказа{% endblock %}
{% block content %}<section class="page-heading"><p class="eyebrow">Почти готово</p><h1>Оформить заказ</h1><p class="lead">Учебный заказ: деньги не списываются, настоящая доставка не выполняется.</p></section>
<div class="checkout-grid"><form method="post" class="card form-card">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><input type="hidden" name="checkout_token" value="{{ session['checkout_token'] }}">
<label for="fulfillment">Способ получения</label><select id="fulfillment" name="fulfillment"><option value="delivery" {% if mode=='delivery' %}selected{% endif %}>Доставка</option><option value="pickup" {% if mode=='pickup' %}selected{% endif %}>Самовывоз −10%</option></select>
<label for="name">Имя получателя</label><input id="name" name="name" value="{{ values.name }}" minlength="2" maxlength="60" autocomplete="name" required>
<label for="phone">Телефон</label><input id="phone" name="phone" type="tel" value="{{ values.phone }}" maxlength="25" autocomplete="tel" placeholder="+7 900 123-45-67" required>
<label for="address">Адрес доставки</label><input id="address" name="address" value="{{ values.address }}" maxlength="250" autocomplete="street-address" placeholder="Улица, дом, квартира"><p class="hint">От 10 символов. Для самовывоза адрес заполнять не нужно.</p>
<label for="comment">Комментарий (необязательно)</label><textarea id="comment" name="comment" maxlength="500" rows="3">{{ values.comment }}</textarea>
<p>Оплата при получении — условная, без онлайн-платежа.</p><button class="button">Подтвердить учебный заказ</button></form>
<aside><section class="card"><h2>В заказе</h2>{% for p in items %}<p>{{ p.name }} × {{ p.quantity }} <strong>{{ (p.price*p.quantity)|money }}</strong></p>{% endfor %}</section>
{% for kind, quote in quotes.items() %}<section class="card section quote" data-kind="{{ kind }}"><h2>{{ 'Доставка' if kind=='delivery' else 'Самовывоз' }}</h2>
<dl class="totals"><dt>Пиццы</dt><dd>{{ quote.subtotal|money }}</dd><dt>Скидка</dt><dd>−{{ quote.discount|money }}</dd><dt>Доставка</dt><dd>{{ quote.delivery_fee|money }}</dd><dt><strong>Итого</strong></dt><dd><strong>{{ quote.total|money }}</strong></dd></dl>
{% if kind=='delivery' and quote.subtotal-quote.discount < 50000 %}<p class="form-error">Для доставки нужно от 500 ₽ после скидки. Сейчас доступен самовывоз.</p>{% endif %}</section>{% endfor %}
<p class="hint section">Показаны оба варианта расчёта. Заказ будет оформлен с выбранным слева способом получения.</p></aside></div>{% endblock %}
```

## templates/contacts.html

```html
{% extends "base.html" %}
{% block title %}Контакты{% endblock %}
{% block content %}
<section class="page-heading">
    <p class="eyebrow">Будем рады встрече</p>
    <h1>Контакты</h1>
    <p class="lead">Ниже указаны демонстрационные сведения вымышленной пиццерии «Пять сыров».</p>
</section>
<section class="grid grid-two">
    <article class="card">
        <h2>Адрес и связь</h2>
        <address>
            <p>{{ pizzeria_address }}</p>
            <p>Телефон: +7 (000) 000-00-00 — пример, не для звонков</p>
            <p>Почта: info@example.com — демонстрационный адрес</p>
        </address>
    </article>
    <article class="card">
        <h2>Время работы</h2>
        <p>Зал и самовывоз: ежедневно с 10:00 до 22:00.</p>
        <p>Приём заказов на доставку: ежедневно с 10:00 до 21:00.</p>
        <a class="text-link" href="/delivery">Условия доставки →</a>
    </article>
</section>
<section class="section callout">
    <h2>Как добраться</h2>
    <p>Для учебного проекта выбрана точка по адресу: {{ pizzeria_address }}. Откройте карту ниже, чтобы посмотреть расположение.</p>
    <p>Адрес используется для демонстрации. Пиццерия «Пять сыров» вымышленная, реального обслуживания по этому адресу проект не предлагает.</p>
</section>
<section class="section" aria-labelledby="map-title">
    <h2 id="map-title">Пять сыров — условная точка</h2>
    <p>Google Maps · {{ pizzeria_address }}.</p>
    <iframe class="location-map" title="Google Maps: Будапештская улица, 38, Санкт-Петербург" src="{{ map_embed_url }}" width="100%" height="380" loading="lazy" referrerpolicy="no-referrer-when-downgrade" allowfullscreen></iframe>
    <p class="hint">Для карты нужен интернет. Если она не загрузилась, <a href="{{ map_link }}" target="_blank" rel="noopener noreferrer">открыть точку в Google Maps</a>.</p>
</section>
{% endblock %}
```

## templates/delivery.html

```html
{% extends "base.html" %}
{% block title %}Доставка и оплата{% endblock %}
{% block content %}
<section class="page-heading">
    <p class="eyebrow">Пицца там, где вы</p>
    <h1>Доставка и оплата</h1>
    <p class="lead">Учебная доставка по Санкт-Петербургу в условной зоне до 5 км от пиццерии. Также доступен самовывоз. Проверка реального расстояния не выполняется.</p>
</section>
<section class="section">
    <h2>Условия доставки</h2>
    <div class="table-wrap">
        <table>
            <caption>Стоимость и время доставки</caption>
            <thead><tr><th scope="col">Условие</th><th scope="col">Значение</th></tr></thead>
            <tbody>
                <tr><th scope="row">Приём заказов на доставку</th><td>Ежедневно с 10:00 до 21:00</td></tr>
                <tr><th scope="row">Время ожидания</th><td>Ориентировочно 40–60 минут</td></tr>
                <tr><th scope="row">Минимальная сумма</th><td>От 500 ₽ после скидок</td></tr>
                <tr><th scope="row">Стоимость доставки</th><td>150 ₽ при сумме от 500 ₽ до 999,99 ₽</td></tr>
                <tr><th scope="row">Бесплатная доставка</th><td>От 1 000 ₽ после скидок</td></tr>
            </tbody>
        </table>
    </div>
</section>
<section class="grid grid-two">
    <article class="card">
        <h2>Способы оплаты</h2>
        <ul><li>Наличными при получении.</li><li>Банковской картой при получении.</li></ul>
        <p>Онлайн-оплата на учебном сайте не реализована.</p>
    </article>
    <article class="card">
        <h2>Самовывоз</h2>
        <p>Учебный самовывоз: ежедневно с 10:00 до 22:00. Адрес демонстрационной точки: {{ pizzeria_address }}.</p>
        <p>Минимальной суммы нет. На пиццы действует скидка 10%.</p>
        <a class="text-link" href="/contacts">Как нас найти →</a>
    </article>
</section>
{% endblock %}
```

## templates/error.html

```html
{% extends "base.html" %}{% block title %}Ошибка {{ error.code }}{% endblock %}{% block content %}<section class="page-heading"><p class="eyebrow">Ошибка {{ error.code }}</p><h1>Не удалось выполнить действие</h1><p class="lead">{{ error.description }}</p><a class="button" href="/">На главную</a> <a href="/orders">Мои заказы</a></section>{% endblock %}
```

## templates/index.html

```html
{% extends "base.html" %}
{% block title %}Главная{% endblock %}
{% block content %}
<section class="hero">
    <p class="eyebrow">Пицца для хорошей компании</p>
    <h1>Пять сыров.<br>Один повод собраться.</h1>
    <p class="lead">Готовим пиццу на тонком тесте: от классической Маргариты до фирменной пиццы с пятью видами сыра. Выбирайте любимый вкус и знакомьтесь с нашим меню.</p>
    <div class="actions">
        <a class="button" href="/menu">Посмотреть меню</a>
        <a class="button button-secondary" href="/delivery">Условия доставки</a>
    </div>
</section>
<section class="section">
    <h2>О нашей пиццерии</h2>
    <p>«Пять сыров» — небольшая пиццерия для встреч с друзьями и семейных вечеров. Мы собрали в меню шесть рецептов: мясные, сырные и овощные варианты.</p>
    <div class="grid">
        <article class="card"><h3>Тонкое тесто</h3><p>Раскатываем основу перед приготовлением, чтобы сохранить хрустящие края.</p></article>
        <article class="card"><h3>Понятный состав</h3><p>Для каждой пиццы в меню указаны ингредиенты, размер и цена.</p></article>
        <article class="card"><h3>Удобный самовывоз</h3><p>Можно забрать заказ в пиццерии. На самовывоз действует скидка 10%.</p></article>
    </div>
</section>
<section class="section callout">
    <h2>С чего начать?</h2>
    <p>Попробуйте Маргариту за 490 ₽ или фирменную пиццу «Пять сыров» за 690 ₽. Все пиццы в меню — диаметром 30 см.</p>
    <a class="text-link" href="/promotions">Посмотреть действующие акции →</a>
</section>
{% endblock %}
```

## templates/login.html

```html
{% extends "base.html" %}{% block title %}Вход{% endblock %}
{% block content %}<section class="page-heading"><p class="eyebrow">С возвращением</p><h1>Войти</h1><p class="lead">Ваша любимая пицца уже ждёт в меню.</p></section>
<form method="post" class="card form-card"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<label for="email">Почта или логин администратора</label><input id="email" name="email" autocomplete="username" maxlength="254" required value="{{ request.form.get('email','') }}">
<label for="password">Пароль</label><input id="password" name="password" type="password" autocomplete="current-password" maxlength="128" required>
<button class="button">Войти</button><p>Первый заказ? <a href="{{ url_for('auth.register') }}">Создать аккаунт</a></p></form>{% endblock %}
```

## templates/menu.html

```html
{% extends "base.html" %}
{% block title %}Меню{% endblock %}
{% block content %}
<section class="page-heading"><p class="eyebrow">Выбирайте свой вкус</p><h1>Меню пицц</h1><p class="lead">Шесть рецептов, тонкое тесто и щедрая начинка. Все пиццы — 30 см.</p><a href="{{ url_for('shop.cart') }}">Перейти в корзину · {{ cart_count }}</a></section>
<section class="grid" aria-label="Пиццы">
{% for id, pizza in pizzas.items() %}
<article class="card pizza-card"><span class="pizza-mark" aria-hidden="true">🍕</span><p class="tag">{{ pizza.tag }}</p><h2>{{ pizza.name }}</h2><p>{{ pizza.ingredients }}</p><p class="price">{{ pizza.price|money }} <span>· 30 см</span></p>
<form method="post" action="{{ url_for('shop.add', pizza_id=id) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button class="button" type="submit">Добавить в корзину</button></form></article>
{% endfor %}
</section>
<section class="section callout"><h2>Хорошая компания — выгоднее</h2><p>На три и более пиццы — скидка 10%. На самовывоз — тоже 10%. Скидки не суммируются и рассчитываются автоматически при оформлении.</p><p>Пиццы содержат пшеницу и молочные продукты.</p><a href="/delivery">Условия доставки →</a></section>
{% endblock %}
```

## templates/order_detail.html

```html
{% extends "base.html" %}{% block title %}Заказ № {{ order.id }}{% endblock %}
{% block head %}{% if not g.user.is_admin and order.status not in ['delivered','cancelled'] %}<meta http-equiv="refresh" content="15">{% endif %}{% endblock %}
{% block content %}<section class="page-heading"><a href="{{ url_for('admin.dashboard') if g.user.is_admin else url_for('orders.mine') }}">← {{ 'Все заказы' if g.user.is_admin else 'Мои заказы' }}</a><h1>Заказ № {{ order.id }}</h1><span class="badge {{ order.status }}">{{ status_label(order.status,order.fulfillment) }}</span><p class="hint section">Учебный заказ · {{ order.created_at }} UTC. Все отметки времени — UTC.</p>
{% if not g.user.is_admin and order.status not in ['delivered','cancelled'] %}<p>Страница обновляется каждые 15 секунд. <a href="{{ url_for('orders.detail',order_id=order.id) }}">Обновить сейчас</a></p>{% endif %}</section>
<div class="checkout-grid"><div><section class="card"><h2>Состав заказа</h2>{% for item in items %}<p>{{ item.name }} × {{ item.quantity }} — {{ (item.unit_price*item.quantity)|money }}</p>{% endfor %}
<dl class="totals"><dt>Пиццы</dt><dd>{{ order.subtotal|money }}</dd><dt>Скидка</dt><dd>−{{ order.discount|money }}</dd><dt>Доставка</dt><dd>{{ order.delivery_fee|money }}</dd><dt><strong>Итого</strong></dt><dd><strong>{{ order.total|money }}</strong></dd></dl></section>
<section class="card section"><h2>{{ 'Доставка' if order.fulfillment=='delivery' else 'Самовывоз' }}</h2><p>{{ order.name }} · {{ order.phone }}</p><p>{{ order.address }}</p>{% if order.comment %}<p>Комментарий: {{ order.comment }}</p>{% endif %}<p class="hint">Без реальной оплаты и доставки.</p></section></div>
<aside><section class="card"><h2>История заказа</h2><ol class="timeline">{% for event in events %}<li><strong>{{ status_label(event.status,order.fulfillment) }}</strong><br><span class="hint">{{ event.created_at }} UTC</span></li>{% endfor %}</ol></section>
{% if g.user.is_admin and next_statuses(order) %}<section class="card section"><h2>Изменить статус</h2><form method="post" action="{{ url_for('admin.change_status',order_id=order.id) }}" class="form-card">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><input type="hidden" name="previous_status" value="{{ order.status }}">
<label for="status">Следующий статус</label><select id="status" name="status">{% for s in next_statuses(order) %}<option value="{{ s }}">{{ status_label(s,order.fulfillment) }}</option>{% endfor %}</select><button class="button">Сохранить статус</button></form></section>{% endif %}</aside></div>{% endblock %}
```

## templates/orders.html

```html
{% extends "base.html" %}{% block title %}Мои заказы{% endblock %}
{% block content %}<section class="page-heading"><p class="eyebrow">{{ g.user.name }}</p><h1>Мои заказы</h1><p class="lead">Состав, история и статус ваших заказов в одном месте.</p></section>
{% if orders %}<div class="order-list">{% for order in orders %}<article class="card order-card"><div><span class="tag">Заказ № {{ order.id }}</span><h2>{{ order.total|money }}</h2><p>{{ order.created_at }} UTC · {{ 'Доставка' if order.fulfillment=='delivery' else 'Самовывоз' }}</p></div><div><p class="badge {{ order.status }}">{{ status_label(order.status,order.fulfillment) }}</p><a class="button button-secondary" href="{{ url_for('orders.detail',order_id=order.id) }}">Отследить заказ →</a></div></article>{% endfor %}</div>
{% else %}<section class="card empty"><h2>Первый заказ ещё впереди</h2><p>Добавьте пиццу в корзину, и мы сохраним её здесь после оформления.</p><a class="button" href="/menu">Выбрать пиццу</a></section>{% endif %}{% endblock %}
```

## templates/promotions.html

```html
{% extends "base.html" %}
{% block title %}Акции{% endblock %}
{% block content %}
<section class="page-heading">
    <p class="eyebrow">Больше поводов встретиться</p>
    <h1>Акции и предложения</h1>
    <p class="lead">Выберите удобный вариант. На один заказ можно применить только одну акцию.</p>
</section>
<section class="grid" aria-label="Предложения">
    <article class="card">
        <p class="tag">Каждый день</p><h2>Самовывоз −10%</h2>
        <p>Скидка 10% на пиццы при получении заказа в пиццерии.</p>
        <p>Действует ежедневно с 10:00 до 22:00, без минимальной суммы. Не распространяется на доставку.</p>
    </article>
    <article class="card">
        <p class="tag">По будням</p><h2>Обеденная скидка</h2>
        <p>Скидка 15% на Маргариту с понедельника по пятницу с 12:00 до 15:00.</p>
        <p>Цена по акции — 416,50 ₽ вместо 490 ₽. Действует при заказе в зале.</p>
    </article>
    <article class="card">
        <p class="tag">Для компании</p><h2>Три пиццы выгоднее</h2>
        <p>Скидка 10% на пиццы при покупке трёх и более пицц в одном заказе.</p>
        <p>Действует ежедневно в зале и на доставку. Стоимость доставки рассчитывается отдельно.</p>
    </article>
</section>
<section class="section callout">
    <h2>Общие условия</h2>
    <p>Предложения не суммируются. Порог бесплатной доставки рассчитывается по стоимости пицц после скидки. Здесь приведены вымышленные условия учебного сайта.</p>
    <a class="text-link" href="/menu">Выбрать пиццу в меню →</a>
</section>
{% endblock %}
```

## templates/register.html

```html
{% extends "base.html" %}{% block title %}Регистрация{% endblock %}
{% block content %}<section class="page-heading"><p class="eyebrow">Своя компания</p><h1>Создать аккаунт</h1><p class="lead">Сохраняйте заказы и следите за приготовлением.</p></section>
<form method="post" class="card form-card">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<label for="name">Имя</label><input id="name" name="name" autocomplete="name" minlength="2" maxlength="60" required value="{{ request.form.get('name','') }}">
<label for="email">Почта</label><input id="email" name="email" type="email" autocomplete="email" maxlength="254" required value="{{ request.form.get('email','') }}" aria-describedby="email-help">
<p class="hint" id="email-help">Латиница, без пробелов, до 254 символов. Проверяем формат адреса; письмо с подтверждением не отправляется.</p>
<label for="password">Пароль</label><input id="password" name="password" type="password" minlength="8" maxlength="128" autocomplete="new-password" required aria-describedby="password-help"><p class="hint" id="password-help">От 8 до 128 символов. Не используйте пароль от настоящей почты.</p>
<label for="confirm">Повторите пароль</label><input id="confirm" name="confirm_password" type="password" minlength="8" maxlength="128" autocomplete="new-password" required>
<button class="button">Зарегистрироваться</button><p>Уже есть аккаунт? <a href="{{ url_for('auth.login') }}">Войти</a></p></form>{% endblock %}
```

## tests/test_app.py

```python
import tempfile
import unittest
from pathlib import Path
from html.parser import HTMLParser

from app import create_app
from database import get_db
from auth import normalize_email
from location import PIZZERIA_ADDRESS


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(value for key, value in attrs if key == 'href' and value.startswith('/'))


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = dict(TESTING=True, SECRET_KEY='test-only', DATABASE=str(Path(self.tmp.name) / 'test.sqlite3'), ADMIN_PASSWORD='admin')
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, path, data=None, client=None):
        client = client or self.client
        client.get('/menu')
        with client.session_transaction() as state:
            token = state['_csrf']
        return client.post(path, data={'csrf_token': token, **(data or {})})

    def register(self, email='alice@example.com', client=None):
        return self.post('/register', dict(name='Алиса', email=email, password='password123', confirm_password='password123'), client)

    def login(self, email='alice@example.com', password='password123', client=None):
        return self.post('/login', dict(email=email, password=password), client)

    def order(self, mode='delivery', pizza=2, qty=1):
        self.register()
        self.login()
        for _ in range(qty):
            self.post(f'/cart/add/{pizza}')
        self.client.get('/checkout')
        with self.client.session_transaction() as state:
            token = state['checkout_token']
        data = dict(checkout_token=token, fulfillment=mode, name='Алиса', phone='+7 900 123-45-67', address='Улица Примерная, 5', comment='Учебный заказ', total='1')
        response = self.post('/checkout', data)
        return response, data

    def rows(self, sql):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql).fetchall()]

    def test_public_pages_and_navigation(self):
        paths = ['/', '/menu', '/promotions', '/delivery', '/contacts']
        for path in paths:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200)
            parser = LinkParser()
            parser.feed(r.get_data(as_text=True))
            self.assertTrue(set(paths).issubset(parser.links))
        with self.client.get('/static/css/style.css') as response:
            self.assertEqual(response.status_code, 200)

    def test_registration_hash_duplicate_and_role(self):
        self.assertEqual(self.register('ALICE@example.com').status_code, 302)
        row = self.rows("SELECT * FROM users WHERE email='alice@example.com'")[0]
        self.assertNotEqual(row['password_hash'], 'password123')
        self.assertEqual(row['is_admin'], 0)
        self.assertEqual(self.register().status_code, 400)
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get('/orders').status_code, 200)
        self.assertEqual(self.client.get('/admin').status_code, 403)

    def test_email_policy(self):
        invalid = ['admin', 'a b@example.com', 'a..b@example.com', '.a@example.com', 'a.@example.com', 'a@-host.com', 'a@host-.com', 'a@host..com', 'a@host', 'a@host.c', 'я@example.com', 'a'*65+'@example.com', 'x@'+'a.'*125+'com', '<script>@example.com']
        for address in invalid:
            with self.subTest(address=address), self.assertRaises(ValueError):
                normalize_email(address)
        self.assertEqual(normalize_email('  Test+tag@Example.com '), 'test+tag@example.com')
        self.assertEqual(normalize_email('a'*64+'@example.com'), 'a'*64+'@example.com')

    def test_registration_rejects_bad_fields(self):
        valid = dict(name='User', email='test@example.com', password='password123', confirm_password='password123')
        for changes in ({'name':'x'}, {'name':'x'*61}, {'password':'123'}, {'password':'x'*129}, {'confirm_password':'different'}, {'email':'bad@address'}):
            self.assertEqual(self.post('/register', {**valid, **changes}).status_code, 400)
        self.assertEqual(len(self.rows('SELECT * FROM users')), 1)

    def test_csrf(self):
        self.assertEqual(self.client.post('/register', data={}).status_code, 400)
        self.assertEqual(self.client.post('/cart/add/1', data={'csrf_token':'я'}).status_code, 400)

    def test_guest_restrictions_and_unknown_pizza(self):
        for path in ['/orders', '/orders/1', '/checkout', '/admin']:
            self.assertEqual(self.client.get(path).status_code, 302)
        self.assertEqual(self.post('/cart/add/999').status_code, 404)

    def test_cart_limits_update_remove(self):
        self.post('/cart/add/1')
        self.post('/cart', {'quantity_1':20})
        self.post('/cart/add/1')
        with self.client.session_transaction() as s:
            self.assertEqual(s['cart']['1'], 20)
        self.post('/cart', {'quantity_1':-1})
        with self.client.session_transaction() as s:
            self.assertEqual(s['cart']['1'], 20)
        self.post('/cart', {'quantity_1':0})
        with self.client.session_transaction() as s:
            self.assertEqual(s['cart'], {})

    def test_delivery_price_snapshot_and_duplicate_submit(self):
        response, data = self.order()
        self.assertEqual(response.status_code, 302)
        order = self.rows('SELECT * FROM orders')[0]
        self.assertEqual(order['total'], 74000)
        self.assertEqual(order['delivery_fee'], 15000)
        self.assertEqual(self.rows('SELECT * FROM order_items')[0]['unit_price'], 59000)
        self.assertEqual(self.post('/checkout', data).location, response.location)
        self.assertEqual(len(self.rows('SELECT * FROM orders')), 1)

    def test_minimum_delivery_and_pickup(self):
        response, data = self.order(pizza=1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows('SELECT * FROM orders'), [])
        self.assertEqual(self.post('/checkout', {**data, 'fulfillment':'pickup', 'address':''}).status_code, 302)
        self.assertEqual(self.rows('SELECT * FROM orders')[0]['total'], 44100)

    def test_discount_and_free_delivery(self):
        response, _ = self.order(pizza=1, qty=3)
        self.assertEqual(response.status_code, 302)
        order = self.rows('SELECT * FROM orders')[0]
        self.assertEqual((order['discount'],order['delivery_fee'],order['total']), (14700,0,132300))

    def test_pickup_discount_not_stacked(self):
        self.order(mode='pickup', pizza=1, qty=3)
        self.assertEqual(self.rows('SELECT * FROM orders')[0]['total'], 132300)
        self.assertEqual(self.rows('SELECT * FROM orders')[0]['address'], PIZZERIA_ADDRESS)

    def test_map_and_pickup_address_match(self):
        for path in ['/contacts', '/delivery']:
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn(PIZZERIA_ADDRESS, html)
            self.assertNotIn('37.618423', html)
        self.assertIn('maps.google.com/maps?q=', self.client.get('/contacts').get_data(as_text=True))

    def test_order_ownership_and_admin_permissions(self):
        self.order()
        other = self.app.test_client()
        self.register('bob@example.com', other)
        self.login('bob@example.com', client=other)
        self.assertEqual(other.get('/orders/1').status_code, 404)
        self.assertNotIn('Заказ № 1', other.get('/orders').get_data(as_text=True))
        self.assertEqual(self.post('/admin/orders/1/status', dict(status='cooking', previous_status='accepted'), other).status_code, 403)

    def test_admin_full_lifecycle_history_and_stale_update(self):
        self.order()
        admin = self.app.test_client()
        self.assertEqual(self.login('admin','admin',admin).status_code, 302)
        self.assertEqual(admin.get('/admin').status_code, 200)
        self.assertIn('alice@example.com', admin.get('/admin').get_data(as_text=True))
        previous = 'accepted'
        self.assertEqual(self.post('/admin/orders/1/status', dict(status='delivered',previous_status=previous),admin).status_code, 400)
        for status in ['cooking','ready','on_way','delivered']:
            self.assertEqual(self.post('/admin/orders/1/status', dict(status=status,previous_status=previous),admin).status_code, 302)
            self.assertEqual(self.post('/admin/orders/1/status', dict(status=status,previous_status=previous),admin).status_code, 409)
            previous = status
        self.assertEqual(len(self.rows('SELECT * FROM order_events')), 5)
        self.assertIn('Доставлен',self.client.get('/orders/1').get_data(as_text=True))
        self.assertEqual(self.post('/admin/orders/1/status',dict(status='cancelled',previous_status='delivered'),admin).status_code,400)

    def test_pickup_no_courier(self):
        self.order(mode='pickup')
        admin = self.app.test_client()
        self.login('admin','admin',admin)
        previous='accepted'
        for status in ['cooking','ready','delivered']:
            self.assertEqual(self.post('/admin/orders/1/status',dict(status=status,previous_status=previous),admin).status_code,302)
            previous=status
        self.assertIn('Выдан', self.client.get('/orders/1').get_data(as_text=True))

    def test_cancellation_and_xss_escaping(self):
        self.order()
        with self.app.app_context():
            db=get_db()
            with db:
                db.execute('UPDATE orders SET comment=?', ('<script>alert(1)</script>',))
        page=self.client.get('/orders/1').get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;script&gt;',page)
        admin=self.app.test_client()
        self.login('admin','admin',admin)
        self.assertEqual(self.post('/admin/orders/1/status',dict(status='cancelled',previous_status='accepted'),admin).status_code,302)
        self.assertEqual(admin.get('/admin?status=bad').status_code,400)

    def test_bad_checkout_fields_and_empty_cart(self):
        _, data=self.order(pizza=1)
        for changes in ({'phone':'abc'}, {'address':'x'}, {'name':'x'}, {'comment':'x'*501}, {'fulfillment':'other'}, {'checkout_token':'я'}):
            self.assertEqual(self.post('/checkout',{**data,**changes}).status_code,400)
        self.assertEqual(self.rows('SELECT * FROM orders'),[])
        self.post('/cart',{'quantity_1':0})
        self.assertEqual(self.client.get('/checkout').status_code,302)

    def test_restart_preserves_orders_and_admin(self):
        self.order()
        second=create_app(self.config)
        client=second.test_client()
        self.assertEqual(self.login(client=client).status_code,302)
        self.assertEqual(client.get('/orders/1').status_code,200)
        self.assertEqual(len(self.rows('SELECT * FROM users WHERE is_admin=1')),1)

    def test_logout_clears_session_and_login_throttling(self):
        self.register()
        self.login()
        self.post('/cart/add/1')
        self.post('/logout')
        self.assertEqual(self.client.get('/orders').status_code,302)
        with self.client.session_transaction() as s:
            self.assertNotIn('user_id',s)
            self.assertNotIn('cart',s)
        for _ in range(5):
            self.assertEqual(self.login(password='wrong').status_code,400)
        self.assertEqual(self.login().status_code,429)


if __name__ == '__main__':
    unittest.main()
```
