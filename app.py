"""ЮЗЫ: Flask API с серверной экономикой и проверкой Telegram initData.

В production: APP_ENV=production, BOT_TOKEN, DATABASE_URL, SESSION_SECRET.
Локально: LOCAL_DEMO=1 python app.py (только с 127.0.0.1 / ::1).
"""
from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, g, jsonify, render_template, request, session
from dotenv import load_dotenv
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

load_dotenv()  # Секреты читаем из .env до импорта storage.py и создания БД.

from engine import (ACHIEVEMENTS, CONFIG, LENGTHS, MAX_INVENTORY, MODES,
                    RARITIES, START_BALANCE, achievements, fresh_stats,
                    make_roll, progress, quote, should_auto_collect,
                    update_roll_stats)
from security import AuthenticationError, validate_init_data
from storage import Item, Player, RequestLog, Roll, get_player, transaction, utcnow

PRODUCTION = (os.environ.get('APP_ENV', '').lower() == 'production' or bool(os.environ.get('RAILWAY_ENVIRONMENT')))
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()
LOCAL_DEMO = os.environ.get('LOCAL_DEMO') == '1'
if PRODUCTION and (not BOT_TOKEN or not os.environ.get('DATABASE_URL') or
                   not os.environ.get('SESSION_SECRET')):
    raise RuntimeError('Production требует BOT_TOKEN, DATABASE_URL и SESSION_SECRET')

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET') or os.urandom(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=PRODUCTION, MAX_CONTENT_LENGTH=32768)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message, self.status = message, status
        super().__init__(message)


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify({'error': err.message}), err.status


@app.errorhandler(ValueError)
def handle_value_error(err):
    return jsonify({'error': str(err)}), 400


def _local_request():
    # Не путать внутренний localhost reverse proxy с публичным запросом.
    hostname = request.host.split(':')[0].lower()
    return hostname in ('localhost', '127.0.0.1', '[::1]') and request.remote_addr in ('127.0.0.1', '::1')


@app.before_request
@app.before_request
def authenticate():
    if not request.path.startswith('/api/'):
        return

    raw = request.headers.get(
        'X-Telegram-Init-Data', ''
    )

    # Локальный режим для разработки
    if not raw:
        if LOCAL_DEMO and not PRODUCTION and _local_request():
            if 'local_player' not in session:
                session['local_player'] = uuid.uuid4().hex

            g.player_id = 'demo:' + session['local_player']
            g.display_name = 'Тестовый игрок'
            g.telegram = None
            return

        app.logger.warning(
            "Telegram initData не получены: %s",
            request.path
        )

        raise ApiError(
            'Сервер не получил данные авторизации Telegram.',
            401
        )

    # Проверяем настройки сервера
    if not BOT_TOKEN:
        raise ApiError(
            'На сервере не настроен BOT_TOKEN.',
            503
        )

    # Проверяем подлинность пользователя
    try:
        user = validate_init_data(raw, BOT_TOKEN)

        g.player_id = user['id']
        g.display_name = user['first_name']
        g.telegram = user

    except (AuthenticationError, ValueError) as exc:
        app.logger.warning(
            "Ошибка Telegram-авторизации: %s",
            exc
        )

        raise ApiError(
            'Telegram-авторизация не прошла.',
            401
        )

def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError('Ожидался JSON-объект')
    return data


