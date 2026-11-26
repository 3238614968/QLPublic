#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 一汽奔腾 YOMI 会员每日任务🚗💨
# ============================================================
#
# 🌟 功能一览（每日全自动）：
#   ✅ 每日积分 / 成长值概览
#   ✅ 任务列表完成态检查 + 客户端任务上报
#   ✅ 车辆信息同步
#
# 🔑 环境变量（二选一，支持任意一种多账号写法）：
#
#   写法一（推荐 · 多账号用 @ 分隔）：
#     export BT_YOMI_ACCOUNTS="手机号#密码 或 手机号#短信登录临时用token
#     @手机号#密码
#     @手机号#密码"
#
#   写法二（多账号同名变量）：
#     BT_YOMI_ACCOUNTS="13800001111#yourPassword"
#     BT_YOMI_ACCOUNTS="13900002222#anotherPass"
#     （青龙会自动合并为两个账号）
#
#   账号格式：
#     手机号#登录密码          → 密码登录（推荐，全自动无感 🎯）
#     手机号#SMS:收到的验证码   → 短信验证码登录（验证码有时效，仅适合手动补跑）
#
#   可选变量：
#     BT_YOMI_DEVICES   品牌型号覆盖，多账号 @ 分隔，格式 "品牌#型号"，缺省自动生成 📱
#     BT_YOMI_NOTIFY    值为 "1" 时开启青龙通知 📢
#     BT_YOMI_DEBUG    值为 "1" 时打印接口调试日志 🐞
#
# 📦 依赖安装（青龙面板 - 依赖管理 - Python3）：
#     curl_cffi
#
# ⚠️ 温馨提示：
#   - 本项目仅用于个人账号的自动化维护，请合理使用 🤝
#
#  @auth: xiaohai
#  @data: 2026.11.26
# ============================================================

import base64
import hashlib
import json
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print('❌ 缺少依赖 curl_cffi，请在青龙【依赖管理-Python3】中安装后重试')
    sys.exit(1)

# ============================================================
# 协议常量（网关 / 签名 / 端点）
# ============================================================

BFF_HOST = 'https://yomi.fawcar.com.cn'
BFF_BASE = '/btyomi'
H5_HOST = 'https://yomi.fawcar.com.cn'
H5_BASE = '/serverApi'

APP_VERSION = '5.4.2'
H5_APP_VERSION = '3.5.8'

SIGN_SECRET = 'A22A90B0504033E3944A483CEB79B698'

UA_PREFIX = 'okhttp/4.4.0'
UA_SUFFIX = ' BTYomi_Android'

RSA_PUBLIC_KEY_B64 = (
    'MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDw2f4yWk8QybdL1fPgtVX/a15SjDJsRV+BLLD'
    'FZ0cSSWCkqTQ/TSD1xw50F0gb9YmWE6xuN/ov6CKdEgMSLpyLAwmXeBMg2p9mma4dl2pl3JUDFOg'
    'BUvWWZtAGruNrbrklijYDW18LEsAy4364T+/Iwn01K2JjoW/o3CBIjN9ONwIDAQAB'
)

SUCCESS_CODES = ('000000', '200')
LOGIN_REQUIRED_CODES = ('070301', '070302', '999998', '333333', '444444')

URL_LOGIN = BFF_HOST + BFF_BASE + '/usercenter/app/v1/v2/baseLogin'
URL_SMS_CODE = BFF_HOST + BFF_BASE + '/usercenter/app/v1/getVerifyCode'
URL_VEHICLE_SYNC = BFF_HOST + BFF_BASE + '/carcenter/app/v1/tsp/carInfo/v2/getTspVehicleInfoReform'
URL_FINISH_TASK = BFF_HOST + BFF_BASE + '/member/app/v1/member/finishTask'

