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
