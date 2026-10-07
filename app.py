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
