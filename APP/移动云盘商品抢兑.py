"""
移动云盘抢兑脚本 v1.1.0

功能:
1. 获取商品列表
2. 通过商品列表获取商品ID
3. 对商品进行兑换操作
4. 查询已有商品
5. 支持指定商品ID进行抢兑
6. 兼容青龙面板，支持多账号并发

配置说明:
变量名: yunpan
格式: Authorization值#手机号#商品ID1,商品ID2 (多账号用 & 分隔)
- 商品ID必填，不填则不兑换，仅查询商品列表和已有商品
- 抓包请求头 Authorization 值

环境变量:
- yunpan: 主变量，格式 Authorization#手机号#商品ID

依赖安装:
pip3 install requests pycryptodome

定时规则建议 (Cron):
0 10,16,24 * * * (每日10:00,16:00,24:00执行抢兑)

Author: xiaohai
Update: 2026.06.12
"""

import base64
import hashlib
import json
import os
import random
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from os import path
from urllib.parse import unquote

import requests
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

SCRIPT_VERSION = '1.1.0'

# ==================== 常量配置 ====================
ua = 'Mozilla/5.0 (Linux; Android 11; M2012K10C Build/RP1A.200720.011; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/90.0.4430.210 Mobile Safari/537.36 MCloudApp/10.0.1'

market_ua_pool = [
    'Mozilla/5.0 (Linux; Android 14; 23127HN0CC Build/UKQ1.230917.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.146 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 14; 24053PY09C Build/UP1A.231005.007; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/142.0.6522.118 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 13; 23049RAD8C Build/TKQ1.221114.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.146 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 14; PGP110 Build/UKQ1.230917.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/141.0.6464.127 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 14; RMXP4721 Build/UKQ1.230917.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.146 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 13; M2012K10C Build/RP1A.200720.011; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/142.0.6522.118 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 14; V2324A Build/UP1A.231005.007; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.146 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 13; RE58B1 Build/TKQ1.221114.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/140.0.6385.82 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 14; 22081212C Build/UKQ1.230917.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.146 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 14; LLY-AN00 Build/HONORLLY-AN00; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/142.0.6522.118 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
]

MARKET_NAME = 'sign_in_3'
CLIENT_VERSION = '13.0.0'
BASE_M = 'https://m.mcloud.139.com'
SOURCE_ID = '1097'
TARGET_SOURCE_ID = '001005'

SOLVE_API = os.getenv('solve_api', 'http://yunpan.apisky.cn/api/sms/solve')

# 数美ID相关
_SM_PUBLIC_KEY = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC8KHAcHbkCn5rxGgGJE+07tY+pt86D/oZ7sA51FaEBv2jgno2TI9zHJVYKJynmiKpixgwUcv93EfWIrU/p/UCs5Vu+odS3I4UBp3R7IZ1A0W01FkumAHYW2PQpMm8ueQKPLUq/idkpG/9b2JDv/qU+Ks36nbUPwlW4CjdfrV+V9QIDAQAB"
_SM_ORGANIZATION = "FXlyfmWg2AzwbrxDKSv5"
_SM_ANDROID_MODELS = [
    {'model': '23127HN0CC', 'build': 'UKQ1.230917.001', 'android': '14', 'chrome': '143.0.7499.146'},
    {'model': '24053PY09C', 'build': 'UP1A.231005.007', 'android': '14', 'chrome': '142.0.6522.118'},
    {'model': '23049RAD8C', 'build': 'TKQ1.221114.001', 'android': '13', 'chrome': '143.0.7499.146'},
    {'model': 'PGP110', 'build': 'UKQ1.230917.001', 'android': '14', 'chrome': '141.0.6464.127'},
    {'model': 'RMXP4721', 'build': 'UKQ1.230917.001', 'android': '14', 'chrome': '143.0.7499.146'},
    {'model': 'M2012K10C', 'build': 'RP1A.200720.011', 'android': '11', 'chrome': '142.0.6522.118'},
    {'model': 'V2324A', 'build': 'UP1A.231005.007', 'android': '14', 'chrome': '143.0.7499.146'},
    {'model': 'RE58B1', 'build': 'TKQ1.221114.001', 'android': '13', 'chrome': '140.0.6385.82'},
    {'model': '22081212C', 'build': 'UKQ1.230917.001', 'android': '14', 'chrome': '143.0.7499.146'},
    {'model': 'LLY-AN00', 'build': 'HONORLLY-AN00', 'android': '14', 'chrome': '142.0.6522.118'},
]
_SM_SCREENS = [
    {'w': 1080, 'h': 2340, 'dpr': 2.625},
    {'w': 1080, 'h': 2400, 'dpr': 2.75},
    {'w': 720, 'h': 1280, 'dpr': 1.5},
    {'w': 1080, 'h': 2160, 'dpr': 2.625},
    {'w': 1080, 'h': 2310, 'dpr': 2.625},
]
_SM_RSA_KEY = RSA.import_key(base64.b64decode(_SM_PUBLIC_KEY))

