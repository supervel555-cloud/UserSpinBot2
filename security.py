"""Telegram initData: HMAC-SHA256, возраст данных, подтверждённый user.id."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl, urlsplit


class AuthenticationError(ValueError):
    pass


def validate_init_data(raw: str, bot_token: str, ttl: int = 86400) -> dict:
    if not raw or len(raw) > 16384 or not bot_token:
        raise AuthenticationError('Отсутствует Telegram initData')
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    fields = dict(pairs)
    if len(pairs) != len(fields):
        raise AuthenticationError('Повторяющиеся поля initData')
    supplied = fields.get('hash', '')
    if len(supplied) != 64:
        raise AuthenticationError('Отсутствует Telegram-подпись')
    # For the bot-token HMAC, Telegram's `signature` field is part of the
    # received data.  It is excluded only by the separate third-party
    # Ed25519 validation flow; excluding it here makes current Mini App
    # payloads fail their regular `hash` check.
    check = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()) if k != 'hash')
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, supplied):
        raise AuthenticationError('Неверная Telegram-подпись')
    try:
        timestamp = int(fields['auth_date'])
        user = json.loads(fields['user'])
        user_id = int(user['id'])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError('Нет валидного Telegram-пользователя') from exc
    now = time.time()
    if timestamp > now + 60 or timestamp < now - ttl:
        raise AuthenticationError('Telegram initData устарели, откройте Mini App заново')
    if user_id <= 0:
        raise AuthenticationError('Неверный Telegram user ID')
    # Авторитетный идентификатор и поля профиля получены только из подписанных initData.
    # Telegram username может отсутствовать либо измениться; ID остаётся постоянным.
    first_name = str(user.get('first_name') or 'Игрок')[:80]
    last_name = str(user.get('last_name') or '')[:80]
    username = user.get('username')
    username = username if isinstance(username, str) and username.isascii() and username.replace('_', '').isalnum() else None
    photo_url = user.get('photo_url')
    if not isinstance(photo_url, str) or len(photo_url) > 2048:
        photo_url = None
    elif urlsplit(photo_url).scheme != 'https' or not urlsplit(photo_url).hostname:
        photo_url = None
    return {
        'id': f'tg:{user_id}',
        'telegram_id': user_id,
        'first_name': first_name,
        'last_name': last_name,
        'username': username,
        'photo_url': photo_url,
    }
