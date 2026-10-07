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