# 全局变量
err_accounts = ''
all_logs = ''
user_summary = ''


# ==================== 数美ID ====================
def _sm_rsa_encrypt(plaintext):
    cipher = PKCS1_v1_5.new(_SM_RSA_KEY)
    return base64.b64encode(cipher.encrypt(plaintext.encode('utf-8'))).decode('ascii')


def _sm_get_smid(uid):
    now = datetime.now()
    ts = now.strftime('%Y%m%d%H%M%S')
    md5_uid = hashlib.md5(uid.encode('utf-8')).hexdigest()
    base_str = ts + md5_uid + '00'
    check = hashlib.md5(('smsk_web_' + base_str).encode('utf-8')).hexdigest()[:14]
    return base_str + check + '0'


def _generate_device_profile():
    phone = random.choice(_SM_ANDROID_MODELS)
    screen = random.choice(_SM_SCREENS)
    ua_str = (f'Mozilla/5.0 (Linux; Android {phone["android"]}; {phone["model"]} '
              f'Build/{phone["build"]}; wv) AppleWebKit/537.36 (KHTML, like Gecko) '
              f'Version/4.0 Chrome/{phone["chrome"]} Mobile Safari/537.36 '
              f'MCloudApp/13.0.0 AppLanguage/zh-CN')
    sw, sh = screen['w'], screen['h']
    avail_h = sh - random.randint(48, 128)
    uid = str(uuid.uuid4())
    ep = _sm_rsa_encrypt(uid)
    smid = _sm_get_smid(uid)
    now_ts = int(time.time() * 1000)
    start_time = now_ts - random.randint(1800000, 5400000)
    now_cst = datetime.now(timezone(timedelta(hours=8)))
    env = {
        'protocol': 242, 'organization': _SM_ORGANIZATION, 'appId': 'default',
        'os': 'web', 'version': '3.0.0', 'sdkver': '3.0.0', 'box': '',
        'rtype': 'all', 'smid': smid, 'subVersion': '1.0.0',
        'time': now_ts - start_time,
        'cdp': 0, 'maxTouchPoints': 5, 'connectionRtt': 0, 'cpucount': 8,
        'battery': {'charging': 0, 'level': round(0.6 + random.random() * 0.35, 2)},
        'dg': '5.0 ' + ua_str[len('Mozilla/'):],
        'gj': 'zh-CN', 'rr': 'Google Inc.', 'sv': 'Netscape', 'qc': 'Mozilla',
        'ye': 8, 'jq': 8, 'lo': [], 'bw': '', 'lr': 'Etc/GMT-8',
        'nr': 1, 'no': 0, 'br': 1, 'ra': 0,
        'gt': sw, 'wy': sw, 'cj': avail_h, 'wt': random.randint(100, 180),
        'hu': ['chrome'], 'documentExist': 1, 'yi': ['location'], 'dx': 'UTF-8',
        'ig': now_cst.strftime('%a %b %d %Y %H:%M:%S ') + '(GMT+08:00)',
        'ii': 1, 'fs': 0, 'ga': 0, 'tk': 0, 'rm': 0, 'kr': 0, 'nk': 0,
        'by': 'srgb', 'ar': 0, 'or': 0, 'et': 0, 'zc': 0, 'fj': 0, 'dc': 0, 'vd': 0,
        'ni': '', 'hn': '',
        'hv': '48000_2_1_0_2_explicit_speakers|______',
        'de': hashlib.md5(uid.encode('utf-8')).hexdigest()[:16] + '|10011011111000111100001100101101111100110101001110000000000100000',
        'xt': 1, 'vh': 0, 'xc': {'red': '0'},
        'pm': {
            'default': round(120.5 + random.random() * 20, 1),
            'apple': round(120.5 + random.random() * 20, 1),
            'serif': round(100 + random.random() * 20, 1),
            'sans': round(120.5 + random.random() * 20, 1),
            'mono': round(100 + random.random() * 20, 1),
            'min': round(10 + random.random() * 2, 1),
            'system': round(120.5 + random.random() * 20, 1),
        },
        'ob': {'maxTouchPoints': 5, 'touchEvent': True, 'touchStart': True},
        'incognito': {
            'getDirectoryExist': 0, 'getDirectoryIncognito': 0, 'maxTouchPointsExist': 1,
            'indexedDBIncognito': 0, 'openDatabaseExist': 0, 'openDatabaseIncognito': 0,
            'localStorageExist': 1, 'localStorageIncognito': 0, 'promiseExist': 1,
            'promiseAllSettledExist': 1, 'queryUsageAndQuotaIncognito': 0,
            'webkitRequestFileSystemIncognito': 0, 'serviceWorkerExist': 1,
            'indexedDBExist': 1, 'browserName': 'Chrome',
        },
        't': now_cst.strftime('%a %b %d %Y %H:%M:%S GMT+0800 (GMT+08:00)'),
        'collectTime': random.randint(50, 130),
    }
    data_b64 = base64.b64encode(json.dumps(env, separators=(',', ':')).encode('utf-8')).decode('ascii')
    return json.dumps({
        'appId': 'default', 'organization': _SM_ORGANIZATION,
        'ep': ep, 'data': data_b64,
        'os': 'web', 'encode': 1, 'compress': 0,
    }, separators=(',', ':'))


