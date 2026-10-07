from urllib.parse import quote

# Shared address for contacts, map and pickup orders.
PIZZERIA_ADDRESS = 'Санкт-Петербург, Будапештская улица, 38'
MAP_QUERY = quote(PIZZERIA_ADDRESS)
MAP_EMBED_URL = f'https://maps.google.com/maps?q={MAP_QUERY}&z=17&output=embed'
MAP_LINK = f'https://www.google.com/maps/search/?api=1&query={MAP_QUERY}'