URL_H5_MY_USER_INFO = H5_HOST + H5_BASE + '/usercenter/app/v1/getMyUserInfo'
URL_H5_TASK_FLAG = H5_HOST + H5_BASE + '/member/app/v1/member/getNewTaskByAid'
URL_H5_TASK_LIST = H5_HOST + H5_BASE + '/member/app/v1/member/getNoviceTaskList'
URL_H5_POINTS = H5_HOST + H5_BASE + '/member/app/v1/member/getPointsDetails'
URL_H5_GROWTH = H5_HOST + H5_BASE + '/member/app/v1/member/getGrowthDetails'

# 客户端任务上报定义
CLIENT_TASKS = [
    {'taskId': '1', 'tacticsCode': 'PR-BT-Open_pentium', 'money': '', 'changeDesc': '打开购奔腾'},
    {'taskId': '2', 'tacticsCode': 'PR-BT-Nearby_dealer', 'money': '5', 'changeDesc': '打开附近经销商'},
    {'taskId': '3', 'tacticsCode': 'PR-BT-Test_drive', 'money': '5', 'changeDesc': '打开试乘试驾'},
    {'taskId': '6', 'tacticsCode': 'PR-BT-Car_section', 'money': '5', 'changeDesc': '打开爱车板块，点击车型的【了解详情】'},
    {'taskId': '7', 'tacticsCode': 'PR-BT-Car_manual', 'money': '5', 'changeDesc': '打开车辆使用手册'},
    {'taskId': '8', 'tacticsCode': 'PR-BT-Strategy', 'money': '5', 'changeDesc': '打开积分攻略'},
    {'taskId': '15', 'tacticsCode': 'PR-BT-Circle_friends_1', 'money': '50', 'changeDesc': '转发内容至朋友圈'},
    {'taskId': '20', 'tacticsCode': 'PR-BT-Circle_friends_2', 'money': '50', 'changeDesc': '转发内容至朋友圈'},
]

# ============================================================
# 频控与运行时
# ============================================================

REQUEST_INTERVAL = 2.0      # 任意两次请求最小间隔（秒）
WRITE_INTERVAL = 10.0       # 写操作（任务上报）最小间隔（秒）
LOGIN_COOLDOWN = 600.0     # 两次登录最小冷却（秒）

SCRIPT_DIR = Path(__file__).resolve().parent
SESSION_DIR = SCRIPT_DIR / 'bt_yomi_sessions'

_last_request_ts = 0.0
_last_write_ts = 0.0
_last_login_ts = 0.0

DEBUG = os.environ.get('BT_YOMI_DEBUG') == '1'


def log(msg: str) -> None:
    print(msg, flush=True)


def debug(msg: str) -> None:
    if DEBUG:
        log('🔍 [debug] ' + msg)


def throttle(kind: str) -> None:
    """请求前频控等待：kind ∈ query / write / login 🐢"""
    global _last_request_ts, _last_write_ts, _last_login_ts
    if kind == 'login':
        gap = LOGIN_COOLDOWN - (time.time() - _last_login_ts)
        if gap > 0:
            log(f'⏳ 登录冷却中，等待 {int(gap) + 1}s ...')
            time.sleep(gap + 0.05)
        _last_login_ts = time.time()
    elif kind == 'write':
        gap = WRITE_INTERVAL - (time.time() - _last_write_ts)
        if gap > 0:
            time.sleep(gap + 0.05)
        _last_write_ts = time.time()
    while True:
        gap = REQUEST_INTERVAL - (time.time() - _last_request_ts)
        if gap <= 0:
            break
        time.sleep(gap)
    _last_request_ts = time.time()


# ============================================================
# 签名与加密
# ============================================================


def calc_aign(timestamp: str, nonce: str) -> str:
    """BFF/H5 共用签名：lower(MD5("Timestamp=..&Nonce=..&Secretkey=.."))"""
    raw = f'Timestamp={timestamp}&Nonce={nonce}&Secretkey={SIGN_SECRET}'
    return hashlib.md5(raw.encode('utf-8')).hexdigest().lower()


