#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
移动云盘自动签到 v5.1.3

包含以下功能:
1. 每日自动签到 (签到/抽奖/摇一摇/新版云朵领取)
2. 多账号并发支持 (环境变量 & 分隔)
3. 云朵中心新版任务自动处理 (上传/分享/AI相机/月任务补传等)
4. 临时文件智能清理与详细日志推送
5. 红包派对任务链 (签到/浏览/云机使用/安装应用/趣味答题/余额)

更新说明:

### 20260616
v5.1.3:
- 修复戳一戳API路径（/market → /ycloud），恢复戳一戳奖励。
- 修复红包派对登录404：改用独立 requests 请求，避免 session cookies 干扰。
- 放宽任务注册限制：不再要求 currstep==0，阶段1任务也能点击注册。
- 回退膨胀云朵接口：taskExpansion（非 backupTaskExpansion），修复 404。

v5.1.2:
- Token 缓存改为 data/yunpan_token_cache.json。
- 智能对比 env token 和缓存 token 的过期时间，自动选择更新的那个。
- 新增 parse_token_key / is_token_expired / is_token_expiring_soon，对齐 JS 判断逻辑。
- Authorization 刷新提前量从 24 小时调整为 5 天。

v5.1.1:
- send_request 不再重试 4xx 错误（404/403 等），避免日志刷屏。
- 红包派对登录失败日志改进，显示 HTTP 状态码。
- 每账号日志开头打印脱敏账号（前3后4）。

### 20260615
v5.1.0:
- 新增红包派对任务链：签到、浏览、云机使用、安装应用、趣味答题、余额奖励日志。
- 30GB定向流量保持手动跳过，补充答题答案与未知题重取。
- 整合 Authorization 自动刷新与 deviceId 缓存。
- 修复戳一戳API路径、签到忙重试、云朵翻倍/复活卡领取等。
- 优化分享文件响应兼容、通知任务处理流程。

### 20260502
v5.0.4:
- deviceId 生成逻辑从 Node.js 子进程改为纯 Python 实现（使用 pycryptodome）。
- 修复数美设备指纹 1902 错误，适配私有部署 API（encode=1, compress=0）。

配置说明:
变量名: yunpan
赋值方式:
1. 抓包 authTokenRefresh.do 请求头 Authorization 值。
2. 填入青龙环境变量，格式：Authorization值#手机号 (多账号用 & 分隔)。

变量名: yunpan_device_id 或 YDYP_DEVICE_ID
用途: 签到和领取云朵优先使用的 deviceId

⚠️ 依赖安装:
pip3 install requests pycryptodome

定时规则建议 (Cron):
0 0 8,16,20 * * * (每日早中晚执行)

Author: yaohuo & xiaohai
Update: 2026.06.16
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
from pathlib import Path
from urllib.parse import unquote
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

import requests

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError:
    AES = None
    pad = None

SCRIPT_VERSION = '5.1.3'

DEVICE_ID_STORAGE_FILENAME = 'yunpan_device_ids.json'
JS_CACHE_DIR = 'data'
JS_CACHE_FILE = 'yunpan_token_cache.json'

ua = 'Mozilla/5.0 (Linux; Android 11; M2012K10C Build/RP1A.200720.011; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/90.0.4430.210 Mobile Safari/537.36 MCloudApp/10.0.1'
market_ua = random.choice([
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
])

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
        'User-Agent': market_ua,
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

cloud_file_dummy_content = b'0'
cloud_file_dummy_hash = hashlib.sha256(cloud_file_dummy_content).hexdigest()

TOKEN_VALID_TIME = 21600000
TOKEN_REFRESH_ADVANCE = 24 * 60 * 60 * 1000
TOKEN_REFRESH_COOLDOWN = 864000
FIVE_DAYS_MS = 5 * 24 * 60 * 60 * 1000
REFRESH_TOKEN_AES_KEY = 'c7lXOigXahPnTViq'
AI_TOOL_ACCOUNT_AES_KEY = 'xuL97!x7GGxG%8V4'
AI_TOOL_ACCOUNT_AES_IV = '5OuCxk4XNu0NA*%x'
TOKEN_EXPIRE_SECONDS_FALLBACK = 2592000
RED_PACKET_SOURCE_ID = '001216'
RED_PACKET_VERSION = 'SYS_CONFIG_Y'
RED_PACKET_BASE_URL = 'https://cpactiv.buy.139.com/cloudphone-market'
RED_PACKET_PAGE_URL = 'https://cpactiv.buy.139.com/#/redEnvelopeParty/home?channelSrc=red-cmccapp'
RED_PACKET_APP_ID = '12345681'
RED_PACKET_SIGN_KEY = 'e10adc3949ba59abbe56e057f20f883e'
RED_PACKET_CHANNEL_SRC = 'red-cmccapp'
RED_PACKET_BROWSE_TASKS = {'NOVICE_2', 'NOVICE_3', 'MONTHLY_1'}
RED_PACKET_DIRECT_TASKS = {'MONTHLY_4', 'MONTHLY_5'}
RED_PACKET_KNOWN_ANSWERS = {
    '如何查看并更新移动云手机客户端最新版本？': '进入"我的"-点击"关于云手机"-点击"检查新版本"',
    '移动云手机可领取定向流量，每月赠送的定向流量是（  ）。': '30GB',
    '移动云手机端内订购的专业版分辨率已升级到1080P，该说法是否正确？': '正确',
    '移动云手机支持视频录制，该说法是否正确？': '正确',
    '云手机支持通过手机、平板、电脑等多种终端设备登录使用，该说法是否正确？': '正确',
    '使用中国移动号码登录移动云手机，是否支持手机号一键登录？': '支持',
    '只有中国移动运营商号码能使用移动云手机？': '不正确',
    '移动云手机是否需要充电使用？': '不需要',
    '移动云手机支持截图，该说法是否正确？': '正确',
    '移动云手机AI灵犀助手已接入DeepSeek，是否正确？': '正确',
    '移动云手机内支持画面清晰度切换，该说法是否正确？': '正确',
    '移动云手机支持连接蓝牙使用吗？': '不支持',
    '在云手机内安装游戏应用是否占本地手机存储空间？': '否，不占本地空间',
    '如何更换云机内的桌面主题或壁纸？': '云机内-【设置】-壁纸/个性主题',
    '如何将云手机里的应用添加至本地手机桌面？': '云手机桌面-长按应用-发送图标到本地',
}
RED_PACKET_MANUAL_TASKS = {
    'NOVICE_1': '需跳转领取定向流量',
}

err_accounts = ''
all_logs = ''
user_amount = ''
GLOBAL_DEBUG = False


def current_millis():
    return int(time.time() * 1000)


def normalize_authorization(token):
    token = (token or '').strip()
    if token and not token.startswith('Basic '):
        return f'Basic {token}'
    return token


def random_string(length=16):
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(random.choice(chars) for _ in range(length))


def generate_uuid():
    return ''.join([
        random_string(8), '-',
        random_string(4), '-4',
        random_string(3), '-',
        random.choice('89ab'), random_string(3), '-',
        random_string(12)
    ])


def aes_encrypt(data, key):
    if AES is None or pad is None:
        raise ImportError('未安装 pycryptodome，无法执行加密')
    key_bytes = key.encode('utf-8')
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    cipher = AES.new(key_bytes, AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(data.encode('utf-8'), AES.block_size))
    return base64.b64encode(encrypted).decode('utf-8')


def extract_raw_token(token, account):
    token = normalize_authorization(token)
    if token.startswith('Basic '):
        token = token[6:]
    try:
        decoded = base64.b64decode(token).decode('utf-8')
        parts = decoded.split(':')
        if len(parts) >= 3:
            return parts[2]
    except Exception:
        pass
    return token


def parse_token_key(authorization):
    """从 token 解析 type / phone / expireAt（对齐 JS parseTokenKey）
    
    token 格式（Base64解码后）: mobile:133xxxx:xxx|xxx|xxx|过期时间戳
    返回值: {'type': 'mobile', 'phone': '133xxxx', 'expireAt': 1234567890000}
    """
    try:
        token = normalize_authorization(authorization)
        if not token:
            return {'type': 'unknown', 'phone': '', 'expireAt': 0}
        if token.startswith('Basic '):
            token = token[6:]
        decoded = base64.b64decode(token).decode('utf-8')
        if not decoded:
            return {'type': 'unknown', 'phone': '', 'expireAt': 0}
        parts = decoded.split(':')
        token_type = parts[0] if parts else 'unknown'
        phone = parts[1] if len(parts) > 1 else ''
        pipe_parts = decoded.split('|')
        expire_at = int(pipe_parts[3]) if len(pipe_parts) > 3 and pipe_parts[3].isdigit() else 0
        return {'type': token_type, 'phone': phone, 'expireAt': expire_at}
    except Exception:
        return {'type': 'unknown', 'phone': '', 'expireAt': 0}


def is_token_expired(expire_at):
    """token 是否已过期（对齐 JS isTokenExpired）"""
    if not expire_at:
        return False
    return expire_at <= current_millis()


def is_token_expiring_soon(expire_at):
    """token 是否在 5 天内过期（对齐 JS isTokenExpiringSoon）"""
    if not expire_at:
        return False
    diff = expire_at - current_millis()
    return 0 < diff <= FIVE_DAYS_MS


def build_authorization(account, raw_token):
    return f"Basic {base64.b64encode(f'mobile:{account}:{raw_token}'.encode()).decode()}"


def parse_expire_time_to_millis(expire_time):
    try:
        expire_seconds = int(float(expire_time))
    except (TypeError, ValueError):
        expire_seconds = TOKEN_EXPIRE_SECONDS_FALLBACK
    if expire_seconds <= 0:
        expire_seconds = TOKEN_EXPIRE_SECONDS_FALLBACK
    return current_millis() + expire_seconds * 1000


def get_env_device_id():
    return (os.getenv('yunpan_device_id') or os.getenv('YDYP_DEVICE_ID') or '').strip()


def normalize_market_device_input(device_id):
    raw = (device_id or '').strip().strip('"').strip("'")
    if not raw:
        return '', ''
    match = re.search(r'(?:^|;\s*)\.thumbcache_[^=;]*=([^;]+)', raw)
    if match:
        raw = match.group(1).strip()
    elif raw.lower().startswith('deviceid='):
        raw = raw.split('=', 1)[1].strip()
    raw = raw.strip().strip('"').strip("'")
    header_value = unquote(raw)
    cookie_value = raw if raw != header_value else (header_value[1:] if header_value.startswith('B') else header_value)
    return header_value, cookie_value


def build_x_device_info(device_id, net_type='wifi', terminal_type='8',
                        version='13.0.0', brand='Android', model='MI 8', system='Android 10'):
    return f"{net_type}||{terminal_type}|{version}|{brand}|{model}|{device_id}||{system.lower()}|||||"


