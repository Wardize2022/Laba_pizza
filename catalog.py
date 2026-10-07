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