def rsa_encrypt_password(plaintext: str) -> str:
    """登录密码 RSA/ECB/PKCS1Padding 加密，输出单行 Base64"""
    try:
        from Crypto.Cipher import PKCS1_v1_5
        from Crypto.PublicKey import RSA
    except ImportError:
        print('❌ 密码登录需要 pycryptodome，请在青龙【依赖管理-Python3】中安装')
        raise
    key = RSA.import_key(base64.b64decode(RSA_PUBLIC_KEY_B64))
    return base64.b64encode(PKCS1_v1_5.new(key).encrypt(plaintext.encode('utf-8'))).decode('ascii')


# ============================================================
# 会话账号对象
# ============================================================


class YomiAccount:
    SESSION_FIELDS = ('id_token', 'userId', 'userCode', 'shortName', 'aid', 'token1',
                      'token2', 'accType', 'userplat', 'verifyType', 'auditStatus',
                      'useType', 'qrCode', 'tspAid', 'avatar')

    def __init__(self, index: int, phone: str, secret: str, brand: str, model: str):
        self.index = index
        self.phone = phone
        self.secret = secret          # 密码 或 SMS:验证码
        self.brand = brand or 'Xiaomi'
        self.model = model or 'MI 10'
        self.session_file = SESSION_DIR / f'session_{phone}.json'
        self.auth: dict = {}
        self.device_id = ''
        self.http = cffi_requests.Session(impersonate='chrome131_android')
        self._load_session()

    # ---------- 本地会话持久化 ----------
    def _load_session(self) -> None:
        try:
            if self.session_file.is_file():
                data = json.loads(self.session_file.read_text(encoding='utf-8'))
                self.auth = data.get('auth') or {}
                self.device_id = data.get('device_id') or ''
                log(f'📦 [{self.phone}] 已加载本地会话缓存')
        except Exception as exc:
            debug(f'读取会话缓存失败: {exc}')

    def _save_session(self) -> None:
        try:
            SESSION_DIR.mkdir(parents=True, exist_ok=True)
            payload = {'auth': self.auth, 'device_id': self.device_id,
                       'saved_at': int(time.time())}
            self.session_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                          encoding='utf-8')
        except Exception as exc:
            debug(f'保存会话缓存失败: {exc}')

    def ensure_device_id(self) -> str:
        if not self.device_id:
            self.device_id = str(uuid.uuid4())
            self._save_session()
        return self.device_id

    def has_session(self) -> bool:
        return bool(str(self.auth.get('id_token') or '').strip())

    def update_auth(self, account_vo: dict) -> None:
        for field in self.SESSION_FIELDS:
            if account_vo.get(field) is not None:
                self.auth[field] = account_vo[field]
        self._save_session()

    # ---------- 请求头装配 ----------
    def _native_headers(self, ts: str, nonce: str, aign: str) -> dict:
        auth = self.auth
        headers = {
            'Content-Type': 'application/json',
            'accept': '*/*',
            'V-Origin': 'Android',
            'Device-Type': self.model,
            'APP-Version': APP_VERSION,
            'Id-Token': str(auth.get('id_token') or ''),
            'User-Id': str(auth.get('userId') or ''),
            'User-Aid': str(auth.get('aid') or ''),
            'User-Code': str(auth.get('userCode') or ''),
            'User-Agent': f'{UA_PREFIX} {UA_SUFFIX}'.strip(),
            'Tsp-Token1': str(auth.get('token1') or ''),
            'Tsp-Token2': str(auth.get('token2') or ''),
            'Timestamp': ts,
            'Nonce': nonce,
            'Aign': aign,
        }
        return headers

    def _h5_headers(self, ts: str, nonce: str, aign: str) -> dict:
        auth = self.auth
        return {
            'Content-Type': 'application/json;charset=UTF-8',
            'aign': aign,
            'timestamp': ts,
            'nonce': nonce,
            'APP-Version': H5_APP_VERSION,
            'Tsp-Token1': str(auth.get('token1') or ''),
            'Tsp-Token2': str(auth.get('token2') or ''),
            'userPlat': str(auth.get('userplat') or ''),
            'User-Id': str(auth.get('userId') or ''),
            'User-Code': str(auth.get('userCode') or ''),
            'User-Aid': str(auth.get('aid') or ''),
            'ID-TOKEN': str(auth.get('id_token') or ''),
        }

    # ---------- HTTP ----------
    def _send(self, method: str, url: str, *, channel: str, params: dict | None = None,
              json_body: dict | None = None, throttle_kind: str = 'query',
              timeout: int = 30) -> dict:
        throttle(throttle_kind)
        ts = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())
        aign = calc_aign(ts, nonce)
        headers = (self._native_headers(ts, nonce, aign) if channel == 'native'
                   else self._h5_headers(ts, nonce, aign))
        query = dict(params) if params else {}
        # H5 GET 追加时间戳防缓存参数（与 H5 网关行为一致）
        if channel == 'h5' and method.upper() == 'GET':
            query[ts] = ts
        debug(f'{method} {url} params={query}')
        try:
            if method.upper() == 'GET':
                resp = self.http.get(url, params=query, headers=headers, timeout=timeout)
            else:
                resp = self.http.post(url, params=query, json=json_body or {},
                                      headers=headers, timeout=timeout)
        except Exception as exc:
            return {'code': 'NETWORK_ERROR', 'msg': str(exc)}
        try:
            body = resp.json()
        except Exception:
            body = {'code': str(resp.status_code), 'msg': 'NON_JSON_RESPONSE',
                    'raw': str(resp.text)[:300]}
        debug(f'← {json.dumps(body, ensure_ascii=False)[:300]}')
        return body

    def native_get(self, url: str, params: dict | None = None, **kw) -> dict:
        return self._send('GET', url, channel='native', params=params, **kw)

    def native_post(self, url: str, json_body: dict | None = None, **kw) -> dict:
        return self._send('POST', url, channel='native', json_body=json_body, **kw)

    def h5_get(self, url: str, params: dict | None = None, **kw) -> dict:
        return self._send('GET', url, channel='h5', params=params, **kw)

    # ---------- 登录 ----------
    def login(self) -> bool:
        if not re.fullmatch(r'(1[0-9])\d{9}', self.phone):
            log(f'❌ [{self.phone}] 手机号格式不正确，跳过')
            return False
        if self.secret.startswith('SMS:'):
            mode, credential = 'sms', self.secret[4:]
            if not credential:
                log(f'❌ [{self.phone}] 短信登录缺少验证码（格式：手机号#SMS:验证码）')
                return False
        else:
            mode, credential = 'password', self.secret
            if not credential:
                log(f'❌ [{self.phone}] 登录密码为空')
                return False
        body = {
            'account': self.phone,
            'tspLoginRequest': {
                'deviceId': self.ensure_device_id(),
                'brand': self.brand,
                'model': self.model,
            },
        }
        if mode == 'password':
            body['password'] = rsa_encrypt_password(credential)
        else:
            body['verifyCode'] = credential
        log(f'🔑 [{self.phone}] 正在登录（{ "密码" if mode == "password" else "短信验证码" }）...')
        resp = self.native_post(URL_LOGIN, json_body=body, throttle_kind='login', timeout=40)
        code = str(resp.get('code') or '')
        if code in ('000000', '200'):
            data = resp.get('data') or {}
            if not isinstance(data, dict) or not data:
                log(f'❌ [{self.phone}] 登录成功但响应缺少账号数据')
                return False
            self.update_auth(data)
            log(f'✅ [{self.phone}] 登录成功 🎉')
            self.sync_vehicle()
            return True
        if code == '444444':
            log(f'ℹ️ [{self.phone}] 账号处于注销冷静期，携带放弃注销参数重试...')
            body['continueToLogIn'] = '1'
            resp = self.native_post(URL_LOGIN, json_body=body, timeout=40)
            if str(resp.get('code') or '') in ('000000', '200'):
                data = resp.get('data') or {}
                if isinstance(data, dict) and data:
                    self.update_auth(data)
                    log(f'✅ [{self.phone}] 登录成功 🎉')
                    self.sync_vehicle()
                    return True
            log(f'❌ [{self.phone}] 重试登录失败: {resp.get("msg")}')
            return False
        if code == '752540':
            log(f'❌ [{self.phone}] 服务端要求该账号使用短信验证码登录')
        elif code == '777777':
            log(f'❌ [{self.phone}] 手机号尚未注册')
        elif code == '000401':
            log(f'❌ [{self.phone}] 密码不符合规则或已过期，需要重置密码')
        else:
            log(f'❌ [{self.phone}] 登录失败: code={code} msg={resp.get("msg")}')
        return False

    def sync_vehicle(self) -> None:
        try:
            resp = self.native_get(URL_VEHICLE_SYNC)
            if str(resp.get('code') or '') in SUCCESS_CODES and isinstance(resp.get('data'), dict):
                infos = (resp['data'].get('vehicleInfos')
                         or resp['data'].get('vehicleInfoList') or [])
                log(f'🚗 [{self.phone}] 已同步 {len(infos)} 辆绑定车辆')
        except Exception as exc:
            debug(f'车辆同步异常: {exc}')

    # ---------- 业务查询 ----------
    def my_user_info(self) -> dict:
        return self.h5_get(URL_H5_MY_USER_INFO,
                           params={'userPlat': str(self.auth.get('userplat') or '')})

    def task_flag(self) -> dict:
        return self.h5_get(URL_H5_TASK_FLAG)

    def task_list(self) -> dict:
        return self.h5_get(URL_H5_TASK_LIST)

    def points_detail(self) -> dict:
        month = time.strftime('%Y-%m')
        return self.h5_get(URL_H5_POINTS, params={'page': 1, 'pageSize': 10, 'queryDate': month})

    def growth_detail(self) -> dict:
        return self.h5_get(URL_H5_GROWTH, params={'page': 1, 'pageSize': 10})

    def finish_task(self, params: dict) -> dict:
        return self.native_get(URL_FINISH_TASK, params=params, throttle_kind='write')