def get_storage_base_dir():
    custom_storage_dir = (os.environ.get('yunpan_storage_dir') or os.environ.get('YDYP_STORAGE_DIR') or '').strip()
    if custom_storage_dir:
        base_dir = Path(custom_storage_dir)
    else:
        base_dir = Path(path.abspath(path.dirname(__file__)))
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def get_storage_file_path(filename):
    return get_storage_base_dir() / filename


def get_js_cache_path():
    """JS 的 data/yunpan_token_cache.json 路径，共用缓存"""
    try:
        return Path(os.getcwd()) / JS_CACHE_DIR / JS_CACHE_FILE
    except Exception:
        return get_storage_base_dir() / JS_CACHE_FILE


def load_js_cache():
    """读取 JS 格式的 token 缓存文件"""
    cache_path = get_js_cache_path()
    try:
        if cache_path.exists():
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault('accounts', {})
                    return data
    except Exception:
        pass
    return {'accounts': {}}


def save_js_cache(data):
    """写入 JS 格式的 token 缓存文件"""
    cache_path = get_js_cache_path()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'保存JS缓存失败: {e}')


def get_token_info(account):
    """读取 token 缓存（JS 格式）"""
    js_cache = load_js_cache()
    entry = (js_cache.get('accounts') or {}).get(account, {})
    if entry and entry.get('authorization'):
        return {
            'token': entry['authorization'],
            'expiresAt': entry.get('expireAt', 0),
            'lastRefreshAt': entry.get('updatedAt', 0),
        }
    return {}


def update_cache_authorization(account, authorization):
    """写入 JS 格式的 token 缓存（对齐 JS updateCacheAuthorization），保留已有扩展字段"""
    parsed = parse_token_key(authorization)
    cache = load_js_cache()
    accounts = cache.setdefault('accounts', {})
    existing = accounts.get(account, {})
    existing.update({
        'authorization': normalize_authorization(authorization),
        'phone': account,
        'type': parsed.get('type', 'unknown'),
        'expireAt': parsed.get('expireAt', 0) or int(existing.get('expireAt', 0)),
        'updatedAt': current_millis(),
    })
    accounts[account] = existing
    save_js_cache(cache)


def get_js_cached_token(account):
    """读取 JS 缓存的 authorization（通用接口）"""
    entry = get_token_info(account)
    return normalize_authorization(entry.get('token', ''))


def get_device_id_storage_path():
    return get_storage_file_path(DEVICE_ID_STORAGE_FILENAME)


