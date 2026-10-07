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