# ============================================================
# 账号解析
# ============================================================


def parse_accounts() -> list:
    raw_list = []

    def _collect(value: str) -> None:
        for part in re.split(r'[@\n]', str(value or '')):
            part = part.strip()
            if part:
                raw_list.append(part)

    # 青龙多变量：环境变量管理里同名 BT_YOMI_ACCOUNTS 写多行会以 & 或换行拼接
    for key in ('BT_YOMI_ACCOUNTS',):
        val = os.environ.get(key)
        if val:
            # 青龙脚本参数（famix 语法）可能传入多个同名变量，用 & 分隔
            _collect(val.replace('&', '\n'))

    if not raw_list:
        return []

    devices: list = []
    dev_val = os.environ.get('BT_YOMI_DEVICES')
    if dev_val:
        devices = [p.strip() for p in re.split(r'[@\n&]', dev_val) if p.strip()]

    accounts = []
    for idx, raw in enumerate(raw_list):
        parts = raw.split('#')
        phone = parts[0].strip()
        secret = '#'.join(parts[1:]).strip() if len(parts) > 1 else ''
        brand, model = 'Xiaomi', 'MI 10'
        if idx < len(devices):
            dev_parts = devices[idx].split('#')
            brand = dev_parts[0].strip() or brand
            if len(dev_parts) > 1:
                model = dev_parts[1].strip() or model
        accounts.append((idx + 1, phone, secret, brand, model))
    return accounts


