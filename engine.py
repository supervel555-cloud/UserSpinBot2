"""Правила ЮЗЫ. Только сервер решает результат и игровую стоимость.

Основано на исходной версии Mini App. Существующие генераторы и функция
calculate_multiplier из учебного game.py используются здесь на сервере.
Все @теги — вымышленные коллекционные объекты, не Telegram usernames.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from game import calculate_multiplier, generate_by_pattern, generate_leet, generate_username

CONFIG = json.loads((Path(__file__).parent / 'game_config.json').read_text(encoding='utf-8'))
MODES = {m['id']: m for m in CONFIG['MODES']}
LENGTHS = {int(o['len']): o for o in CONFIG['LENGTH_OPTIONS']}
ACHIEVEMENTS = {a['id']: a for a in CONFIG['ACHIEVEMENTS']}
EXCLUSIVE = tuple(CONFIG['EXCLUSIVE_TAGS'])
SECRET = tuple(CONFIG['SECRET_TAGS'])
LEVELS = CONFIG['LEVEL_THRESHOLDS']
MAX_INVENTORY = 50
START_BALANCE = 500
RNG = secrets.SystemRandom()

RARITIES = {
    'Обычный':     {'name': 'Обычный', 'color': '#9ca3af', 'pips': 1, 'minPrice': 60, 'maxPrice': 140},
    'Необычный':   {'name': 'Необычный', 'color': '#22c55e', 'pips': 2, 'minPrice': 180, 'maxPrice': 350},
    'Редкий':      {'name': 'Редкий', 'color': '#3b82f6', 'pips': 3, 'minPrice': 1000, 'maxPrice': 3000},
    'Эпический':   {'name': 'Эпический', 'color': '#a855f7', 'pips': 4, 'minPrice': 10000, 'maxPrice': 30000},
    'Легендарный': {'name': 'Легендарный', 'color': '#eab308', 'pips': 5, 'minPrice': 100000, 'maxPrice': 500000},
    'Мифический':  {'name': 'Мифический', 'color': '#ef4444', 'pips': 6, 'minPrice': 1000000, 'maxPrice': 5000000},
    'Секретный':   {'name': 'Секретный', 'color': '#00ffff', 'pips': 7, 'minPrice': 10000000, 'maxPrice': 50000000},
    'Эксклюзивный':{'name': 'Эксклюзивный', 'color': '#ff0080', 'pips': 8, 'minPrice': 100000000, 'maxPrice': 500000000},
}
RARITY_ORDER = list(RARITIES)


def _pool() -> tuple[str, ...]:
    words = ('dark wolf fire ice storm shadow ghost neon cyber ninja fox hawk void star moon sun '
             'rain snow wind rock iron steel gold silver blood bone soul mind dream night day dawn '
             'dusk time space zero alpha omega prime royal epic hero titan max pro god master lord '
             'angel demon witch mage knight rogue hunter rider walker runner seeker keeper maker sniper '
             'tanker hacker gamer coder dev user guest anon phantom venom blaze frost aqua terra aero '
             'pyro luna solar cosmo astro galaxy planet orbit comet crystal pearl ruby emerald topaz '
             'opal jade nord arctic sahara nile apex zenith nova vortex chaos order logic fury rage '
             'peace calm wild legend myth holy light grey blue red green black white cyan punk code '
             'byte bit chip data cache stack loop array codec node sudo kernel shell pixel retro '
             'laser plasma quantum photon atomic nuclear').split()
    nums = ('1', '2', '3', '7', '9', '13', '42', '99', '777', '888', '666')
    prefixes = ('x', 'the', 'mr', 'its', 'no', 'only', 'just', 'im')
    suffixes = ('x', 'z', 'gg', 'pro', 'yt', 'tv', 'op', 'xp')
    pool = set()
    def add(s: str):
        if 3 <= len(s) <= 8 and s.isascii() and s.isalnum():
            pool.add(s)
    for w in words:
        add(w)
        for n in nums: add(w + n)
        for p in prefixes: add(p + w)
        for x in suffixes: add(w + x)
    abc = 'abcdefghijklmnopqrstuvwxyz'
    for a in abc:
        add(a * 3)
        for b in abc: add(a + b + a)
    for d in '0123456789': add(d * 3)
    return tuple(sorted(pool.difference(EXCLUSIVE).difference(SECRET)))

POOL = _pool()


def quote(mode: str, length: int, count: int = 1) -> dict[str, Any]:
    if mode not in MODES or length not in LENGTHS or count not in (1, 5):
        raise ValueError('Неизвестный режим, длина или количество круток')
    # Избегаем float округления — стоимость всегда целое число от сервера.
    factor = {0: 100, 3: 180, 4: 150, 5: 120, 6: 100, 7: 100, 8: 100}[length]
    unit = (MODES[mode]['cost'] * factor + 50) // 100
    return {'mode': mode, 'length': length, 'count': count,
            'unit_cost': unit, 'total_cost': unit * count}


def pick_tag(length: int, unavailable: set[str], guarantees: dict[str, int]) -> tuple[str, str | None]:
    def eligible(tags):
        return [t for t in tags if (length == 0 or len(t) == length) and t not in unavailable]
    for field, tags, forced_name in (('exclusive', EXCLUSIVE, 'Эксклюзивный'),
                                     ('secret', SECRET, 'Секретный')):
        if guarantees.get(field, 0) > 0:
            candidates = eligible(tags)
            if candidates:
                guarantees[field] -= 1
                return RNG.choice(candidates), forced_name
            guarantees[field] = 0
    if RNG.random() < .0001:
        candidates = eligible(EXCLUSIVE)
        if candidates: return RNG.choice(candidates), 'Эксклюзивный'
    if RNG.random() < .001:
        candidates = eligible(SECRET)
        if candidates: return RNG.choice(candidates), 'Секретный'
    # Вариант учебных генераторов Велимира. Исключаем невалидные и занятые теги.
    if RNG.random() < .1:
        fn = RNG.choice((generate_username, generate_by_pattern, generate_leet))
        for _ in range(8):
            tag = fn()
            if 3 <= len(tag) <= 8 and (length == 0 or len(tag) == length) and tag not in unavailable:
                return tag, None
    candidates = eligible(POOL)
    if not candidates:
        raise ValueError('Нет доступных юзов выбранной длины')
    return RNG.choice(candidates), None


def get_multiplier(tag: str, luck: int = 1) -> tuple[int, list[str]]:
    # Старый интерфейс давал коротким (2 символа) отдельный бонус.
    if len(tag) < 3:
        mult, bonuses = 500, ['Короткий ×500']
        if tag == tag[::-1]:
            mult *= 8
            bonuses.append('Палиндром ×8')
        if len(set(tag)) == 1:
            mult *= 100
            bonuses.append('Все одинаковые ×100')
        if tag.isalpha():
            mult *= 2
            bonuses.append('Только буквы ×2')
    else:
        mult, old_bonuses = calculate_multiplier(tag)
        bonuses = [str(x[0]) for x in old_bonuses]
    if tag.isdigit():
        mult *= 2
        bonuses.append('Только цифры ×2')
    if luck > 1:
        mult *= luck
        bonuses.append(f'Удача ×{luck}')
    return mult, bonuses


def get_rarity(tag: str, mode: str, forced: str | None = None,
               mythic_guarantees: dict[str, int] | None = None) -> dict[str, Any]:
    if tag in EXCLUSIVE: return RARITIES['Эксклюзивный']
    if tag in SECRET: return RARITIES['Секретный']
    if forced: return RARITIES[forced]
    if mythic_guarantees is not None and mythic_guarantees.get('mythic', 0) > 0:
        mythic_guarantees['mythic'] -= 1
        return RARITIES['Мифический']
    percent = RNG.random() * 100
    if mode == 'common' and percent < .00001: return RARITIES['Мифический']
    if mode == 'uncommon' and percent < .00005: return RARITIES['Мифический']
    if mode == 'rare' and percent < .0001: return RARITIES['Мифический']
    if mode == 'epic':
        return RARITIES['Мифический' if percent < .001 else 'Легендарный' if percent < 1.001 else 'Эпический']
    if mode == 'legendary':
        return RARITIES['Мифический' if percent < .5 else 'Легендарный' if percent < 20.5 else 'Эпический']
    mult, _ = get_multiplier(tag)
    name = ('Мифический' if mult >= 50000 else 'Легендарный' if mult >= 10000 else
            'Эпический' if mult >= 3000 else 'Редкий' if mult >= 500 else
            'Необычный' if mult >= 150 else 'Обычный')
    if mode == 'common':
        if RARITIES[name]['pips'] >= 4 and RNG.random() < .99: name = 'Редкий'
        elif name == 'Редкий' and RNG.random() < .85: name = 'Необычный'
    elif mode == 'uncommon':
        if RARITIES[name]['pips'] >= 4 and RNG.random() < .95: name = 'Редкий'
        elif name == 'Редкий' and RNG.random() < .6: name = 'Необычный'
    elif mode == 'rare':
        if RARITIES[name]['pips'] >= 5 and RNG.random() < .8: name = 'Редкий'
        elif name == 'Эпический' and RNG.random() < .4: name = 'Редкий'
    return RARITIES[name]


def make_roll(mode: str, length: int, unavailable: set[str],
              guarantees: dict[str, int] | None = None, luck: int = 1) -> dict[str, Any]:
    guarantees = guarantees if guarantees is not None else {}
    tag, forced = pick_tag(length, unavailable, guarantees)
    rarity = get_rarity(tag, mode, forced, guarantees)
    multiplier, bonuses = get_multiplier(tag, luck)
    price = RNG.randrange(rarity['minPrice'], rarity['maxPrice'] + 1)
    return {'tag': tag, 'rarity': rarity['name'], 'price': price,
            'multiplier': multiplier, 'bonuses': bonuses}


def fresh_stats() -> dict[str, int]:
    return {k: 0 for k in ('totalSpins', 'lettersOnly', 'digitsOnly', 'tripleDigit',
        'allSame', 'palindrome', 'epicCount', 'legendaryCount', 'mythicCount',
        'secretCount', 'exclusiveCount', 'rareCount', 'bigWin', 'maxInventory',
        'len8Count', 'maxBalance')}


def update_roll_stats(stats: dict, data: dict) -> dict:
    stats = {**fresh_stats(), **(stats or {})}
    tag, rarity, price = data['tag'], data['rarity'], data['price']
    stats['totalSpins'] += 1
    if tag.isalpha(): stats['lettersOnly'] += 1
    if tag.isdigit(): stats['digitsOnly'] += 1
    if len(tag) >= 3 and tag.isdigit() and len(set(tag)) == 1: stats['tripleDigit'] += 1
    if len(set(tag)) == 1: stats['allSame'] += 1
    if tag == tag[::-1]: stats['palindrome'] += 1
    rkey = {'Эпический': 'epicCount', 'Легендарный': 'legendaryCount',
            'Мифический': 'mythicCount', 'Секретный': 'secretCount',
            'Эксклюзивный': 'exclusiveCount', 'Редкий': 'rareCount'}
    if rarity in rkey: stats[rkey[rarity]] += 1
    if price >= 500000: stats['bigWin'] += 1
    if len(tag) == 8: stats['len8Count'] += 1
    return stats


def progress(spins: int) -> dict[str, Any]:
    level = 1
    for threshold in LEVELS[1:]:
        if spins >= threshold: level += 1
        else: break
    previous = LEVELS[level - 1]
    nxt = LEVELS[level] if level < len(LEVELS) else previous
    pct = min(100, (spins - previous) / (nxt - previous) * 100) if nxt > previous else 100
    return {'level': level, 'current': previous, 'next': nxt, 'percent': round(pct, 2)}


def achievements(stats: dict, claimed: dict) -> list[dict]:
    return [{**a, 'progress': min(int(stats.get(a['key'], 0)), a['target']),
             'percent': min(100, round(100 * stats.get(a['key'], 0) / a['target'], 2)),
             'claimed': bool(claimed.get(a['id'])),
             'ready': not claimed.get(a['id']) and stats.get(a['key'], 0) >= a['target']}
            for a in ACHIEVEMENTS.values()]


def should_auto_collect(name: str, level: int) -> bool:
    if level == 0: return False
    n = RARITY_ORDER.index(name) + 1
    return level == 1 or (level == 2 and n >= 4) or (level == 3 and n >= 5) or (level == 4 and n >= 6)
