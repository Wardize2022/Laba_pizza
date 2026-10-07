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