# ============================================================
# 单账号主流程
# ============================================================


def is_login_expired(resp: dict) -> bool:
    return str(resp.get('code') or '') in LOGIN_REQUIRED_CODES


def run_account(index: int, phone: str, secret: str, brand: str, model: str) -> str:
    """跑一个账号，返回通知文本。🏁"""
    log('')
    log(f'{"=" * 46}')
    log(f'🚗 账号 {index}: {phone[:3]}****{phone[-4:] if len(phone) >= 7 else phone}')
    log(f'{"=" * 46}')
    acc = YomiAccount(index, phone, secret, brand, model)

    # 1) 会话：优先复用本地缓存
    if not acc.has_session():
        if not secret:
            log(f'❌ [{phone}] 无本地会话且未配置密码，跳过')
            return f'❌ {phone[:3]}****{phone[-4:]}: 无会话且未配置密码'
        if not acc.login():
            return f'❌ {phone[:3]}****{phone[-4:]}: 登录失败'
    else:
        log(f'♻️ [{phone}] 复用本地会话 id_token 🎫')

    # 2) 验证会话有效性：查会员信息
    info = acc.my_user_info()
    if is_login_expired(info):
        log(f'⚠️ [{phone}] 会话已失效，重新登录...')
        if not secret:
            log(f'❌ [{phone}] 会话失效且未配置密码，跳过')
            return f'❌ {phone[:3]}****{phone[-4:]}: 会话失效且未配置密码'
        if not acc.login():
            return f'❌ {phone[:3]}****{phone[-4:]}: 重新登录失败'
        info = acc.my_user_info()
        if is_login_expired(info):
            return f'❌ {phone[:3]}****{phone[-4:]}: 登录后仍无法访问会员信息'
    elif str(info.get('code') or '') not in SUCCESS_CODES:
        log(f'⚠️ [{phone}] 会员信息查询异常: code={info.get("code")} msg={info.get("msg")}，继续执行任务')

    # 3) 概览：积分 / 成长值
    summary_lines = []
    info_data = info.get('data') if isinstance(info.get('data'), dict) else {}
    nickname = info_data.get('vshortname') or info_data.get('customerName') or '会员'
    if str(info.get('code') or '') in SUCCESS_CODES:
        integral = info_data.get('integral')
        growth = info_data.get('growthValue')
        level = info_data.get('level')
        if integral is not None or growth is not None:
            summary_lines.append(f'积分 {integral or 0} 💰 成长值 {growth or 0} 🌱')
        if level:
            summary_lines.append(f'等级 Lv.{level}')
        log(f'👤 [{phone}] {nickname} | ' + ' | '.join(summary_lines))

    # 4) 任务面板 + 完成态
    tasks_resp = acc.task_list()
    today = time.strftime('%Y-%m-%d')
    panel: dict = {}
    if str(tasks_resp.get('code') or '') in SUCCESS_CODES and isinstance(tasks_resp.get('data'), list):
        for t in tasks_resp['data']:
            if isinstance(t, dict) and t.get('taskName'):
                # App 判据：taskStatus = "0" 表示已完成
                panel[str(t.get('taskName'))] = (str(t.get('taskStatus')) == '0')
        log(f'📋 [{phone}] 任务面板共 {len(panel)} 项（已完成 {sum(panel.values())} 项）')
    else:
        log(f'⚠️ [{phone}] 任务面板查询异常: code={tasks_resp.get("code")} msg={tasks_resp.get("msg")}')

    # 5) 当日积分流水（用于判重 + 展示今日收入）
    today_records = []
    points = acc.points_detail()
    if str(points.get('code') or '') in SUCCESS_CODES and isinstance(points.get('data'), dict):
        records = points['data'].get('memberIntegralResList') or []
        for r in records:
            if isinstance(r, dict) and str(r.get('createDate') or '').startswith(today):
                today_records.append(r)
    done_descs = {str(r.get('changeDesc') or '') for r in today_records}
    if today_records:
        earned = sum(int(r.get('changeIntegral') or 0) for r in today_records
                     if str(r.get('changeIntegral') or '').lstrip('-').isdigit())
        log(f'🪙 [{phone}] 今日积分流水 {len(today_records)} 条，合计 {earned:+d} 分')

    # 6) 防重执行客户端任务上报
    executed, skipped = [], []
    for task in CLIENT_TASKS:
        desc = task['changeDesc']
        if panel.get(desc):
            skipped.append(desc)
            continue
        if desc in done_descs:
            skipped.append(desc)
            continue
        log(f'📝 [{phone}] 上报任务: {desc} ...')
        resp = acc.finish_task(dict(task))
        if str(resp.get('code') or '') in SUCCESS_CODES:
            executed.append(f'{desc} +{task["money"] or 0}分')
            log(f'✅ [{phone}] {desc} 完成 🎉')
        else:
            if is_login_expired(resp):
                log(f'❌ [{phone}] 会话在任务执行中失效，停止后续任务')
                break
            log(f'❌ [{phone}] {desc} 上报失败: code={resp.get("code")} msg={resp.get("msg")}')

    # 7) 汇总
    lines = [f'🚗 {nickname}（{phone[:3]}****{phone[-4:] if len(phone) >= 7 else phone}）']
    if summary_lines:
        lines.append('  ' + ' | '.join(summary_lines))
    if today_records:
        earned = sum(int(r.get('changeIntegral') or 0) for r in today_records
                     if str(r.get('changeIntegral') or '').lstrip('-').isdigit())
        lines.append(f'  🪙 今日流水 {len(today_records)} 条 / {earned:+d} 分')
    if executed:
        lines.append(f'  ✅ 本次完成任务 {len(executed)} 项: ' + '、'.join(executed))
    if skipped:
        lines.append(f'  ⏭️ 已完成跳过 {len(skipped)} 项')
    if not executed and not skipped:
        lines.append('  📋 今日暂无可上报的客户端任务')
    return '\n'.join(lines)


