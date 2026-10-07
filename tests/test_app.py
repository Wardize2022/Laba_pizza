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