def fetch_device_id():
    url = 'https://slw.h5cmpassport.com:9090/deviceprofile/v4'
    headers = {
        'User-Agent': random.choice(market_ua_pool),
        'Content-Type': 'application/json;charset=UTF-8',
        'Origin': 'https://m.mcloud.139.com',
        'Referer': 'https://m.mcloud.139.com/portal/mobilecloud/index.html?path=newsignin',
    }
    payload_str = _generate_device_profile()
    try:
        time.sleep(random.uniform(0.5, 1.5))
        resp = requests.post(url, data=payload_str, headers=headers, timeout=15)
        result = resp.json()
        if result.get('code') == 1100 and result.get('detail', {}).get('deviceId'):
            device_id = 'B' + result['detail']['deviceId']
            return device_id
        else:
            print(f'获取deviceId失败: {result}')
    except Exception as e:
        print(f'获取deviceId异常: {e}')
    return None


# ==================== 滑块验证码识别 ====================
def identify_slide_offset(puzzle_b64, picture_b64):
    try:
        resp = requests.post(SOLVE_API, json={
            'puzzle': puzzle_b64,
            'picture': picture_b64,
        }, headers={'Content-Type': 'application/json'}, timeout=30)
        result = resp.json()
        if result.get('code') == 0 and result.get('data'):
            offset = result['data'].get('offset')
            confidence = result['data'].get('confidence', 0)
            method = result['data'].get('method', '')
            if offset is not None:
                print(f'API识别偏移量: {offset} (置信度={confidence}, 方法={method})')
                return int(offset)
        print(f'API识别失败: {result.get("message", result)}')
        return None
    except Exception as e:
        print(f'API识别异常: {e}')
        return None


# ==================== 通知服务 ====================
def load_send():
    cur_path = path.abspath(path.dirname(__file__))
    notify_file = cur_path + "/notify.py"
    if path.exists(notify_file):
        try:
            from notify import send
            print("加载通知服务成功！")
            return send
        except ImportError:
            print("加载通知服务失败~")
    else:
        print("加载通知服务失败~")
    return False


