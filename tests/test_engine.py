import unittest
import time
import hmac
import json
import hashlib
from urllib.parse import urlencode

from engine import make_roll, get_multiplier, quote, progress, achievements, fresh_stats
from security import validate_init_data, AuthenticationError


class GameTests(unittest.TestCase):
    def test_quotes_server_side(self):
        self.assertEqual(quote('common',0,1)['total_cost'],100)
        self.assertEqual(quote('common',3,5)['total_cost'],900)
        with self.assertRaises(ValueError): quote('admin',0,1)
        with self.assertRaises(ValueError): quote('common',2,1)
        with self.assertRaises(ValueError): quote('common',0,100000)

    def test_multiplier_from_python(self):
        mult, bonuses = get_multiplier('ababab')
        self.assertGreaterEqual(mult,120)
        self.assertTrue(any('Повтор блока' in b for b in bonuses))
        m2, b2 = get_multiplier('777')
        self.assertGreater(m2,0)
        self.assertTrue(any('Только цифры' in b for b in b2))

    def test_generation_respects_length(self):
        for length in (3,4,5,6,7,8):
            roll=make_roll('common',length,set())
            self.assertEqual(len(roll['tag']),length)
            self.assertIn(roll['rarity'],('Обычный','Необычный','Редкий','Эпический','Легендарный','Мифический','Секретный','Эксклюзивный'))
            self.assertIsInstance(roll['price'],int)

    def test_achievements_progress(self):
        stats=fresh_stats();stats['totalSpins']=1
        first=next(a for a in achievements(stats,{}) if a['id']=='first_spin')
        self.assertTrue(first['ready'])
        self.assertFalse(next(a for a in achievements(stats,{'first_spin':True}) if a['id']=='first_spin')['ready'])
        self.assertEqual(progress(10)['level'],2)

    def test_signed_telegram_identity(self):
        token='123456:example-token'
        data={'user':json.dumps({'id':42,'first_name':'Velimir'},separators=(',',':')),
              'auth_date':str(int(time.time()))}
        check='\n'.join(f'{k}={v}' for k,v in sorted(data.items()))
        secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
        data['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        valid=urlencode(data)
        self.assertEqual(validate_init_data(valid,token)['id'],'tg:42')
        with self.assertRaises(AuthenticationError): validate_init_data(valid.replace('Velimir','Other'),token)
        data['auth_date']='1000000000'
        with self.assertRaises(AuthenticationError): validate_init_data(urlencode(data),token)

if __name__ == '__main__': unittest.main()
