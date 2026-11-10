"""Проверяем Telegram ID -> отдельный игровой счёт и отображаемое имя."""
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

# База тестов отделена от прогресса пользователя.
_TEMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(_TEMP.name) / 'profile_test.sqlite3')
os.environ['BOT_TOKEN'] = '123456:unit-test-token'
os.environ['LOCAL_DEMO'] = '0'
os.environ.pop('APP_ENV', None)
os.environ.pop('RAILWAY_ENVIRONMENT', None)

import app as game_app
from storage import engine


def signed_init_data(tg_id, first_name, username=None, photo_url=None,
                     telegram_signature=None):
    user = {'id': tg_id, 'first_name': first_name}
    if username: user['username'] = username
    if photo_url: user['photo_url'] = photo_url
    fields = {
        'auth_date': str(int(time.time())),
        'user': json.dumps(user, separators=(',', ':'), ensure_ascii=False),
    }
    if telegram_signature:
        fields['signature'] = telegram_signature
    secret = hmac.new(b'WebAppData', os.environ['BOT_TOKEN'].encode(), hashlib.sha256).digest()
    text = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    fields['hash'] = hmac.new(secret, text.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class TelegramProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = game_app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        engine.dispose()
        _TEMP.cleanup()

    def get_state(self, auth):
        return self.client.get('/api/state', headers={'X-Telegram-Init-Data':auth})

    def test_telegram_account_isolation_and_display(self):
        alice = signed_init_data(77711, 'Алина', 'alice_player', 'https://example.com/pic.jpg')
        bob = signed_init_data(88822, 'Борис')
        first = self.get_state(alice)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json['player']['telegram']['username'], 'alice_player')
        self.assertEqual(first.json['player']['telegram']['first_name'], 'Алина')
        self.assertEqual(first.json['player']['telegram']['photo_url'], 'https://example.com/pic.jpg')
        self.assertEqual(first.json['player']['telegram']['id'], 77711)
        self.assertEqual(first.json['player']['balance'], 500)
        result = self.client.post('/api/spin',
            headers={'X-Telegram-Init-Data':alice},
            json={'mode':'common', 'length':0, 'count':1, 'request_id':'alice-roll-001'})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        second = self.get_state(alice)
        other = self.get_state(bob)
        self.assertEqual(second.json['player']['spins'], 1)
        self.assertEqual(other.json['player']['spins'], 0)
        self.assertEqual(other.json['player']['balance'], 500)
        self.assertIsNone(other.json['player']['telegram']['username'])
        self.assertEqual(other.json['player']['telegram']['first_name'], 'Борис')
        self.assertEqual(self.get_state(alice).json['player']['spins'], 1)

    def test_browser_and_modified_identity_rejected(self):
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        raw = signed_init_data(4321, 'Велимир', 'velimir_dev')
        self.assertEqual(self.get_state(raw.replace('velimir_dev', 'fake_name')).status_code, 401)

    def test_current_init_data_with_signature_is_accepted(self):
        raw = signed_init_data(54321, 'РўРµСЃС‚',
                               telegram_signature='third-party-signature')
        self.assertEqual(self.get_state(raw).status_code, 200)

    def test_no_unsafe_avatar_url(self):
        data = signed_init_data(123456, 'Тест', photo_url='javascript:alert(1)')
        response = self.get_state(data)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json['player']['telegram']['photo_url'])


if __name__ == '__main__':
    unittest.main()