# ==================== 主类 ====================
class Exchange:
    def __init__(self, cookie, target_prize_ids=None):
        try:
            self.target_prize_ids = target_prize_ids or []
            self.client_version = CLIENT_VERSION
            self.market_base_url = BASE_M
            self.market_source_id = SOURCE_ID
            self.sso_token = None
            self.user_domain_id = ''
            self.market_device_id = fetch_device_id()
            if not self.market_device_id:
                self.log('动态获取deviceId失败')
                self.market_device_id = ''
            self.market_headers = {}
            self.market_cookies = {}
            self.session = requests.Session()
            self.user_log_lines = []
            self.timestamp = str(int(round(time.time() * 1000)))
            self.cookies = {'sensors_stay_time': self.timestamp}

            parts = cookie.split("#")
            if len(parts) < 2:
                raise ValueError(f"变量值格式错误: {cookie}")

            self.Authorization = parts[0]
            self.account = parts[1]

            # 从账号第3段提取商品ID
            if len(parts) >= 3 and parts[2].strip():
                self.target_prize_ids = [pid.strip() for pid in parts[2].split(',') if pid.strip()]

            # 手机号脱敏
            if len(self.account) >= 11:
                self.encrypt_account = self.account[:3] + "****" + self.account[-4:]
            else:
                self.encrypt_account = self.account

            self.jwtHeaders = {
                'User-Agent': ua,
                'Accept': '*/*',
                'Host': 'caiyun.feixin.10086.cn:7071',
            }
        except Exception as e:
            print(f"{e}")
            self.Authorization = None

    def log(self, content):
        print(content)
        self.user_log_lines.append(content)

    @staticmethod
    def catch_errors(func):
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                err_str = f"错误: {str(e)}"
                print(err_str)
                self.user_log_lines.append(err_str)
            return None
        return wrapper

    def sleep(self, min_s=0.5, max_s=1.5):
        time.sleep(random.uniform(min_s, max_s))

    def send_request(self, url, headers=None, cookies=None, data=None, json_data=None, params=None,
                     method='GET', retries=3):
        request_headers = dict(headers or {})
        request_cookies = dict(cookies or {})
        if json_data is not None:
            request_args = {'json': json_data}
        elif isinstance(data, dict):
            request_args = {'json': data}
        else:
            request_args = {'data': data}

        for attempt in range(retries):
            try:
                response = self.session.request(method, url, params=params, headers=request_headers or None,
                                                cookies=request_cookies or None, **request_args)
                response.raise_for_status()
                return response
            except (requests.RequestException, ConnectionError, TimeoutError) as e:
                print(f"请求异常: {e}")
                if attempt >= retries - 1:
                    return None
                time.sleep(1)

    def request_json(self, url, headers=None, cookies=None, data=None, json_data=None, params=None,
                     method='GET', retries=3):
        response = self.send_request(url, headers=headers, cookies=cookies, data=data, json_data=json_data,
                                     params=params, method=method, retries=retries)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError as e:
            self.log(f'响应解析失败: {e}')
            return None

    @staticmethod
    def extract_user_domain_id(jwt_token):
        try:
            payload = jwt_token.split('.')[1]
            payload += '=' * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload).decode())
            sub = data.get('sub', '')
            if isinstance(sub, str):
                sub = json.loads(sub)
            return sub.get('userDomainId', '')
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ''

    def build_market_context(self, jwt_token):
        self.user_domain_id = self.extract_user_domain_id(jwt_token)
        self.market_headers = {
            'User-Agent': random.choice(market_ua_pool),
            'Accept': '*/*',
            'jwtToken': jwt_token,
            'X-Requested-With': 'com.chinamobile.mcloud',
            'Referer': self.build_market_page_url(),
        }
        self.market_cookies = {'jwtToken': jwt_token}
        if self.user_domain_id:
            self.market_cookies['userDomainId'] = self.user_domain_id
        self.seed_market_device_cookie()

    def get_market_device_id(self):
        if self.market_device_id:
            return self.market_device_id if self.market_device_id.startswith('B') else f'B{self.market_device_id}'
        for cookie in self.session.cookies:
            if cookie.name.startswith('.thumbcache_') and cookie.value:
                cookie_value = unquote(cookie.value)
                return cookie_value if cookie_value.startswith('B') else f'B{cookie_value}'
        return ''

    def seed_market_device_cookie(self):
        device_id = self.market_device_id
        if not device_id:
            return
        cookie_value = device_id[1:] if device_id.startswith('B') else device_id
        if any(cookie.name.startswith('.thumbcache_') and unquote(cookie.value) == cookie_value
               for cookie in self.session.cookies):
            return
        self.session.cookies.set(f'.thumbcache_{self.account}', cookie_value,
                                 domain='m.mcloud.139.com', path='/')

    def build_market_page_url(self, source_id=None):
        current_source_id = source_id or self.market_source_id
        return (f'{self.market_base_url}/portal/mobilecloud/index.html?path=newsignin'
                f'&sourceid={current_source_id}&enableShare=1&token={self.sso_token or ""}'
                f'&targetSourceId={TARGET_SOURCE_ID}')

    def build_signin_headers(self, extra=None):
        """构建签到中心请求头"""
        headers = {
            'jwtToken': self.market_headers.get('jwtToken', ''),
            'deviceid': self.get_market_device_id(),
            'activityid': MARKET_NAME,
            'appversion': f'{self.client_version}.0',
            'cache-control': 'no-cache',
            'showloading': 'true',
            'content-type': 'application/json;charset=UTF-8',
            'accept': '*/*',
            'referer': self.build_market_page_url(),
            'user-agent': random.choice(market_ua_pool),
            'x-requested-with': 'com.chinamobile.mcloud',
            'accept-language': 'zh,zh-CN;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        if extra:
            headers.update(extra)
        return headers

    def request_market_json(self, url, params=None, headers=None, data=None, method='GET', retries=3):
        """请求市场接口"""
        req_headers = headers or self.build_signin_headers()
        return self.request_json(url, headers=req_headers, params=params, data=data, method=method, retries=retries)

    # ==================== SSO获取token ====================
    def sso(self):
        sso_url = 'https://orches.yun.139.com/orchestration/auth-rebuild/token/v1.0/querySpecToken'
        sso_headers = {
            'Authorization': self.Authorization,
            'User-Agent': ua,
            'Content-Type': 'application/json',
            'Accept': '*/*',
            'Host': 'orches.yun.139.com',
        }
        sso_payload = {"account": self.account, "toSourceId": "001005"}
        sso_data = self.request_json(sso_url, headers=sso_headers, data=sso_payload, method='POST')
        if not sso_data:
            self.log('刷新Token失败: 接口无响应')
            return None
        if sso_data.get('success'):
            refresh_token = sso_data['data']['token']
            self.sso_token = refresh_token
            return refresh_token
        else:
            self.log(f"刷新Token失败: {sso_data.get('message', '未知错误')}")
            return None

    # ==================== JWT认证 ====================
    @catch_errors
    def jwt(self):
        token = self.sso()
        if token is None:
            self.log('ck可能失效了')
            return False

        jwt_url = f"https://caiyun.feixin.10086.cn:7071/portal/auth/tyrzLogin.action?ssoToken={token}"
        jwt_data = self.request_json(jwt_url, headers=self.jwtHeaders, method='POST')
        if not jwt_data:
            self.log('JWT获取失败: 接口无响应')
            return False
        if jwt_data.get('code') != 0:
            self.log(f"JWT获取失败: {jwt_data.get('msg', '未知错误')}")
            return False

        jwt_token = jwt_data['result']['token']
        self.jwtHeaders['jwtToken'] = jwt_token
        self.cookies['jwtToken'] = jwt_token
        self.build_market_context(jwt_token)
        self.log('JWT获取成功')
        return True

    # ==================== 获取商品列表 ====================
    @catch_errors
    def get_exchange_list(self):
        """获取兑换商品列表"""
        url = f'{self.market_base_url}/ycloud/signin/page/exchangeList'
        params = {
            'client': 'app',
            'clientVersion': self.client_version,
        }
        data = self.request_market_json(url, params=params)
        if not data:
            self.log('获取商品列表失败: 接口无响应')
            return []

        if data.get('code') != 0:
            self.log(f'获取商品列表失败: {data.get("msg", "未知错误")}')
            return []

        result = data.get('result', {})
        all_items = []

        for group_key, items in result.items():
            if isinstance(items, list):
                for item in items:
                    item['_group'] = group_key
                    all_items.append(item)

        self.log(f'获取商品列表成功: 共{len(all_items)}个商品')
        return all_items

    # ==================== 获取滑块验证码 ====================
    @catch_errors
    def get_slide(self):
        url = f'{self.market_base_url}/ycloud/auth-service/slide/getSlide'
        headers = self.build_signin_headers({
            'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
        })
        data = self.request_market_json(url, headers=headers, method='POST', data={})
        if not data:
            self.log('获取滑块验证码失败: 接口无响应')
            return None

        if data.get('code') != 0:
            self.log(f'获取滑块验证码失败: {data.get("msg", "未知错误")}')
            return None

        result = data.get('result', {})
        puzzle_b64 = result.get('puzzle', '')
        picture_b64 = result.get('picture', '')

        if not puzzle_b64 or not picture_b64:
            self.log('获取滑块验证码失败: 图片数据为空')
            return None

        self.log('获取滑块验证码成功，开始识别偏移量...')
        offset = identify_slide_offset(puzzle_b64, picture_b64)
        return offset

    # ==================== 兑换商品 ====================
    @catch_errors
    def exchange_prize(self, prize_id, prize_name=''):
        """兑换指定商品"""
        self.log(f'开始兑换商品: {prize_name or prize_id}')

        # 尝试获取滑块验证码偏移量（最多3次）
        offset = None
        for slide_attempt in range(3):
            offset = self.get_slide()
            if offset is not None:
                break
            self.log(f'第{slide_attempt + 1}次滑块识别失败，重新获取...')
            time.sleep(1)

        if offset is None:
            self.log('滑块验证码识别失败，跳过兑换')
            return False

        # 添加随机偏移，模拟人工操作
        final_offset = offset + random.randint(-3, 3)
        self.log(f'最终偏移量: {final_offset}')

        url = f'{self.market_base_url}/ycloud/signin/page/exchangeV2'
        params = {
            'prizeId': prize_id,
            'client': 'app',
            'clientVersion': self.client_version,
            'puzzleOffset': final_offset,
            'smsCode': '',
        }

        data = self.request_market_json(url, params=params)
        if not data:
            self.log('兑换失败: 接口无响应')
            return False

        if data.get('code') == 0:
            result = data.get('result', {})
            self.log(f'兑换成功! 商品: {result.get("prizeName", prize_name)}')
            expire_time = result.get('expireTime', '')
            if expire_time:
                self.log(f'过期时间: {expire_time}')
            return True
        else:
            msg = data.get('msg', '未知错误')
            self.log(f'兑换失败: {msg}')
            # 如果是验证码错误，重试
            if '验证' in msg or '滑块' in msg or 'puzzle' in msg.lower():
                self.log('验证码验证失败，尝试重新识别...')
                return self._retry_exchange_with_slide(prize_id, prize_name)
            return False

    def _retry_exchange_with_slide(self, prize_id, prize_name='', max_retries=3):
        """重试兑换"""
        for attempt in range(max_retries):
            self.sleep(1, 2)
            offset = self.get_slide()
            if offset is None:
                offset = random.randint(150, 350)

            final_offset = offset + random.randint(-3, 3)
            self.log(f'重试第{attempt + 1}次，偏移量: {final_offset}')

            url = f'{self.market_base_url}/ycloud/signin/page/exchangeV2'
            params = {
                'prizeId': prize_id,
                'client': 'app',
                'clientVersion': self.client_version,
                'puzzleOffset': final_offset,
                'smsCode': '',
            }

            data = self.request_market_json(url, params=params)
            if not data:
                continue

            if data.get('code') == 0:
                result = data.get('result', {})
                self.log(f'兑换成功! 商品: {result.get("prizeName", prize_name)}')
                return True
            else:
                msg = data.get('msg', '未知错误')
                self.log(f'重试第{attempt + 1}次失败: {msg}')
                if '验证' not in msg and '滑块' not in msg and 'puzzle' not in msg.lower():
                    break

        self.log(f'重试{max_retries}次后仍失败')
        return False

    # ==================== 查询已有商品 ====================
    @catch_errors
    def query_received_prizes(self):
        """查询已兑换可领取的商品"""
        url = f'https://caiyun.feixin.10086.cn/market/prizeApi/checkPrize/getUserPrizeLogPage'
        params = {
            'currPage': '1',
            'pageSize': '15',
            '_': self.timestamp,
        }
        data = self.request_json(url, headers=self.jwtHeaders, cookies=self.cookies, params=params)
        if not data:
            self.log('查询已有商品失败: 接口无响应')
            return []

        result = data.get('result', {}).get('result') or []
        pending = []
        received = []
        for item in result:
            prize_name = item.get('prizeName', '')
            flag = item.get('flag')
            if flag == 1:
                pending.append(prize_name)
            else:
                received.append(prize_name)

        if pending:
            self.log(f'待领取商品: {", ".join(pending)}')
        else:
            self.log('暂无待领取商品')

        return pending

    # ==================== 查询云朵信息 ====================
    @catch_errors
    def get_cloud_info(self):
        """查询云朵余额"""
        url = f'{self.market_base_url}/ycloud/signin/page/infoV3'
        params = {'client': 'app'}
        data = self.request_market_json(url, params=params)
        if not data or data.get('code') != 0:
            self.log('查询云朵失败')
            return 0
        result = data.get('result', {})
        total = result.get('total', 0)
        to_receive = result.get('toReceive', 0)
        self.log(f'当前云朵: {total}, 待领取: {to_receive}')
        return total

    # ==================== 主运行流程 ====================
    @catch_errors
    def run(self):
        if not self.jwt():
            global err_accounts
            err_accounts += f'{self.encrypt_account}\n'
            return

        self.log(f'\n===== 开始抢兑流程 =====')

        # 1. 查询云朵余额
        self.log(f'\n--- 查询云朵余额 ---')
        cloud_total = self.get_cloud_info()
        self.sleep()

        # 2. 获取商品列表
        items = self.get_exchange_list()
        if not items:
            self.log('商品列表为空，退出')
            return

        # 3. 确定要兑换的商品
        target_items = []
        if self.target_prize_ids:
            for pid in self.target_prize_ids:
                for item in items:
                    if str(item.get('prizeId')) == str(pid):
                        target_items.append(item)
                        break
                else:
                    self.log(f'商品ID {pid} 未在列表中找到')
        else:
            self.log('\n未指定商品ID，跳过兑换（仅查询商品列表和已有商品）')

        if not target_items:
            self.log('没有可兑换的商品')
        else:
            # 4. 执行兑换
            self.log(f'\n--- 开始兑换 ---')
            success_count = 0
            for item in target_items:
                pid = item.get('prizeId', '')
                name = item.get('prizeName', '')
                daily_remain = item.get('dailyRemainderCount', 0)

                if daily_remain <= 0:
                    self.log(f'跳过 {name} (已抢光)')
                    continue

                self.log(f'\n尝试兑换: {name} (ID:{pid})')
                if self.exchange_prize(pid, name):
                    success_count += 1
                self.sleep(2, 4)

            self.log(f'\n兑换完成: 成功{success_count}个')

        # 5. 查询已有商品
        self.log(f'\n--- 查询已有商品 ---')
        self.query_received_prizes()
        self.sleep()

        # 汇总日志
        global all_logs, user_summary
        user_log_str = "\n".join(self.user_log_lines)
        all_logs += f"用户【{self.encrypt_account}】日志:\n{user_log_str}\n\n"
        user_summary += f"用户【{self.encrypt_account}】: 云朵{cloud_total}\n"


# ==================== 入口 ====================
if __name__ == "__main__":
    env_name = 'yunpan'
    token = os.getenv(env_name)

    if not token:
        print(f"未获取到ck变量：请检查变量 {env_name} 是否填写")
        exit(0)

    cookies = re.split(r'[&@\n]', token)
    cookies = [c.strip() for c in cookies if c.strip()]

    print(f"移动云盘抢兑脚本 v{SCRIPT_VERSION}")
    print(f"共获取到{len(cookies)}个账号")

    for i, account_info in enumerate(cookies, start=1):
        print(f"\n======== 第 {i} 个账号 ========")
        ex = Exchange(account_info)

        if not ex.Authorization:
            print(f"账号 {i} 无效，跳过执行")
            continue

        ex.session.cookies.clear()
        ex.run()
        print("\n准备进行下一个账号")
        time.sleep(random.uniform(1, 3))

    # 构建推送消息
    msg = ""
    if err_accounts:
        msg += f"失效账号:\n{err_accounts}\n"
    msg += f"抢兑详情:\n{all_logs}\n"
    msg += f"账号汇总:\n{user_summary}"

    print("\n================ 运行总结 ================")
    if err_accounts:
        print(f"失效账号:\n{err_accounts}")
    if user_summary:
        print(f"账号汇总:\n{user_summary}")

    # 替换特殊字符防止推送报错
    msg = msg.replace('-', ' ').replace('.', ' ').replace('!', '！')
    msg = msg.replace('(', '（').replace(')', '）').replace('_', ' ')
    msg = msg.replace('=', ' ').replace('~', ' ').replace('{', ' ').replace('}', ' ')
    msg = msg.replace('|', ' ')

    send = load_send()
    if send:
        try:
            send("移动云盘抢兑", msg)
        except Exception as e:
            print(f"推送通知失败: {e}")
