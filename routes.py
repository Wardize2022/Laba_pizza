from flask import render_template


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
        return render_template("menu.html")
    # УЧАСТНИК 2: Меню — конец

    # УЧАСТНИК 3: Акции — начало
    @app.route("/promotions")
    def promotions():
        return render_template("promotions.html")
    # УЧАСТНИК 3: Акции — конец

    # УЧАСТНИК 4: Доставка и оплата — начало
    pass
    # УЧАСТНИК 4: Доставка и оплата — конец

    # УЧАСТНИК 5: Контакты — начало
    pass
    # УЧАСТНИК 5: Контакты — конец