def load_device_id_storage():
    storage_path = get_device_id_storage_path()
    if not storage_path.exists():
        return {}
    try:
        with open(storage_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_device_id(device_id, account):
    storage_path = get_device_id_storage_path()
    data = load_device_id_storage()
    account_data = data.get(account, {})
    normalized_device_id, _ = normalize_market_device_input(device_id)
    account_data['deviceId'] = normalized_device_id or device_id
    data[account] = account_data
    try:
        with open(storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'保存deviceId失败: {e}')


def save_token_info(account, token, expires_at, last_refresh_at):
    """写入 token 到 JS 格式缓存"""
    update_cache_authorization(account, token)


def ensure_account_storage_entry(account, token=''):
    """确保账号在 deviceId 缓存中有记录"""
    storage_path = get_device_id_storage_path()
    data = load_device_id_storage()
    account_data = data.get(account)
    if not isinstance(account_data, dict):
        account_data = {}
    changed = False
    if 'deviceId' not in account_data:
        account_data['deviceId'] = ''
        changed = True
    if changed or account not in data:
        data[account] = account_data
        try:
            with open(storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'初始化账号缓存失败: {e}')


def get_device_id(account):
    data = load_device_id_storage()
    account_data = data.get(account, {})
    return account_data.get('deviceId', '')


def print_startup_info(account_count):
    print(f"移动云盘自动签到 v{SCRIPT_VERSION}")
    print(f"移动云盘共获取到{account_count}个账号")


def print_device_id_notice():
    if get_env_device_id():
        print("已检测到 yunpan_device_id，将优先使用环境变量 deviceId")
        return
    cached_data = load_device_id_storage()
    cached_accounts = [account for account, info in cached_data.items()
                       if isinstance(info, dict) and info.get('deviceId')]
    if cached_accounts:
        print(f"已发现本地缓存 deviceId {len(cached_accounts)} 个账号；新账号将自动写入 yunpan_device_ids.json")
        return
    print("未检测到 yunpan_device_id；脚本将优先读取本地缓存，缓存为空时自动生成并保存 deviceId")


def print_storage_path_notice():
    print(f"缓存文件目录: {get_storage_base_dir()}")
    print(f"deviceId缓存文件: {get_device_id_storage_path()}")
    print(f"Token缓存文件（与JS共用）: {get_js_cache_path()}")


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


class YP:
    def __init__(self, cookie):
        try:
            self.notebook_id = None
            self.note_token = None
            self.note_auth = None
            self.auth_token = None
            self.click_num = 15
            self.draw = 1
            self.client_version = '13.0.0'
            self.market_base_url = 'https://m.mcloud.139.com'
            self.market_source_id = '1097'
            self.sso_token = None
            self.user_domain_id = ''
            self.market_device_id = ''
            self.market_device_cookie_value = ''
            self.market_x_device_info = ''
            self.market_device_from_env = False
            self.market_device_is_placeholder = False
            self.market_headers = {}
            self.market_cookies = {}
            self.red_packet_token = ''
            self.red_packet_mobile = ''
            self.session = requests.Session()
            self.user_log_lines = []

            self.timestamp = str(int(round(time.time() * 1000)))
            self.cookies = {'sensors_stay_time': self.timestamp}

            parts = cookie.split("#")
            if len(parts) < 2:
                raise ValueError(f"⚠️ 变量值格式错误: {cookie}")

            self.Authorization = normalize_authorization(parts[0])
            self.account = parts[1].strip()
            self.refresh_capable = AES is not None and pad is not None
            self.load_persisted_authorization()
            self.load_or_create_market_device_profile()
            self.auth_token = extract_raw_token(self.Authorization, self.account)

            if len(self.account) >= 11:
                self.encrypt_account = self.account[:3] + "****" + self.account[-4:]
            else:
                self.encrypt_account = self.account

            self.fruit_url = 'https://happy.mail.10086.cn/jsp/cn/garden/'

            self.jwtHeaders = {
                'User-Agent': ua,
                'Accept': '*/*',
                'Host': 'caiyun.feixin.10086.cn:7071',
            }
            self.treeHeaders = {
                'Host': 'happy.mail.10086.cn',
                'Accept': 'application/json, text/plain, */*',
                'User-Agent': ua,
                'Referer': 'https://happy.mail.10086.cn/jsp/cn/garden/wap/index.html?sourceid=1003',
                'Cookie': '',
            }

        except Exception as e:
            print(f"{e}")
            self.Authorization = None

    def get_storage_record(self):
        """读取 token 缓存（与 1.js 共用的 JS 格式）"""
        js_cache = load_js_cache()
        entry = (js_cache.get('accounts') or {}).get(self.account, {})
        if entry and entry.get('authorization'):
            return {
                'token': entry['authorization'],
                'expiresAt': entry.get('expireAt', 0),
                'lastRefreshAt': entry.get('updatedAt', 0),
                'userDomainId': entry.get('userDomainId', ''),
                'redPacketToken': entry.get('redPacketToken', ''),
                'redPacketMobile': entry.get('redPacketMobile', ''),
            }
        return {}

    def load_persisted_authorization(self):
        """对齐 JS applyCacheAuthorization：智能对比 env token 和缓存 token，选择更新/更有效的那个"""
        env_parsed = parse_token_key(self.Authorization)
        cached = self.get_storage_record()
        cached_token = normalize_authorization(cached.get('token', ''))

        if not cached_token:
            return

        cache_parsed = parse_token_key(cached_token)
        if cache_parsed.get('phone') and cache_parsed['phone'] != self.account:
            cache_parsed['phone'] = self.account

        if cache_parsed.get('phone') and cache_parsed['phone'] != self.account:
            return

        env_expire = env_parsed.get('expireAt', 0) or int(cached.get('expiresAt') or 0)
        cache_expire = cache_parsed.get('expireAt', 0) or int(cached.get('expiresAt') or 0)

        if is_token_expired(env_expire) and not is_token_expired(cache_expire):
            self.Authorization = cached_token
            self.log(f'-Token缓存命中: env已过期，使用缓存Token（过期: {self._format_expire(cache_expire)}）')
            return

        if cache_expire > env_expire:
            self.Authorization = cached_token
            self.log(f'-Token缓存命中: 缓存Token更新（env: {self._format_expire(env_expire)}, cache: {self._format_expire(cache_expire)}）')
            return

        if not env_expire and not cache_expire:
            self.Authorization = cached_token
            self.log('-Token缓存命中: 使用缓存Token（env/cache均解析不出过期时间）')
            return

        if env_expire:
            save_token_info(self.account, self.Authorization, env_expire, current_millis())

    @staticmethod
    def _format_expire(expire_at):
        if not expire_at:
            return '未知'
        try:
            return datetime.fromtimestamp(expire_at / 1000).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return str(expire_at)

    def load_or_create_market_device_profile(self):
        ensure_account_storage_entry(self.account, self.Authorization)

        fetched_id = fetch_device_id()
        if fetched_id:
            self.market_device_id = fetched_id
            self.market_device_cookie_value = fetched_id
            self.market_x_device_info = build_x_device_info(fetched_id)
            self.market_device_from_env = False
            self.market_device_is_placeholder = False
            save_device_id(fetched_id, self.account)
            return

        env_device_id = get_env_device_id()
        if env_device_id:
            device_id, cookie_value = normalize_market_device_input(env_device_id)
            self.market_device_id = device_id
            self.market_device_cookie_value = cookie_value
            self.market_x_device_info = build_x_device_info(device_id)
            self.market_device_from_env = True
            self.market_device_is_placeholder = False
            return

        stored_device_id = get_device_id(self.account)
        if stored_device_id:
            device_id, cookie_value = normalize_market_device_input(stored_device_id)
            self.market_device_id = device_id
            self.market_device_cookie_value = cookie_value
            self.market_x_device_info = build_x_device_info(device_id)
            self.market_device_is_placeholder = False
            return

        self.market_device_id = fetched_id or ''
        if not self.market_device_id:
            self.market_device_id = str(uuid.uuid4()).replace('-', '')
            self.market_device_is_placeholder = True
        _, self.market_device_cookie_value = normalize_market_device_input(self.market_device_id)
        self.market_x_device_info = build_x_device_info(self.market_device_id)
        if self.market_device_id:
            save_device_id(self.market_device_id, self.account)

    def save_authorization_record(self, token=None, user_domain_id=None, refreshed=False, expires_at=None):
        token = normalize_authorization(token or self.Authorization)
        update_cache_authorization(self.account, token)
        uid = user_domain_id or self.user_domain_id
        if uid:
            cache = load_js_cache()
            entry = cache.setdefault('accounts', {}).setdefault(self.account, {})
            entry['userDomainId'] = uid
            save_js_cache(cache)

    def should_refresh_authorization(self, force=False):
        """对齐 JS maybeRefreshAuthorization：从 token 自身解析过期时间，5天内过期就刷新"""
        if force:
            return True, '强制刷新'

        parsed = parse_token_key(self.Authorization)
        expire_at = parsed.get('expireAt', 0)

        if not expire_at:
            stored = self.get_storage_record()
            expire_at = int(stored.get('expiresAt') or 0)

        if not expire_at:
            return True, '无法解析Token过期时间'

        if is_token_expired(expire_at):
            return True, 'Token已过期'

        if is_token_expiring_soon(expire_at):
            days = (expire_at - current_millis()) / 86400000
            return True, f'将在 {days:.1f} 天后过期'

        days = (expire_at - current_millis()) / 86400000
        return False, f'Token 状态良好（剩余 {days:.1f} 天）'

    def refresh_authorization_token(self, force=False):
        need_refresh, reason = self.should_refresh_authorization(force=force)
        if not need_refresh:
            self.log(f'-Authorization状态: {reason}')
            return True
        if not self.refresh_capable:
            self.log('-未安装 pycryptodome，跳过Authorization自动刷新')
            return True

        refresh_headers = {
            'Content-Type': 'application/json',
            'User-Agent': ua,
            'x-yun-tid': generate_uuid(),
            'Authorization': self.Authorization,
            'x-yun-api-version': 'v1',
            'x-yun-module-type': '100',
            'x-yun-op-type': '1',
            'x-yun-app-channel': '10214200',
            'x-yun-client-info': '||8||||||||||||',
            'hcy-cool-flag': '1',
        }
        stored = self.get_storage_record()
        if stored.get('userDomainId'):
            refresh_headers['x-yun-uni'] = stored['userDomainId']

        encrypted_data = aes_encrypt({'phoneNumber': self.account}, REFRESH_TOKEN_AES_KEY)
        refresh_data = self.request_json('https://user-njs.yun.139.com/user/auth/refreshToken',
                                         headers=refresh_headers,
                                         data={'data': encrypted_data},
                                         method='POST',
                                         retries=1)
        if not refresh_data:
            self.log(f'-Authorization刷新失败: 接口无响应 ({reason})')
            return True
        code = str(refresh_data.get('code', ''))
        data = refresh_data.get('data')
        success = refresh_data.get('success', False)
        is_success = (code in ('0', '00', '000', '0000')) or bool(success) or (code.startswith('0') and len(code) <= 4)
        if not is_success or not isinstance(data, dict):
            self.log(f"-Authorization刷新失败: {refresh_data.get('message') or refresh_data.get('msg', '未知错误')}")
            return True
        raw_token = data.get('token')
        if not raw_token:
            self.log('-Authorization刷新失败: 响应缺少token')
            return True
        expires_at = parse_expire_time_to_millis(data.get('expireTime'))
        self.Authorization = build_authorization(self.account, raw_token)
        self.auth_token = raw_token
        self.save_authorization_record(token=self.Authorization, refreshed=True, expires_at=expires_at)
        update_cache_authorization(self.account, self.Authorization)
        self.log(f'-Authorization自动刷新成功: {reason}')
        self.log(f"-Authorization有效期: {datetime.fromtimestamp(expires_at / 1000).strftime('%Y-%m-%d %H:%M:%S')}")
        return True

    def sync_token_storage(self):
        stored = self.get_storage_record()
        expires_at = stored.get('expiresAt', current_millis() + TOKEN_VALID_TIME)
        last_refresh_at = stored.get('lastRefreshAt', 0)
        save_token_info(self.account, self.Authorization, expires_at, last_refresh_at)
        if self.market_device_id and not self.market_device_is_placeholder:
            save_device_id(self.market_device_id, self.account)
        self.save_authorization_record(token=self.Authorization, user_domain_id=self.user_domain_id)

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

    @catch_errors
    def run(self):
        self.log(f'账号: {self.encrypt_account}')
        if self.jwt():
            self.refresh_authorization_token()
            self.signin_status()
            self.click()
            self.get_tasklist(url='sign_in_3', app_type='cloud_app')
            self.log(f'\n📰 公众号任务')
            self.wxsign()
            self.shake()
            self.surplus_num()
            self.log(f'\n🔥 热门任务')
            self.backup_cloud()
            self.complete_notice_task()
            self.log(f'\n📧 139邮箱任务')
            self.get_tasklist(url='newsign_139mail', app_type='email_app')
            self.receive()
            self.red_envelope_party()
            self.sync_token_storage()

            global all_logs
            user_log_str = "\n".join(self.user_log_lines)
            all_logs += f"用户【{self.encrypt_account}】日志:\n{user_log_str}\n\n"

        else:
            global err_accounts
            err_accounts += f'{self.encrypt_account}\n'

    @catch_errors
    def send_request(self, url, headers=None, cookies=None, data=None, json_data=None, params=None, method='GET', debug=None,
                     retries=5):

        debug = debug if debug is not None else GLOBAL_DEBUG

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
                response = self.session.request(method, url, params = params, headers = request_headers or None,
                                                cookies = request_cookies or None, **request_args)
                if 400 <= response.status_code < 500:
                    if debug:
                        print(f'\n【{url}】响应状态码: {response.status_code}')
                        print(f'响应数据:\n{response.text}')
                    return None
                response.raise_for_status()
                if debug:
                    print(f'\n【{url}】响应数据:\n{response.text}')
                return response
            except requests.HTTPError as e:
                print(f"请求异常: {e}")
                return None
            except (requests.ConnectionError, TimeoutError, ConnectionError) as e:
                print(f"请求异常: {e}")
                if attempt >= retries - 1:
                    print("达到最大重试次数。")
                    return None
                time.sleep(1)

    def request_json(self, url, headers=None, cookies=None, data=None, json_data=None, params=None, method='GET', debug=None,
                     retries=5):
        response = self.send_request(url, headers=headers, cookies=cookies, data=data, json_data=json_data,
                                     params=params, method=method, debug=debug, retries=retries)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError as e:
            self.log(f'响应解析失败: {e}')
            return None

    @staticmethod
    def get_today_sign_state(result):
        today_sign_in = result.get('todaySignIn')
        if isinstance(today_sign_in, bool):
            return today_sign_in
        for day in result.get('cal') or []:
            if day.get('t'):
                return bool(day.get('s'))
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
            'User-Agent': market_ua,
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
        self.session.cookies.set(f'.thumbcache_{self.account}', cookie_value, domain='m.mcloud.139.com', path='/')

    def build_market_page_url(self, source_id=None):
        current_source_id = source_id or self.market_source_id
        return f'{self.market_base_url}/portal/mobilecloud/index.html?path=newsignin&sourceid={current_source_id}&enableShare=1&token={self.sso_token or ""}&targetSourceId=001005'

    def build_market_headers(self, extra_headers=None, referer=None):
        headers = dict(self.market_headers)
        headers['Referer'] = referer or headers.get('Referer') or self.build_market_page_url()
        device_id = self.get_market_device_id()
        if device_id:
            headers['deviceId'] = device_id
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def build_receive_headers(self, source_id=None):
        return self.build_market_headers({
            'showLoading': 'true',
            'appVersion': f'{self.client_version}.0',
            'activityId': 'sign_in_3',
        }, referer = self.build_market_page_url(source_id))

    def request_market_json(self, url, params=None, data=None, method='GET', debug=None, retries=5, headers=None,
                            cookies=None, json_body=False):
        request_cookies = dict(self.market_cookies)
        if cookies:
            request_cookies.update(cookies)
        extra = dict(headers) if headers else {}
        if json_body and isinstance(data, dict):
            send_data = None
            request_json_data = data
        elif json_body:
            extra.setdefault('Content-Type', 'application/json;charset=UTF-8')
            send_data = json.dumps(data, ensure_ascii=False, separators=(',', ':')) if data is not None else data
            request_json_data = None
        else:
            send_data = data
            request_json_data = None
        return self.request_json(url, headers=self.build_market_headers(extra), cookies=request_cookies,
                                 data=send_data, json_data=request_json_data, params=params, method=method,
                                 debug=debug, retries=retries)

    def post_signin_journaling(self, keyword, source_id=None):
        current_source_id = source_id or self.market_source_id
        payload = f'module=uservisit&optkeyword={keyword}&sourceid={current_source_id}&marketName=sign_in_3'
        response = self.send_request(f'{self.market_base_url}/ycloud/visitlog/journaling',
                                     headers=self.build_market_headers(
                                         {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
                                         referer=self.build_market_page_url(current_source_id)
                                     ),
                                     cookies=self.market_cookies, data=payload, method='POST', retries=1)
        return response is not None

    def prepare_signin_center_session(self, for_receive=False, source_id=None):
        current_source_id = source_id or self.market_source_id
        page_url = self.build_market_page_url(current_source_id)
        self.send_request(page_url, headers = self.build_market_headers(referer = page_url),
                          cookies = self.market_cookies, retries = 1)
        for keyword in (
            'newsignin_index_pv',
            'newsignin_index_client',
            'newsignin_index_app_client',
            'newsignin_index_cookie_login',
            'newsignin_index_cookie',
            'newsignin_index_app_cookie_login',
        ):
            self.post_signin_journaling(keyword, current_source_id)
        if for_receive:
            self.post_signin_journaling('newsignin_index_receive_type', current_source_id)
        return True

    def click_task(self, task_id, key='task'):
        return self.request_market_json(f'{self.market_base_url}/ycloud/signin/task/click?key={key}&id={task_id}')

    def get_notice_status(self):
        send_data = self.request_json('https://caiyun.feixin.10086.cn/market/msgPushOn/task/status',
                                      headers = self.jwtHeaders) or {}
        if send_data.get('code') != 0:
            return {}
        return send_data.get('result', {}) or {}

    def format_notice_task_log(self, task_name, notice_status):
        if not notice_status:
            return f'-需手动完成: {task_name}'
        push_on = int(notice_status.get('pushOn') or 0)
        first_status = int(notice_status.get('firstTaskStatus') or 0)
        second_status = int(notice_status.get('secondTaskStatus') or 0)
        on_duration = int(notice_status.get('onDuaration') or 0)
        total = int(notice_status.get('total') or 31)
        if push_on != 1:
            return f'-需手动完成: {task_name} (通知未开启)'
        if second_status == 3:
            return f'-已完成: {task_name}'
        if first_status != 3:
            return f'-待领取: {task_name} (首日奖励可领取)'
        if second_status == 2:
            return f'-待领取: {task_name} (已开启{on_duration}/{total}天)'
        return f'-进行中: {task_name} (已开启{on_duration}/{total}天)'

    def build_cloud_file_headers(self):
        return {
            'x-yun-op-type': '1',
            'x-yun-sub-op-type': '100',
            'x-yun-api-version': 'v1',
            'x-yun-client-info': '6|127.0.0.1|1|12.1.0|realme|RMX5060|BCFF2BBA6881DD8E4971803C63DDB5E4|02-00-00-00-00-00|android 15|1264X2592|zh||||032|0|',
            'x-yun-app-channel': '10000023',
            'Authorization': self.Authorization,
            'Content-Type': 'application/json; charset=UTF-8',
            'User-Agent': 'okhttp/4.12.0',
            'Host': 'personal-kd-njs.yun.139.com',
            'Connection': 'Keep-Alive'
        }

    def build_share_headers(self):
        return {
            'Authorization': self.Authorization,
            'x-yun-api-version': 'v1',
            'x-yun-app-channel': '10000023',
            'x-yun-client-info': f'||9|{self.client_version}|Chrome|143.0.7499.146|codextestshare||Windows 10||zh-CN|||Q2hyb21l||',
            'x-yun-module-type': '100',
            'x-yun-svc-type': '1',
            'x-SvcType': '1',
            'x-yun-channel-source': '10000023',
            'x-huawei-channelSrc': '10000023',
            'Content-Type': 'application/json;charset=UTF-8',
            'CMS-DEVICE': 'default',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'Referer': 'https://yun.139.com/shareweb/',
            'Origin': 'https://yun.139.com',
        }

    def create_cloud_file(self, prefix):
        beijing_tz = timezone(timedelta(hours=8))
        now = datetime.now(beijing_tz)
        file_size = len(cloud_file_dummy_content)
        file_name = f"{prefix}{now.strftime('%Y%m%d_%H%M%S')}.txt"
        payload = {
            "contentHash": cloud_file_dummy_hash,
            "contentHashAlgorithm": "SHA256",
            "contentType": "application/oct-stream",
            "fileRenameMode": "force_rename",
            "localCreatedAt": now.isoformat(timespec='milliseconds'),
            "name": file_name,
            "parallelUpload": True,
            "parentFileId": "/",
            "partInfos": [{
                "end": file_size,
                "partNumber": 1,
                "partSize": file_size,
                "start": 0
            }],
            "size": file_size,
            "type": "file"
        }
        response = self.send_request('https://personal-kd-njs.yun.139.com/hcy/file/create',
                                     headers = self.build_cloud_file_headers(), data = payload, method = 'POST')
        if not response or response.status_code != 200:
            return None
        try:
            res_json = response.json()
        except ValueError:
            return None
        if not res_json.get("success"):
            return None
        data = res_json.get("data", {})
        return {
            "fileId": data.get("fileId"),
            "fileName": data.get("fileName", file_name),
        }

    def list_cloud_root_files(self):
        items = []
        page_cursor = ''
        while True:
            response = self.request_json('https://personal-kd-njs.yun.139.com/hcy/file/list',
                                         headers = self.build_cloud_file_headers(),
                                         data = {
                                             'imageThumbnailStyleList': ['Small', 'Large'],
                                             'orderBy': 'updated_at',
                                             'orderDirection': 'DESC',
                                             'pageInfo': {'pageCursor': page_cursor, 'pageSize': 100},
                                             'parentFileId': '/',
                                         },
                                         method = 'POST')
            if not response:
                return items
            if not response.get('success'):
                self.log(f"获取云盘文件列表失败: {response.get('message', '未知错误')}")
                return items
            data = response.get('data', {})
            items.extend(data.get('items', []))
            page_cursor = data.get('nextPageCursor') or ''
            if not page_cursor:
                return items

    @staticmethod
    def is_cleanup_upload_file(item):
        if item.get('type') != 'file' or item.get('parentFileId') != '/':
            return False
        name = item.get('name', '')
        if not (name.endswith('.txt') and (name.startswith('auto_upload_') or name.startswith('auto_share_'))):
            return False
        size = item.get('size')
        content_hash = item.get('contentHash')
        return size in (0, 1, None) or content_hash == cloud_file_dummy_hash

    def trash_cloud_files(self, file_ids):
        if not file_ids:
            return True
        response = self.request_json('https://personal-kd-njs.yun.139.com/hcy/recyclebin/batchTrash',
                                     headers = self.build_cloud_file_headers(),
                                     data = {'fileIds': file_ids},
                                     method = 'POST')
        if not response:
            self.log('清理上传文件失败: 接口无响应')
            return False
        if response.get('success'):
            return True
        self.log(f"清理上传文件失败: {response.get('message', '未知错误')}")
        return False

    def cleanup_uploaded_files(self, current_file=None):
        file_ids = []
        if current_file and current_file.get('fileId'):
            file_ids.append(current_file['fileId'])
        for item in self.list_cloud_root_files():
            if self.is_cleanup_upload_file(item):
                file_ids.append(item.get('fileId'))
        seen = set()
        unique_file_ids = []
        for file_id in file_ids:
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            unique_file_ids.append(file_id)
        if not unique_file_ids:
            return True
        if self.trash_cloud_files(unique_file_ids):
            self.log(f'-已清理上传文件: {len(unique_file_ids)}个')
            return True
        return False

    def complete_share_file_task(self, task):
        share_file = self.create_cloud_file('auto_share_')
        if not share_file:
            self.log('分享文件失败: 创建临时文件失败')
            return None
        try:
            response = self.request_json('https://yun.139.com/orchestration/personalCloud-rebuild/outlink/v1.0/getOutLink',
                                         headers = self.build_share_headers(),
                                         data = {
                                             'getOutLinkReq': {
                                                 'subLinkType': 0,
                                                 'encrypt': 0,
                                                 'coIDLst': [share_file.get('fileId')],
                                                 'caIDLst': [],
                                                 'pubType': 1,
                                                 'dedicatedName': share_file.get('fileName', ''),
                                                 'periodUnit': 1,
                                                 'viewerLst': [],
                                                 'extInfo': {'isWatermark': 0, 'shareChannel': '3001'},
                                                 'commonAccountInfo': {'account': self.account, 'accountType': 1},
                                             }
                                         },
                                         method = 'POST', retries = 1)
        finally:
            self.trash_cloud_files([share_file.get('fileId')])
        result = response.get('data', {}).get('result', {}) if response else {}
        if not response or not response.get('success') or result.get('resultCode') != '0':
            msg = result.get('resultDesc') or response.get('message', '未知错误') if response else '接口无响应'
            self.log(f'分享文件失败: {msg}')
            return None
        return self.query_cloud_task(task.get('id', 434), 'month') or task

    def build_ai_headers(self, use_client_info=False):
        headers = {
            'Connection': 'keep-alive',
            'sec-ch-ua-platform': '"Android"',
            'Authorization': self.Authorization,
            'x-yun-api-version': 'v1',
            'x-yun-tid': str(uuid.uuid4()),
            'sec-ch-ua': '"Android WebView";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            'sec-ch-ua-mobile': '?1',
            'X-Requested-With': 'com.chinamobile.mcloud',
            'Origin': 'https://frontend.mcloud.139.com',
            'Referer': 'https://frontend.mcloud.139.com/',
            'User-Agent': f'Mozilla/5.0 (Linux; Android 10; MI 8 Build/QKQ1.190828.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.146 Mobile Safari/537.36 MCloudApp/{self.client_version} tid/{uuid.uuid4()}',
            'Content-Type': 'application/json',
            'Sec-Fetch-Site': 'same-site',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Dest': 'empty',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'zh,zh-CN;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        if use_client_info:
            headers['Accept'] = 'text/event-stream'
            headers['x-yun-client-info'] = f'4||1|{self.client_version}||MI 8|{uuid.uuid4().hex.upper()}||android 10|||||'
            headers['x-yun-app-channel'] = '101'
            return headers
        headers['Accept'] = '*/*'
        headers['x-DeviceInfo'] = f'||36|{self.client_version}||MI 8|{uuid.uuid4()}||android 10|||||'
        return headers

    def get_ai_camera_sample_base64(self):
        sample_path = path.join(path.abspath(path.dirname(__file__)), 'assets', 'ai_camera_sample.jpg')
        if not path.exists(sample_path):
            return ''
        with open(sample_path, 'rb') as file:
            return f"data:image/jpg;base64,{base64.b64encode(file.read()).decode()}"

    @staticmethod
    def is_ai_chat_success(text):
        payloads = []
        for line in (text or '').splitlines():
            if line.startswith('data:'):
                payloads.append(line[5:].strip())
        if not payloads and text:
            payloads.append(text.strip())
        for payload in payloads:
            if not payload or payload == '[DONE]':
                continue
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            if data.get('success') or data.get('code') == '0000':
                return True
        return False

    def complete_ai_camera_task(self):
        if not self.user_domain_id:
            self.log('AI相机任务失败: 缺少用户信息')
            return False
        image_data = self.get_ai_camera_sample_base64()
        if not image_data:
            self.log('AI相机任务失败: 缺少样图')
            return False
        recognize_payload = json.dumps({
            'channelId': '101',
            'userId': self.user_domain_id,
            'recognizeType': '1',
            'base64': image_data,
            'sendType': '2',
            'imageExt': 'jpg',
            'uploadToCloud': True,
            'timeout': 30000,
        }, ensure_ascii = False, separators = (',', ':'))
        recognize_data = self.request_json('https://ai.yun.139.com/api/image/aiRecognize',
                                           headers = self.build_ai_headers(),
                                           data = recognize_payload,
                                           method = 'POST')
        if not recognize_data:
            self.log('AI相机识图失败: 接口无响应')
            return False
        if not recognize_data.get('success'):
            self.log(f"AI相机识图失败: {recognize_data.get('message', '未知错误')}")
            return False
        recognize_result = recognize_data.get('data') or {}
        file_id = recognize_result.get('fileId')
        if not file_id:
            self.log('AI相机识图失败: 缺少文件ID')
            return False
        task_id = str(recognize_result.get('taskId') or int(time.time() * 1000))
        file_name = f'{int(task_id) + 1}.jpeg' if task_id.isdigit() else f'{task_id}.jpeg'
        input_time = datetime.now(timezone(timedelta(hours = 8))).isoformat(timespec = 'milliseconds')
        chat_payload = json.dumps({
            'userId': self.user_domain_id,
            'sessionId': '',
            'applicationType': 'chat',
            'applicationId': '',
            'sourceChannel': '101',
            'dialogueInput': {
                'dialogue': '？',
                'prompt': '',
                'inputTime': input_time,
                'enableForceLlm': False,
                'enableForceNetworkSearch': True,
                'enableModelThinking': False,
                'enableAllNetworkSearch': False,
                'enableKnowledgeAndNetworkSearch': False,
                'enableRegenerate': False,
                'versionInfo': {'h5Version': '2.7.6'},
                'extInfo': '{}',
                'sortInfo': {},
                'toolSetting': {'imageToolSetting': {'enableLlmDescribe': True}},
                'attachment': {
                    'attachmentTypeList': [3],
                    'fileList': [{'fileId': file_id, 'name': file_name}],
                },
            },
        }, ensure_ascii = False, separators = (',', ':'))
        chat_response = self.send_request('https://ai.yun.139.com/api/outer/assistant/chat/v2/add',
                                          headers = self.build_ai_headers(use_client_info = True),
                                          data = chat_payload,
                                          method = 'POST')
        if not chat_response:
            self.log('AI相机对话失败: 接口无响应')
            return False
        if self.is_ai_chat_success(chat_response.text):
            return True
        try:
            chat_data = chat_response.json()
        except ValueError:
            chat_data = None
        if chat_data and (chat_data.get('success') or chat_data.get('code') == '0000'):
            return True
        if chat_data:
            self.log(f"AI相机对话失败: {chat_data.get('message') or chat_data.get('msg', '未知错误')}")
            return False
        self.log('AI相机对话失败: 响应解析失败')
        return False

    @staticmethod
    def get_task_progress(task):
        progress_parts = []
        currstep = task.get('currstep', 0)
        process = task.get('process', 0)
        if currstep:
            progress_parts.append(f'阶段{currstep}')
        if process:
            progress_parts.append(f'进度{process}')
        if not progress_parts:
            return ''
        return f" ({'，'.join(progress_parts)})"

    @staticmethod
    def strip_task_name(task):
        return re.sub(r'<[^>]+>', '', task.get('name', ''))

    @staticmethod
    def get_task_step_types(task):
        return set(task.get('stepTypeSet') or [])

    def get_task_click_keys(self, task):
        task_id = task.get('id')
        currstep = task.get('currstep', 0)
        step_types = self.get_task_step_types(task)
        if task_id == 409:
            if currstep > 0:
                return ['task2']
            return ['task', 'task2']
        if 'click' in step_types:
            return ['task']
        if task.get('state', '') != 'FINISH':
            return ['task']
        return []

    def get_cloud_task_groups(self):
        return [
            ('cloudEmail', '\n📮 联动任务'),
            ('time', '\n✨ 新版热门任务'),
            ('day', '\n📆 云盘每日任务'),
            ('month', '\n📆 云盘每月任务'),
        ]

    def query_cloud_task(self, task_id, group='time'):
        return_data = self.request_market_json(f'{self.market_base_url}/ycloud/signin/task/taskListV2',
                                               json_body=True, method='POST', data={
            'marketname': 'sign_in_3',
            'clientVersion': self.client_version,
            'group': group,
        })
        if not return_data or return_data.get('code') != 0:
            return None
        for task in return_data.get('result', {}).get(group, []):
            if task.get('id') == task_id:
                return task
        return None

    def complete_monthly_upload_task(self, task):
        target_count = 100
        current_process = int(task.get('process') or 0)
        for attempt in range(3):
            remaining = max(0, target_count - current_process)
            if remaining == 0:
                return True
            if attempt == 0:
                self.log(f'-开始补上传进度: 当前{current_process}/{target_count}，还需{remaining}次')
            else:
                self.log(f'-继续补上传进度: 当前{current_process}/{target_count}，还需{remaining}次')
            for _ in range(remaining):
                self.updata_file()
            refreshed_task = self.query_cloud_task(task.get('id', 522), 'time')
            if not refreshed_task:
                return False
            refreshed_process = int(refreshed_task.get('process') or 0)
            if refreshed_task.get('state') == 'FINISH' or refreshed_process >= target_count:
                return True
            if refreshed_process <= current_process:
                self.log(f'-月上传任务进度: {refreshed_process}/{target_count}')
                return False
            current_process = refreshed_process
        self.log(f'-月上传任务进度: {current_process}/{target_count}')
        return False

    def get_cloud_tasklist_v2(self):
        for group, title in self.get_cloud_task_groups():
            return_data = self.request_market_json(f'{self.market_base_url}/ycloud/signin/task/taskListV2',
                                                   json_body=True, method='POST', data={
                'marketname': 'sign_in_3',
                'clientVersion': self.client_version,
                'group': group,
            })
            if not return_data:
                self.log(f'获取任务列表失败: {group}')
                continue
            if return_data.get('code') != 0:
                self.log(f"获取任务列表失败: {group} {return_data.get('msg', '未知错误')}")
                continue
            tasks = return_data.get('result', {}).get(group, [])
            if not tasks:
                continue
            self.log(title)
            for task in tasks:
                self.handle_cloud_v2_task(group, task)
        self.claim_revival_reward()
        self.claim_multiple_clouds()
        self.cleanup_uploaded_files()

    def handle_cloud_v2_task(self, group, task):
        task_id = task.get('id')
        task_name = self.strip_task_name(task)
        task_status = task.get('state', '')
        if task_status == 'FINISH':
            print(f'-已完成: {task_name}')
            return
        if group == 'day' and task_id == 106:
            self.log(f'-去完成: {task_name}')
            self.do_task(task_id, task_type='day', app_type='cloud_app')
            return
        if task_id == 522:
            self.log(f'-去完成: {task_name}')
            if self.complete_monthly_upload_task(task):
                self.log(f'-已完成: {task_name}')
                return
            refreshed_task = self.query_cloud_task(task_id, group) or task
            self.log(f'-需手动完成: {task_name}{self.get_task_progress(refreshed_task)}')
            return
        if task_id == 434:
            self.log(f'-去完成: {task_name}')
            refreshed_task = self.complete_share_file_task(task)
            if refreshed_task:
                refreshed_name = self.strip_task_name(refreshed_task)
                if refreshed_task.get('state') == 'FINISH':
                    self.log(f'-已完成: {refreshed_name}')
                    return
                self.log(f'-分享成功: {refreshed_name}{self.get_task_progress(refreshed_task)}')
                return
            self.log(f'-需手动完成: {task_name}{self.get_task_progress(task)}')
            return
        if task_id == 406:
            self.log(self.format_notice_task_log(task_name, self.get_notice_status()))
            return
        if task_id == 478:
            self.log(f'-去完成: {task_name}')
            click_data = self.click_task(task_id, 'randomCloudTask') or {}
            if click_data.get('code') == 0:
                result = click_data.get('result') or {}
                num = result.get('num', 0)
                msg = result.get('msg', '')
                if num:
                    self.log(f'-已完成: {task_name} 获得{num}云朵 {msg}')
                else:
                    self.log(f'-已完成: {task_name} {msg}')
            else:
                self.log(f'-需手动完成: {task_name} {click_data.get("msg", "未知错误")}')
            return
        task_keys = self.get_task_click_keys(task)
        if task_keys:
            self.log(f'-去完成: {task_name}')
            for task_key in task_keys:
                click_data = self.click_task(task_id, task_key)
                if click_data and click_data.get('code') == 0:
                    continue
                msg = click_data.get('msg', '未知错误') if click_data else '接口无响应'
                self.log(f'-任务登记失败: {task_name} {msg}')
                return
            if task_id == 585 and self.complete_ai_camera_task():
                self.log(f'-已完成: {task_name}')
                return
            self.log(f'-已登记任务: {task_name}')
            return
        self.log(f'-需手动完成: {task_name}{self.get_task_progress(task)}')

    def claim_revival_reward(self):
        """领取云朵复活卡奖励"""
        try:
            url = f'{self.market_base_url}/ycloud/signin/page/receiveRevivalReward'
            data = self.request_market_json(url, json_body=True, method='POST', data={}, retries=2)
            if not data:
                return
            if data.get('code') == 0:
                result = data.get('result') or {}
                reward = result.get('rewardClouds', 0)
                total = result.get('totalClouds', 0)
                if reward:
                    self.log(f'-云朵复活卡: 领取{reward}云朵 (累计{total}云朵)')
                else:
                    self.log('-云朵复活卡: 暂无奖励可领')
            else:
                self.log(f'-云朵复活卡: {data.get("msg", "未知错误")}')
        except Exception as e:
            self.log(f'-云朵复活卡失败: {e}')

    def claim_multiple_clouds(self):
        """查询并领取翻倍云朵奖励"""
        try:
            url = f'{self.market_base_url}/ycloud/signin/page/multiple'
            data = self.request_market_json(url, retries=2)
            if not data or data.get('code') != 0:
                return
            result = data.get('result') or {}
            cloud_count = int(result.get('cloudCount') or 0)
            if cloud_count <= 0:
                return
            self.log(f'-云朵翻倍: 可领{cloud_count}云朵')
        except Exception as e:
            self.log(f'-云朵翻倍查询失败: {e}')

    def sleep(self, min_delay=1, max_delay=1.5):
        delay = random.uniform(min_delay, max_delay)
        time.sleep(delay)

    def query_spec_token(self, source_id='001005'):
        sso_url = 'https://orches.yun.139.com/orchestration/auth-rebuild/token/v1.0/querySpecToken'
        sso_headers = {
            'Authorization': self.Authorization,
            'User-Agent': ua,
            'Content-Type': 'application/json',
            'Accept': '*/*',
            'Host': 'orches.yun.139.com'
        }
        sso_payload = {"account": self.account, "toSourceId": source_id}
        sso_data = self.request_json(sso_url, headers=sso_headers, data=sso_payload, method='POST')
        if not sso_data:
            self.log(f'获取specToken失败({source_id}): 接口无响应')
            return None
        if sso_data.get('success'):
            return sso_data['data']['token']
        else:
            self.log(f"获取specToken失败({source_id}): {sso_data.get('message', '未知错误')}")
            return None

    def sso(self):
        token = self.query_spec_token('001005')
        if token is not None:
            self.sso_token = token
            return token
        else:
            self.log('-ck可能失效了')
            return None

    def jwt(self):
        token = self.sso()
        if token is not None:

            jwt_url = f"https://caiyun.feixin.10086.cn:7071/portal/auth/tyrzLogin.action?ssoToken={token}"
            jwt_data = self.request_json(jwt_url, headers = self.jwtHeaders, method = 'POST')
            if not jwt_data:
                self.log('JWT获取失败: 接口无响应')
                return False
            if jwt_data['code'] != 0:
                self.log(f"JWT获取失败: {jwt_data['msg']}")
                return False
            jwt_token = jwt_data['result']['token']
            self.jwtHeaders['jwtToken'] = jwt_token
            self.cookies['jwtToken'] = jwt_token
            self.build_market_context(jwt_token)
            return True
        else:
            self.log('-ck可能失效了')
            return False

    @catch_errors
    def signin_status(self):
        self.sleep()
        check_url = f'{self.market_base_url}/ycloud/signin/page/infoV3'
        check_data = self.request_market_json(check_url, params={'client': 'app'})
        if not check_data:
            self.log('查询签到失败: 接口无响应')
            return
        if check_data.get('code') != 0:
            self.log(f"查询签到失败: {check_data.get('msg', '未知错误')}")
            return
        if self.get_today_sign_state(check_data.get('result', {})):
            self.log('✅已签到')
            return
        self.sleep(1, 2)
        signin_data = self.request_market_json(f'{self.market_base_url}/ycloud/signin/page/startSignIn',
                                               params={'client': 'app'})
        if not signin_data:
            self.log('签到失败: 接口无响应')
            return
        if signin_data.get('code') == 0 and self.get_today_sign_state(signin_data.get('result', {})):
            self.log('✅签到成功')
            return
        self.sleep(1, 2)
        latest_data = self.request_market_json(check_url, params={'client': 'app'})
        if latest_data and latest_data.get('code') == 0 and self.get_today_sign_state(latest_data.get('result', {})):
            self.log('✅签到成功')
            return
        self.log(f"签到失败: {signin_data.get('msg', '未知错误')}")

    def click(self):
        successful_click = 0

        try:
            for _ in range(self.click_num):
                return_data = self.click_task(319) or {}
                time.sleep(0.2)

                if 'result' in return_data:
                    self.log(f'✅戳一戳: {return_data["result"]}')
                    successful_click += 1

            if successful_click == 0:
                print(f'❌戳一戳: 未获得 x {self.click_num}')
        except Exception as e:
            print(f'错误信息:{e}')

    @catch_errors
    def refresh_notetoken(self):
        note_url = 'http://mnote.caiyun.feixin.10086.cn/noteServer/api/authTokenRefresh.do'
        note_payload = {
            "authToken": self.auth_token,
            "userPhone": self.account
        }
        note_headers = {
            'X-Tingyun-Id': 'p35OnrDoP8k;c=2;r=1122634489;u=43ee994e8c3a6057970124db00b2442c::8B3D3F05462B6E4C',
            'Charset': 'UTF-8',
            'Connection': 'Keep-Alive',
            'User-Agent': 'mobile',
            'APP_CP': 'android',
            'CP_VERSION': '3.2.0',
            'x-huawei-channelsrc': '10001400',
            'Host': 'mnote.caiyun.feixin.10086.cn',
            'Content-Type': 'application/json; charset=UTF-8',
            'Accept-Encoding': 'gzip'
        }

        try:
            response = self.send_request(note_url, headers = note_headers, data = note_payload, method = "POST")
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print('出错了:', e)
            return

        self.note_token = response.headers.get('NOTE_TOKEN')
        self.note_auth = response.headers.get('APP_AUTH')

    def get_tasklist(self, url, app_type):
        if url == 'sign_in_3' and app_type == 'cloud_app':
            self.get_cloud_tasklist_v2()
            return
        url = f'https://caiyun.feixin.10086.cn/market/signin/task/taskList?marketname={url}'
        return_data = self.send_request(url, headers = self.jwtHeaders, cookies = self.cookies).json()
        self.sleep()
        task_list = return_data.get('result', {})

        try:
            for task_type, tasks in task_list.items():
                if task_type in ["new", "hidden", "hiddenabc"]:
                    continue
                if app_type == 'cloud_app':
                    if task_type == "month":
                        self.log('\n📆 云盘每月任务')
                        for month in tasks:
                            task_id = month.get('id')
                            if task_id in [110, 113, 417, 409]:
                                continue
                            task_name = re.sub(r'<[^>]+>', '', month.get('name', ''))
                            task_status = month.get('state', '')

                            if task_status == 'FINISH':
                                print(f'-已完成: {task_name}')
                                continue
                            self.log(f'-去完成: {task_name}')
                            self.do_task(task_id, task_type = 'month', app_type = 'cloud_app')
                            time.sleep(2)
                    elif task_type == "day":
                        self.log('\n📆 云盘每日任务')
                        for day in tasks:
                            task_id = day.get('id')
                            if task_id == 404:
                                continue
                            task_name = re.sub(r'<[^>]+>', '', day.get('name', ''))
                            task_status = day.get('state', '')

                            if task_status == 'FINISH':
                                print(f'-已完成: {task_name}')
                                continue
                            self.log(f'-去完成: {task_name}')
                            self.do_task(task_id, task_type = 'day', app_type = 'cloud_app')
                elif app_type == 'email_app':
                    if task_type == "month":
                        self.log('\n📆 139邮箱每月任务')
                        for month in tasks:
                            task_id = month.get('id')
                            task_name = re.sub(r'<[^>]+>', '', month.get('name', ''))
                            task_status = month.get('state', '')
                            if task_id in [1004, 1005, 1015, 1020]:
                                continue

                            if task_status == 'FINISH':
                                print(f'-已完成: {task_name}')
                                continue
                            self.log(f'-去完成: {task_name}')
                            self.do_task(task_id, task_type = 'month', app_type = 'email_app')
                            time.sleep(2)
        except Exception as e:
            self.log(f'获取任务列表错误:{e}')

    @catch_errors
    def do_task(self, task_id, task_type, app_type):
        self.sleep()
        if app_type == 'cloud_app':
            self.click_task(task_id)
        else:
            task_url = f'https://caiyun.feixin.10086.cn/market/signin/task/click?key=task&id={task_id}'
            self.send_request(task_url, headers = self.jwtHeaders, cookies = self.cookies)

        if app_type == 'cloud_app':
            if task_type == 'day':
                if task_id == 106:
                    self.log('-开始上传文件，默认0kb')
                    self.updata_file()
                elif task_id == 107:
                    self.refresh_notetoken()
                    print('-获取默认笔记id')
                    note_url = 'http://mnote.caiyun.feixin.10086.cn/noteServer/api/syncNotebookV3.do'
                    headers = {
                        'X-Tingyun-Id': 'p35OnrDoP8k;c=2;r=1122634489;u=43ee994e8c3a6057970124db00b2442c::8B3D3F05462B6E4C',
                        'Charset': 'UTF-8',
                        'Connection': 'Keep-Alive',
                        'User-Agent': 'mobile',
                        'APP_CP': 'android',
                        'CP_VERSION': '3.2.0',
                        'x-huawei-channelsrc': '10001400',
                        'APP_NUMBER': self.account,
                        'APP_AUTH': self.note_auth,
                        'NOTE_TOKEN': self.note_token,
                        'Host': 'mnote.caiyun.feixin.10086.cn',
                        'Content-Type': 'application/json; charset=UTF-8',
                        'Accept': '*/*'
                    }
                    payload = {
                        "addNotebooks": [],
                        "delNotebooks": [],
                        "notebookRefs": [],
                        "updateNotebooks": []
                    }
                    return_data = self.send_request(url = note_url, headers = headers, data = payload,
                                                    method = 'POST').json()
                    if return_data is None:
                        return print('出错了')
                    self.notebook_id = return_data['notebooks'][0]['notebookId']
                    print('开始创建笔记')
                    self.create_note(headers)
            elif task_type == 'month':
                pass
        elif app_type == 'email_app':
            if task_type == 'month':
                pass

    @catch_errors
    def updata_file(self):
        upload_info = self.create_cloud_file('auto_upload_')
        if not upload_info:
            self.log('-上传失败: 接口无响应')
            return
        self.log(f"-上传文件成功，文件名: {upload_info.get('fileName', '')}")
        self.cleanup_uploaded_files(upload_info)

    def create_note(self, headers):
        note_id = self.get_note_id(32)
        createtime = str(int(round(time.time() * 1000)))
        time.sleep(3)
        updatetime = str(int(round(time.time() * 1000)))
        note_url = 'http://mnote.caiyun.feixin.10086.cn/noteServer/api/createNote.do'
        payload = {
            "archived": 0,
            "attachmentdir": note_id,
            "attachmentdirid": "",
            "attachments": [],
            "audioInfo": {
                "audioDuration": 0,
                "audioSize": 0,
                "audioStatus": 0
            },
            "contentid": "",
            "contents": [{
                "contentid": 0,
                "data": "<font size=\"3\">000000</font>",
                "noteId": note_id,
                "sortOrder": 0,
                "type": "RICHTEXT"
            }],
            "cp": "",
            "createtime": createtime,
            "description": "android",
            "expands": {
                "noteType": 0
            },
            "latlng": "",
            "location": "",
            "noteid": note_id,
            "notestatus": 0,
            "remindtime": "",
            "remindtype": 1,
            "revision": "1",
            "sharecount": "0",
            "sharestatus": "0",
            "system": "mobile",
            "tags": [{
                "id": self.notebook_id,
                "orderIndex": "0",
                "text": "默认笔记本"
            }],
            "title": "00000",
            "topmost": "0",
            "updatetime": updatetime,
            "userphone": self.account,
            "version": "1.00",
            "visitTime": ""
        }
        create_note_data = self.send_request(note_url, headers = headers, data = payload, method = "POST")
        if create_note_data.status_code == 200:
            self.log('-创建笔记成功')
        else:
            self.log('-创建失败')

    def get_note_id(self, length):
        characters = '19f3a063d67e4694ca63a4227ec9a94a19088404f9a28084e3e486b928039a299bf756ebc77aa4f6bfa250308ec6a8be8b63b5271a00350d136d117b8a72f39c5bd15cdfd350cba4271dc797f15412d9f269e666aea5039f5049d00739b320bb9e8585a008b52c1cbd86970cae9476446f3e41871de8d9f6112db94b05e5dc7ea0a942a9daf145ac8e487d3d5cba7cea145680efc64794d43dd15c5062b81e1cda7bf278b9bc4e1b8955846e6bc4b6a61c28f831f81b2270289e5a8a677c3141ddc9868129060c0c3b5ef507fbd46c004f6de346332ef7f05c0094215eae1217ee7c13c8dca6d174cfb49c716dd42903bb4b02d823b5f1ff93c3f88768251b56cc'
        note_id = ''.join(random.choice(characters) for _ in range(length))
        return note_id

    @catch_errors
    def wxsign(self):
        self.sleep()
        url = 'https://caiyun.feixin.10086.cn/market/playoffic/followSignInfo?isWx=true'
        return_data = self.send_request(url, headers = self.jwtHeaders, cookies = self.cookies).json()

        if return_data['msg'] != 'success':
            return self.log(return_data['msg'])
        if not return_data['result'].get('todaySignIn'):
            return self.log('❌签到失败,可能未绑定公众号')
        return self.log('✅公众号签到成功')

    def shake(self):
        url = "https://caiyun.feixin.10086.cn:7071/market/shake-server/shake/shakeIt?flag=1"
        successful_shakes = 0

        try:
            for _ in range(self.click_num):
                return_data = self.send_request(url = url, cookies = self.cookies, headers = self.jwtHeaders,
                                                method = 'POST').json()
                time.sleep(1)
                shake_prize_config = return_data["result"].get("shakePrizeconfig")

                if shake_prize_config:
                    self.log(f"🎉摇一摇获得: {shake_prize_config['name']}")
                    successful_shakes += 1
        except Exception as e:
            print(f'错误信息: {e}')
        if successful_shakes == 0:
            print(f'❌未摇中 x {self.click_num}')

    @catch_errors
    def surplus_num(self):
        self.sleep()
        draw_info_url = 'https://caiyun.feixin.10086.cn/market/playoffic/drawInfo'
        draw_url = "https://caiyun.feixin.10086.cn/market/playoffic/draw"

        draw_info_data = self.send_request(draw_info_url, headers = self.jwtHeaders).json()

        if draw_info_data.get('msg') == 'success':
            remain_num = draw_info_data['result'].get('surplusNumber', 0)
            self.log(f'剩余抽奖次数{remain_num}')
            if remain_num > 50 - self.draw:
                for _ in range(self.draw):
                    self.sleep()
                    draw_data = self.send_request(url = draw_url, headers = self.jwtHeaders).json()

                    if draw_data.get("code") == 0:
                        prize_name = draw_data["result"].get("prizeName", "")
                        self.log("✅抽奖成功，获得:" + prize_name)
                    else:
                        print("❌抽奖失败")
            else:
                pass
        else:
            self.log(f"抽奖查询失败: {draw_info_data.get('msg')}")

    @catch_errors
    def do_fruit_task(self, task_name, task_id, water_num):
        self.log(f'-去完成: {task_name}')
        do_task_url = f'{self.fruit_url}task/doTask.do?taskId={task_id}'
        do_task_data = self.send_request(do_task_url, headers = self.treeHeaders).json()

        if do_task_data.get('success'):
            get_water_url = f'{self.fruit_url}task/givenWater.do?taskId={task_id}'
            get_water_data = self.send_request(get_water_url, headers = self.treeHeaders).json()

            if get_water_data.get('success'):
                self.log(f'-已完成任务获得水滴: {water_num}')
            else:
                self.log(f'❌领取失败: {get_water_data.get("msg", "")}')
        else:
            self.log(f'❌参与任务失败: {do_task_data.get("msg", "")}')

    @catch_errors
    def cloud_game(self):
        game_info_url = 'https://caiyun.feixin.10086.cn/market/signin/hecheng1T/info?op=info'
        bigin_url = 'https://caiyun.feixin.10086.cn/market/signin/hecheng1T/beinvite'
        end_url = 'https://caiyun.feixin.10086.cn/market/signin/hecheng1T/finish?flag=true'

        game_info_data = self.send_request(game_info_url, headers = self.jwtHeaders, cookies = self.cookies).json()
        if game_info_data and game_info_data.get('code', -1) == 0:
            currnum = game_info_data.get('result', {}).get('info', {}).get('curr', 0)
            count = game_info_data.get('result', {}).get('history', {}).get('0', {}).get('count', '')
            rank = game_info_data.get('result', {}).get('history', {}).get('0', {}).get('rank', '')

            self.log(f'今日剩余游戏次数: {currnum} 合成次数: {count}')

            for _ in range(currnum):
                self.send_request(bigin_url, headers = self.jwtHeaders, cookies = self.cookies).json()
                print('-开始游戏,等待10-15秒完成游戏')
                time.sleep(random.randint(10, 15))
                end_data = self.send_request(end_url, headers = self.jwtHeaders, cookies = self.cookies).json()
                if end_data and end_data.get('code', -1) == 0:
                    self.log('游戏成功')
        else:
            print("-获取游戏信息失败")

    @catch_errors
    def receive(self):
        prize_url = f"https://caiyun.feixin.10086.cn/market/prizeApi/checkPrize/getUserPrizeLogPage?currPage=1&pageSize=15&_={self.timestamp}"
        info_data = self.request_market_json(f'{self.market_base_url}/ycloud/signin/page/infoV3',
                                             params={'client': 'app'})
        if not info_data:
            self.log('查询云朵失败: 接口无响应')
            return
        if info_data.get('code') != 0:
            self.log(f"查询云朵失败: {info_data.get('msg', '未知错误')}")
            return
        info_result = info_data.get('result', {})
        pending_amount = info_result.get('toReceive', 0)
        total_amount = info_result.get('total', '')
        if pending_amount:
            self.prepare_signin_center_session(for_receive=True)
            receive_data = self.request_market_json(f'{self.market_base_url}/ycloud/signin/page/receiveV3',
                                                    params={'client': 'app'},
                                                    headers=self.build_receive_headers())
            if not receive_data:
                self.log('领取云朵失败: 接口无响应')
                self.log(f'-当前待领取:{pending_amount}云朵')
            elif receive_data.get('code') == 0:
                receive_result = receive_data.get('result', {})
                self.log(f'-领取云朵:{receive_result.get("receive", pending_amount)}云朵')
                total_amount = receive_result.get('total', total_amount)
            else:
                latest_info_data = self.request_market_json(f'{self.market_base_url}/ycloud/signin/page/infoV3',
                                                            params={'client': 'app'})
                latest_result = latest_info_data.get('result', {}) if latest_info_data and latest_info_data.get('code') == 0 else {}
                latest_pending = latest_result.get('toReceive', pending_amount)
                latest_total = latest_result.get('total', total_amount)
                pending_delta = pending_amount - latest_pending if isinstance(pending_amount, int) and isinstance(latest_pending, int) else 0
                total_delta = latest_total - total_amount if isinstance(total_amount, int) and isinstance(latest_total, int) else 0
                claimed_amount = total_delta or pending_delta or (pending_amount if latest_pending == 0 else 0)
                if claimed_amount > 0:
                    self.log(f'-领取云朵:{claimed_amount}云朵')
                    total_amount = latest_total
                else:
                    self.log(f"领取云朵失败: {receive_data.get('msg', '未知错误')}")
                    self.log(f'-当前待领取:{pending_amount}云朵')
        else:
            self.log('-当前待领取:0云朵')
        self.sleep()
        prize_data = self.request_json(prize_url, headers = self.jwtHeaders, cookies = self.cookies) or {}
        result = prize_data.get('result', {}).get('result') or []
        rewards = ''
        for value in result:
            prizeName = value.get('prizeName')
            flag = value.get('flag')
            if flag == 1:
                rewards += f'待领取奖品: {prizeName}\n'
        self.log(f'-当前云朵数量:{total_amount}云朵')
        if rewards:
            self.log(rewards)
        global user_amount
        user_amount += f'用户【{self.encrypt_account}】:{total_amount}云朵\n'

    @catch_errors
    def backup_cloud(self):
        backup_url = 'https://caiyun.feixin.10086.cn/market/backupgift/info'
        backup_data = self.send_request(backup_url, headers = self.jwtHeaders).json()
        state = backup_data.get('result', {}).get('state', '')
        if state == -1:
            self.log('本月未备份,暂无连续备份奖励')

        elif state == 0:
            self.log('-领取本月连续备份奖励')
            cur_url = 'https://caiyun.feixin.10086.cn/market/backupgift/receive'
            cur_data = self.send_request(cur_url, headers = self.jwtHeaders).json()
            self.log(f'-获得云朵数量:{cur_data.get("result").get("result")}')

        elif state == 1:
            print('-已领取本月连续备份奖励')
        self.sleep()
        expend_url = f'{self.market_base_url}/ycloud/signin/page/taskExpansion'
        expend_data = self.request_market_json(expend_url) or {}

        curMonthBackup = expend_data.get('result', {}).get('curMonthBackup', '')
        preMonthBackup = expend_data.get('result', {}).get('preMonthBackup', '')
        curMonthBackupTaskAccept = expend_data.get('result', {}).get('curMonthBackupTaskAccept', '')
        nextMonthTaskRecordCount = expend_data.get('result', {}).get('nextMonthTaskRecordCount', '')
        acceptDate = expend_data.get('result', {}).get('acceptDate', '')

        if curMonthBackup:
            self.log(f'- 本月已备份，下月可领取膨胀云朵: {nextMonthTaskRecordCount}')
        else:
            self.log('- 本月还未备份，下月暂无膨胀云朵')

        if preMonthBackup:
            if curMonthBackupTaskAccept:
                print('- 上月已备份，膨胀云朵已领取')
            else:
                receive_url = f'{self.market_base_url}/ycloud/signin/page/receiveTaskExpansion?acceptDate={acceptDate}'
                receive_data = self.request_market_json(receive_url)
                if not receive_data or receive_data.get('code') != 0:
                    msg = receive_data.get('msg', '接口无响应') if receive_data else '接口无响应'
                    self.log(f'-领取失败:{msg}')
                else:
                    cloudCount = receive_data.get('result', {}).get('cloudCount', '')
                    self.log(f'- 膨胀云朵领取成功: {cloudCount}朵')
        else:
            print('-上月未备份，本月无膨胀云朵领取')

    @catch_errors
    def complete_notice_task(self):
        notice_status = self.get_notice_status()
        if not notice_status:
            self.log('获取通知任务状态失败')
            return

        pushOn = notice_status.get('pushOn', '')
        firstTaskStatus = notice_status.get('firstTaskStatus', '')
        secondTaskStatus = notice_status.get('secondTaskStatus', '')
        onDuaration = notice_status.get('onDuaration', '')
        total = notice_status.get('total', 31)

        if pushOn == 1:
            reward_url = 'https://caiyun.feixin.10086.cn/market/msgPushOn/task/obtain'

            if firstTaskStatus == 3:
                print('- 任务1奖励已领取')
            else:
                self.log('- 领取任务1奖励')
                reward1_data = self.send_request(reward_url, headers=self.jwtHeaders, data={"type": 1},
                                                 method="POST").json()
                self.log(reward1_data.get('result', {}).get('description', ''))

            if secondTaskStatus == 2:
                self.log('- 领取任务2奖励')
                reward2_data = self.send_request(reward_url, headers=self.jwtHeaders, data={"type": 2},
                                                 method="POST").json()
                self.log(reward2_data.get('result', {}).get('description', ''))

            progress = f'{onDuaration}/{total}天'
            self.log(f'- 通知已开启天数: {onDuaration}, 满{total}天可领取奖励')
        else:
            self.log('- 通知权限未开启')

    def login_red_packet(self):
        token = self.query_spec_token(RED_PACKET_SOURCE_ID)
        if not token:
            return False
        login_url = f'{RED_PACKET_BASE_URL}/ticket/login'
        login_headers = {
            'Content-Type': 'application/json',
            'User-Agent': ua,
            'Accept': '*/*',
            'Host': 'cpactiv.buy.139.com',
        }
        import requests as _requests
        try:
            resp = _requests.post(login_url, headers=login_headers,
                                  json={'token': token, 'sourceId': RED_PACKET_SOURCE_ID},
                                  timeout=15)
            if resp.status_code == 200:
                login_data = resp.json()
            else:
                self.log(f'红包派对登录失败: HTTP {resp.status_code}，请求已记录')
                return False
        except Exception as e:
            self.log(f'红包派对登录异常: {e}')
            return False
        if not login_data:
            self.log('红包派对登录失败: 接口无响应')
            return False
        if login_data.get('code') != 0:
            self.log(f"红包派对登录失败: {login_data.get('msg', '未知错误')}")
            return False
        login_result = login_data.get('result') or {}
        self.red_packet_token = login_result.get('token') or ''
        self.red_packet_mobile = login_result.get('mobile') or self.account
        header = login_data.get('header') or {}
        if str(header.get('status')) == '200':
            self.market_source_id = RED_PACKET_SOURCE_ID
            self.build_market_context(login_result.get('jwtToken') or '')
            self.save_red_packet_token()
            return True
        self.log(f"红包派对登录失败: {header.get('respMsg', '未知错误')}")
        return False

    def load_red_packet_token(self):
        stored = self.get_storage_record()
        self.red_packet_token = stored.get('redPacketToken', '')
        self.red_packet_mobile = stored.get('redPacketMobile', '')
        return bool(self.red_packet_token)

    def save_red_packet_token(self):
        cache = load_js_cache()
        entry = cache.setdefault('accounts', {}).setdefault(self.account, {})
        entry['redPacketToken'] = self.red_packet_token
        entry['redPacketMobile'] = self.red_packet_mobile
        save_js_cache(cache)

    def sign_red_packet(self):
        if not self.red_packet_mobile:
            no = self.account
        else:
            no = self.red_packet_mobile
        sign_str = f'{RED_PACKET_SIGN_KEY}mobile{no}'
        sign = hashlib.md5(sign_str.encode()).hexdigest()
        data = self.request_market_json(f'{RED_PACKET_BASE_URL}/sign/signBySourceId',
                                        json_body=True, method='POST', data={
                'mobile': no,
                'sign': sign,
                'sourceId': RED_PACKET_SOURCE_ID,
                'version': RED_PACKET_VERSION,
                'channelSrc': RED_PACKET_CHANNEL_SRC,
            })
        if not data:
            return False
        if data.get('code') == 0:
            self.log('红包签到成功')
            return True
        self.log(f"红包签到失败: {data.get('msg', '未知错误')}")
        return True

    def get_red_packet_task_list(self):
        token = self.red_packet_token
        if not token:
            return None
        return self.request_market_json(f'{RED_PACKET_BASE_URL}/taskCenter/task',
                                        json_body=True, method='POST', data={
                'appId': RED_PACKET_APP_ID,
                'sourceId': RED_PACKET_SOURCE_ID,
                'token': token,
            })

    def log_red_packet_balance(self):
        token = self.red_packet_token
        if not token:
            return
        balance_data = self.request_market_json(f'{RED_PACKET_BASE_URL}/taskCenter/balance',
                                                json_body=True, method='POST', data={
                'appId': RED_PACKET_APP_ID,
                'sourceId': RED_PACKET_SOURCE_ID,
                'token': token,
            })
        if not balance_data:
            return
        header = balance_data.get('header') or {}
        if str(header.get('status')) != '200':
            return
        result = balance_data.get('data') or {}
        amount = result.get('amount', 0)
        self.log(f'-余额: {amount}')

    def handle_red_packet_sign(self, task_list):
        sign_task = task_list.get('SIGN') or {}
        if not sign_task:
            return
        for task in sign_task:
            if task.get('state') == 3:
                continue
            self.sign_red_packet()
            return

    def handle_red_packet_task(self, task):
        task_name = task.get('taskName', '')
        task_code = task.get('taskCode', '')
        state = task.get('state', 0)
        if state == 3:
            self.log(f'-已完成: {task_name}')
            return
        if task_code in RED_PACKET_MANUAL_TASKS:
            self.log(f'-{RED_PACKET_MANUAL_TASKS[task_code]}: {task_name}')
            return
        if state == 0:
            click_data = self.request_market_json(f'{RED_PACKET_BASE_URL}/taskCenter/click',
                                                  json_body=True, method='POST', data={
                    'appId': RED_PACKET_APP_ID,
                    'sourceId': RED_PACKET_SOURCE_ID,
                    'token': self.red_packet_token,
                    'taskCode': task_code,
                })
            if not click_data:
                self.log(f'-操作失败: {task_name}')
                return
            header = click_data.get('header') or {}
            if str(header.get('status')) != '200':
                self.log(f"-操作失败: {task_name} {header.get('respMsg', '未知错误')}")
                return
            done = click_data.get('data') or {}
            if done.get('state') == 2:
                self.log(f'-进行中: {task_name}')
                return
            return
        if state == 1:
            self.log(f'-进行中: {task_name}')
            return
        if state == 2:
            if task_code in RED_PACKET_BROWSE_TASKS:
                self.log(f'-浏览任务: {task_name} (需跳转活动页)')
                return
            if task_code in RED_PACKET_DIRECT_TASKS:
                complete_data = self.request_market_json(f'{RED_PACKET_BASE_URL}/taskCenter/complete',
                                                         json_body=True, method='POST', data={
                        'appId': RED_PACKET_APP_ID,
                        'sourceId': RED_PACKET_SOURCE_ID,
                        'token': self.red_packet_token,
                        'taskCode': task_code,
                    })
                if not complete_data:
                    self.log(f'-完成失败: {task_name}')
                    return
                header = complete_data.get('header') or {}
                if str(header.get('status')) == '200':
                    self.log(f'-已完成: {task_name}')
                else:
                    self.log(f"-完成失败: {task_name} {header.get('respMsg', '未知错误')}")
                return
            if task_code.startswith('ANSWER_'):
                self.do_red_packet_question(task, task_code)
                return
            self.log(f'-需手动完成: {task_name}')
            return

    def get_red_packet_question(self, task):
        token = self.red_packet_token
        if not token:
            return None
        data = self.request_market_json(f'{RED_PACKET_BASE_URL}/taskCenter/question',
                                        json_body=True, method='POST', data={
                'appId': RED_PACKET_APP_ID,
                'sourceId': RED_PACKET_SOURCE_ID,
                'token': token,
                'taskCode': task.get('taskCode', ''),
            })
        if not data:
            return None
        header = data.get('header') or {}
        if str(header.get('status')) != '200':
            return None
        return data.get('data') or {}

    def do_red_packet_question(self, task, task_code):
        token = self.red_packet_token
        if not token:
            return
        task_name = task.get('taskName', '')
        question = self.get_red_packet_question(task)
        if not question:
            self.log(f'-获取题目失败: {task_name}')
            return
        question_text = question.get('question') or question.get('questionText', '')
        options = question.get('options') or question.get('questionOptionList') or []
        answer = RED_PACKET_KNOWN_ANSWERS.get(question_text, '')
        if not answer and options:
            answer = options[0].get('optionDesc') or options[0].get('name', '')
            self.log(f'-未知题目: {question_text}')
        if not answer:
            self.log(f'-无答案: {task_name}')
            return
        option_id = ''
        for opt in options:
            desc = opt.get('optionDesc') or opt.get('name', '')
            if desc == answer:
                option_id = opt.get('id') or opt.get('optionId', '')
                break
        if not option_id:
            self.log(f'-找不到选项: {task_name}')
            return
        answer_data = self.request_market_json(f'{RED_PACKET_BASE_URL}/taskCenter/answer',
                                               json_body=True, method='POST', data={
                'appId': RED_PACKET_APP_ID,
                'sourceId': RED_PACKET_SOURCE_ID,
                'token': token,
                'taskCode': task_code,
                'optionId': option_id,
            })
        if not answer_data:
            self.log(f'-答题失败: {task_name}')
            return
        header = answer_data.get('header') or {}
        if str(header.get('status')) == '200':
            self.log(f'-答题成功: {task_name}')
        else:
            self.log(f"-答题失败: {task_name} {header.get('respMsg', '未知错误')}")

    @staticmethod
    def red_packet_task_groups():
        return [
            ('SIGN', '签到'),
            ('NOVICE', '新手任务'),
            ('DAILY', '每日任务'),
            ('MONTHLY', '每月任务'),
        ]

    def red_envelope_party(self):
        self.log(f'\n🧧 红包派对任务')
        if not self.login_red_packet():
            return
        data = self.get_red_packet_task_list()
        if not data:
            self.log('获取红包派对任务失败: 接口无响应')
            return
        header = data.get('header') or {}
        if str(header.get('status')) != '200':
            self.log(f"获取红包派对任务失败: {header.get('errMsg') or header.get('respMsg') or '未知错误'}")
            return
        task_list = data.get('data') or {}
        self.log_red_packet_balance()
        self.handle_red_packet_sign(task_list)
        for group, title in self.red_packet_task_groups():
            tasks = task_list.get(group) or []
            if not tasks:
                continue
            self.log(title)
            for task in tasks:
                self.handle_red_packet_task(task)
        self.log_red_packet_balance()


if __name__ == "__main__":
    env_name = 'yunpan'
    token = os.getenv(env_name)
    if not token:
        print(f'⛔️未获取到ck变量：请检查变量 {env_name} 是否填写')
        exit(0)

    cookies = re.split(r'[&]', token)
    print_startup_info(len(cookies))
    print_device_id_notice()
    print_storage_path_notice()

    for i, account_info in enumerate(cookies, start=1):
        print(f"\n======== ▷ 第 {i} 个账号 ◁ ========")
        yp_instance = YP(account_info)

        if not yp_instance.Authorization:
            print(f"⛔️ 账号 {i} 无效，跳过执行")
            continue

        yp_instance.session.cookies.clear()
        yp_instance.run()
        print("\n准备进行下一个账号")
        time.sleep(random.uniform(1, 3))


    msg = ""
    if err_accounts:
        msg += f"失效账号:\n{err_accounts}\n"

    msg += f"任务详情:\n{all_logs}\n"
    msg += f"云朵汇总:\n{user_amount}"

    print("\n================ 运行总结 ================")
    if err_accounts:
        print(f"❌ 失效账号:\n{err_accounts}")
    if user_amount:
        print(f"☁️ 云朵汇总:\n{user_amount}")

    msg = msg.replace('-', ' ').replace('.', ' ').replace('!', '！').replace('(', '（').replace(')', '）')
    msg = msg.replace('_', ' ').replace('=', ' ').replace('~', ' ').replace('{', ' ').replace('}', ' ').replace('|', ' ')

    send = load_send()

    if send:
        send('中国移动云盘任务信息', msg)