def item_dict(item: Item, user_id: str | None = None):
    return {'id': item.id, 'tag': item.tag, 'rarity': RARITIES[item.rarity],
            'price': item.price, 'multiplier': item.multiplier,
            'bonuses': item.bonuses, 'status': item.status,
            'asking_price': item.asking_price,
            'suggested_asking_price': min(999999999, (item.price * 12 + 5) // 10),
            'mine': item.owner_id == user_id if user_id else None}


def roll_dict(roll: Roll):
    return {'id': roll.id, 'tag': roll.tag, 'username': roll.tag,
            'rarity': RARITIES[roll.rarity], 'multiplier': roll.multiplier,
            'bonuses': roll.bonuses, 'price': roll.price, 'status': roll.status}


def _inventory(db, user):
    return db.query(Item).filter(Item.owner_id == user,
                                 Item.status == 'inventory').order_by(Item.created_at.desc()).all()


def _pending(db, user):
    return db.query(Roll).filter(Roll.player_id == user, Roll.status == 'pending').order_by(Roll.created_at.desc()).all()


def _owned_tags(db, user):
    return {r[0] for r in db.query(Item.tag).filter(Item.owner_id == user,
                    Item.status.in_(('inventory', 'listed'))).all()}


def _owned_inventory_count(db, user):
    return db.query(func.count(Item.id)).filter(Item.owner_id == user, Item.status == 'inventory').scalar() or 0


def _update_balance_max(player):
    stats = {**fresh_stats(), **(player.stats or {})}
    stats['maxBalance'] = max(stats['maxBalance'], player.balance)
    player.stats = stats


def _update_inventory_max(db, player):
    stats = {**fresh_stats(), **(player.stats or {})}
    stats['maxInventory'] = max(stats['maxInventory'], _owned_inventory_count(db, player.id))
    player.stats = stats


def _sell_roll(player, roll):
    if roll.status != 'pending':
        raise ApiError('Этот результат уже обработан', 409)
    roll.status = 'sold'
    player.balance += roll.price
    _update_balance_max(player)


def _collect_roll(db, player, roll):
    if roll.status != 'pending':
        raise ApiError('Этот результат уже обработан', 409)
    if _owned_inventory_count(db, player.id) >= MAX_INVENTORY:
        raise ApiError('Инвентарь полон', 409)
    if roll.tag in _owned_tags(db, player.id):
        raise ApiError('Такой юз уже есть у игрока', 409)
    item = Item(owner_id=player.id, tag=roll.tag, rarity=roll.rarity,
                price=roll.price, multiplier=roll.multiplier,
                bonuses=roll.bonuses, status='inventory')
    db.add(item)
    db.flush()
    roll.status = 'collected'
    _update_inventory_max(db, player)
    return item


def _state(db: Session, player: Player):
    inventory = _inventory(db, player.id)
    pending = _pending(db, player.id)
    listed = db.query(Item).filter(Item.owner_id == player.id,
                                   Item.status == 'listed').order_by(Item.created_at.desc()).all()
    identity = getattr(g, 'telegram', None)
    return {'player': {'telegram': {
                           'id': identity['telegram_id'] if identity else None,
                           'first_name': identity['first_name'] if identity else 'Тестовый игрок',
                           'last_name': identity['last_name'] if identity else '',
                           'username': identity['username'] if identity else None,
                           'photo_url': identity['photo_url'] if identity else None,
                       },
                       'balance': player.balance, 'spins': player.spins,
                       'auto_level': player.auto_level,
                       'installed_item_id': player.installed_item_id,
                       'installed_tag': next((x.tag for x in inventory if x.id == player.installed_item_id), None),
                       'stats': player.stats, 'level': progress(player.spins),
                       'guarantees': player.guarantees,
                       'luck_left': max(0, int((player.luck_until.replace(tzinfo=timezone.utc) - utcnow()).total_seconds()))
                           if player.luck_until else 0},
            'inventory': [item_dict(x) for x in inventory],
            'inventory_total': sum(x.price for x in inventory),
            'pending': [roll_dict(x) for x in pending],
            'own_listings': [item_dict(x) for x in listed],
            'achievements': achievements(player.stats or fresh_stats(), player.claimed or {})}


@app.get('/')
def index():
    return render_template('index.html')


@app.get('/health')
def health():
    return jsonify({'status': 'ok'})


@app.get('/api/config')
def config():
    return jsonify({'modes': CONFIG['MODES'], 'lengths': CONFIG['LENGTH_OPTIONS'],
                    'auto_levels': CONFIG['AUTO_LEVELS'], 'max_inventory': MAX_INVENTORY,
                    'demo': LOCAL_DEMO and not PRODUCTION and _local_request(),
                    'rarities': RARITIES})


@app.get('/api/quote')
def price_quote():
    try:
        mode = request.args.get('mode', 'common')
        length = int(request.args.get('length', 0))
        count = int(request.args.get('count', 1))
        return jsonify(quote(mode, length, count))
    except (TypeError, ValueError) as err:
        raise ApiError(str(err))


@app.get('/api/state')
def state():
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        payload = _state(db, player)
    return jsonify(payload)


@app.post('/api/spin')
def spin():
    data = body()
    mode = data.get('mode', 'common')
    length = data.get('length', 0)
    count = data.get('count', 1)
    request_id = data.get('request_id')
    if type(length) is not int or type(count) is not int or not isinstance(mode, str):
        raise ApiError('Параметры крутки должны быть корректных типов')
    if not isinstance(request_id, str) or not re.fullmatch(r'[\w-]{8,80}', request_id, re.ASCII):
        raise ApiError('Нужен корректный уникальный request_id')
    offer = quote(mode, length, count)
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        previous = db.query(RequestLog).filter(RequestLog.player_id == player.id,
                                                RequestLog.request_id == request_id).one_or_none()
        request_body = {'mode': mode, 'length': length, 'count': count}
        if previous:
            if previous.body != request_body:
                raise ApiError('request_id уже использовался с другими параметрами', 409)
            response = previous.response
        else:
            # Неразобранные предыдущие результаты автоматически продаются
            # при следующей крутке. Никаких двойных выплат.
            for old in _pending(db, player.id):
                _sell_roll(player, old)
            if player.balance < offer['total_cost']:
                raise ApiError('Недостаточно игровой валюты', 409)
            player.balance -= offer['total_cost']
            player.spins += count
            own_tags = _owned_tags(db, player.id)
            guarantees = dict(player.guarantees or {})
            expires = player.luck_until
            if expires and expires.tzinfo is None: expires = expires.replace(tzinfo=timezone.utc)
            luck = 100 if expires and expires > utcnow() else 1
            rolls = []
            for _ in range(count):
                generated = make_roll(mode, length, own_tags, guarantees, luck=luck)
                # Avoid repeats inside same batch; tags can reappear after sale.
                own_tags.add(generated['tag'])
                result = Roll(player_id=player.id, **generated, status='pending')
                db.add(result)
                db.flush()
                player.stats = update_roll_stats(player.stats, generated)
                if should_auto_collect(generated['rarity'], player.auto_level):
                    try:
                        _collect_roll(db, player, result)
                    except ApiError:
                        _sell_roll(player, result)
                elif player.auto_level != 0:
                    _sell_roll(player, result)
                rolls.append(roll_dict(result))
            player.guarantees = guarantees
            _update_balance_max(player)
            response = {'results': rolls, 'total_price': sum(x['price'] for x in rolls), 'quote': offer,
                        'message': 'Результаты созданы и записаны на сервере'}
            db.add(RequestLog(player_id=player.id, request_id=request_id,
                              body=request_body, response=response))
    return jsonify(response)


@app.post('/api/rolls/<roll_id>/collect')
def collect_roll(roll_id):
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        roll = db.query(Roll).filter(Roll.id == roll_id,
                                     Roll.player_id == player.id).with_for_update().one_or_none()
        if not roll: raise ApiError('Результат не найден', 404)
        item = _collect_roll(db, player, roll)
        result = item_dict(item)
    return jsonify({'item': result})


@app.post('/api/rolls/<roll_id>/sell')
def sell_roll(roll_id):
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        roll = db.query(Roll).filter(Roll.id == roll_id,
                                     Roll.player_id == player.id).with_for_update().one_or_none()
        if not roll: raise ApiError('Результат не найден', 404)
        _sell_roll(player, roll)
        result = roll_dict(roll)
    return jsonify({'roll': result})


@app.post('/api/rolls/sell-pending')
def sell_pending():
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        rolls = _pending(db, player.id)
        for roll in rolls: _sell_roll(player, roll)
    return jsonify({'sold_count': len(rolls)})


@app.get('/api/inventory')
def inventory():
    with transaction() as db:
        player = get_player(db, g.player_id)
        return jsonify({'items': [item_dict(x) for x in _inventory(db, player.id)]})


@app.post('/api/inventory/<item_id>/sell')
def sell_item(item_id):
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        item = db.query(Item).filter(Item.id == item_id, Item.owner_id == player.id,
                                    Item.status == 'inventory').with_for_update().one_or_none()
        if not item: raise ApiError('Предмет не найден', 404)
        if player.installed_item_id == item.id: raise ApiError('Сначала снимите установленный юз', 409)
        player.balance += item.price
        _update_balance_max(player)
        db.delete(item)
    return jsonify({'sold': True})


@app.post('/api/inventory/sell-all')
def sell_all():
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        items = [x for x in _inventory(db, player.id) if x.id != player.installed_item_id]
        total = sum(x.price for x in items)
        for item in items: db.delete(item)
        player.balance += total
        _update_balance_max(player)
    return jsonify({'total': total, 'count': len(items)})


@app.post('/api/profile/install')
def install():
    item_id = body().get('item_id')
    if not isinstance(item_id, str): raise ApiError('Укажите item_id')
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        item = db.query(Item).filter(Item.id == item_id, Item.owner_id == player.id,
                                    Item.status == 'inventory').one_or_none()
        if not item: raise ApiError('Юз не найден в инвентаре', 404)
        player.installed_item_id = item.id
    return jsonify({'installed': item_id})


@app.post('/api/profile/uninstall')
def uninstall():
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        player.installed_item_id = None
    return jsonify({'installed': None})


@app.post('/api/settings')
def update_settings():
    data = body()
    level = data.get('auto_level')
    if type(level) is not int or level not in (0, 1, 2, 3, 4):
        raise ApiError('Некорректный уровень авто-инвентаря')
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        player.auto_level = level
    return jsonify({'auto_level': level})


@app.post('/api/achievements/<achievement_id>/claim')
def claim_achievement(achievement_id):
    if achievement_id not in ACHIEVEMENTS: raise ApiError('Нет такого достижения', 404)
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        a = ACHIEVEMENTS[achievement_id]
        claimed = dict(player.claimed or {})
        if claimed.get(achievement_id): raise ApiError('Награда уже получена', 409)
        if player.stats.get(a['key'], 0) < a['target']:
            raise ApiError('Условия достижения ещё не выполнены', 409)
        claimed[achievement_id] = True
        player.claimed = claimed
        player.balance += a['reward']
        _update_balance_max(player)
    return jsonify({'reward': a['reward']})


@app.get('/api/market')
def market():
    with transaction() as db:
        get_player(db, g.player_id)
        listings = db.query(Item).filter(Item.status == 'listed').order_by(Item.created_at.desc()).limit(200).all()
        return jsonify({'listings': [item_dict(x, g.player_id) for x in listings]})


@app.post('/api/market/list')
def list_item():
    data = body()
    item_id, ask = data.get('item_id'), data.get('price')
    if not isinstance(item_id, str) or type(ask) is not int or not 1 <= ask <= 999999999:
        raise ApiError('Укажите предмет и цену от 1 до 999999999')
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        item = db.query(Item).filter(Item.id == item_id, Item.owner_id == player.id,
                                   Item.status == 'inventory').with_for_update().one_or_none()
        if not item: raise ApiError('Предмет не найден', 404)
        if player.installed_item_id == item.id: raise ApiError('Сначала снимите установленный юз', 409)
        item.status, item.asking_price = 'listed', ask
        result = item_dict(item)
    return jsonify({'listing': result})


@app.post('/api/market/<item_id>/cancel')
def cancel_listing(item_id):
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        item = db.query(Item).filter(Item.id == item_id, Item.owner_id == player.id,
                                   Item.status == 'listed').with_for_update().one_or_none()
        if not item: raise ApiError('Лот не найден', 404)
        if _owned_inventory_count(db, player.id) >= MAX_INVENTORY:
            raise ApiError('Нет места в инвентаре, нельзя снять лот', 409)
        item.status, item.asking_price = 'inventory', None
        _update_inventory_max(db, player)
    return jsonify({'cancelled': True})


@app.post('/api/market/<item_id>/buy')
def buy_listing(item_id):
    with transaction() as db:
        buyer = get_player(db, g.player_id, lock=True)
        item = db.query(Item).filter(Item.id == item_id,
                                   Item.status == 'listed').with_for_update().one_or_none()
        if not item: raise ApiError('Лот уже недоступен', 409)
        if item.owner_id == buyer.id: raise ApiError('Свой лот покупать нельзя', 409)
        if _owned_inventory_count(db, buyer.id) >= MAX_INVENTORY:
            raise ApiError('Инвентарь полон', 409)
        if item.tag in _owned_tags(db, buyer.id):
            raise ApiError('У вас уже есть такой юз', 409)
        if buyer.balance < item.asking_price:
            raise ApiError('Недостаточно игровой валюты', 409)
        seller = db.query(Player).filter(Player.id == item.owner_id).with_for_update().one()
        buyer.balance -= item.asking_price
        seller.balance += item.asking_price
        _update_balance_max(seller)
        item.owner_id = buyer.id
        item.status, item.asking_price = 'inventory', None
        _update_inventory_max(db, buyer)
        result = item_dict(item)
    return jsonify({'purchased': result})


@app.post('/api/reset')
def reset_account():
    if body().get('confirm') != 'RESET': raise ApiError('Для сброса укажите confirm=RESET')
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        db.query(Roll).filter(Roll.player_id == player.id).delete(synchronize_session=False)
        db.query(RequestLog).filter(RequestLog.player_id == player.id).delete(synchronize_session=False)
        db.query(Item).filter(Item.owner_id == player.id).delete(synchronize_session=False)
        player.balance, player.spins = START_BALANCE, 0
        player.stats = fresh_stats()
        player.claimed, player.guarantees = {}, {}
        player.auto_level, player.installed_item_id, player.luck_until = 0, None, None
    return jsonify({'reset': True})


@app.post('/api/dev/cheat')
def dev_cheat():
    # NEVER available to anyone through a public Railway URL, even if LOCAL_DEMO=1.
    if PRODUCTION or not LOCAL_DEMO or not _local_request():
        raise ApiError('Чит-команды отключены', 403)
    data = body()
    action, count = data.get('action'), data.get('count', 1)
    if action not in ('money', 'luck', 'mythic', 'secret', 'exclusive') or type(count) is not int or count not in (1, 5):
        raise ApiError('Некорректная тестовая команда')
    with transaction() as db:
        player = get_player(db, g.player_id, lock=True)
        if action == 'money':
            player.balance += 1_000_000
            _update_balance_max(player)
        elif action == 'luck':
            from datetime import timedelta
            player.luck_until = utcnow() + timedelta(seconds=30)
        else:
            guar = dict(player.guarantees or {})
            guar[action] = min(100, guar.get(action, 0) + count)
            player.guarantees = guar
    return jsonify({'ok': True})


if __name__ == '__main__':
    # Через HTTPS-туннель приложение доступно из интернета — отладчик Werkzeug запрещён.
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', '5000')), debug=False)