# ============================================================
# 通知（青龙 sendNotify）
# ============================================================


def send_notify(title: str, content: str) -> None:
    if os.environ.get('BT_YOMI_NOTIFY') != '1':
        return
    try:
        for p in (os.getcwd(), str(SCRIPT_DIR)):
            if p and p not in sys.path:
                sys.path.append(p)
        from sendNotify import send  # type: ignore
        send(title, content)
        log('📢 通知已发送')
    except Exception as exc:
        log(f'⚠️ 通知发送失败: {exc}')


# ============================================================
# 主入口
# ============================================================


def main() -> int:
    log('🚗💨 奔腾 YOMI 每日任务')
    log(f'⏰ {time.strftime("%Y-%m-%d %H:%M:%S")}')
    accounts = parse_accounts()
    if not accounts:
        log('')
        log('❌ 未配置环境变量 BT_YOMI_ACCOUNTS')
        log('   示例（多账号 @ 分隔）:')
        log('   export BT_YOMI_ACCOUNTS="13800001111#password1@13900002222#password2"')
        log('   或在青龙环境变量中添加多个同名 BT_YOMI_ACCOUNTS 变量（每行一个账号）')
        return 1

    log(f'📋 共解析到 {len(accounts)} 个账号，开始执行...\n')
    results = []
    for idx, phone, secret, brand, model in accounts:
        try:
            results.append(run_account(idx, phone, secret, brand, model))
        except Exception as exc:
            log(f'❌ [{phone}] 执行异常: {type(exc).__name__}: {exc}')
            results.append(f'❌ {phone}: 执行异常 {type(exc).__name__}')
        if idx < len(accounts):
            time.sleep(random.uniform(3, 6))

    log('')
    log('📊 ===== 执行汇总 =====')
    for r in results:
        log(r)
        log('')
    send_notify('🚗 奔腾 YOMI 每日任务', '\n\n'.join(results))
    log('🏁 全部账号执行完毕，下次再见 👋')
    return 0


if __name__ == '__main__':
    sys.exit(main())
