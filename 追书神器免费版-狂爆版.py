"""
追书神器免费版 - 狂暴版
@author: xiaohai
@date: 2025-7-11
环境变量: zssq (支持通过青龙 API 读取多个同名项)
格式: token 或 token#android_id 或 device_id#android_id#token
多用户: @ 隔开 或 换行
循环获取金币，直至抵达上线80000
金币80000上限时，本账号自动跳过
"""

import sys
import os
import json
import time
import random
import string
import base64
import hashlib
import logging
import binascii
from datetime import datetime

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
# 代理
PROXY_API_URL = ""
PROXY_LEASE_SECONDS = 120
PROXY_REFRESH_BUFFER = 10


class QinglongClient:
    def __init__(self):
        self.base_url = os.getenv("QL_API_URL") or "http://127.0.0.1:5700"
        self.client_id = os.getenv("QL_CLIENT_ID")
        self.client_secret = os.getenv("QL_CLIENT_SECRET")
        self.token = None

    def is_configured(self):
        return bool(self.client_id and self.client_secret)

    def get_token(self):
        if self.token:
            return self.token
        url = "{}/open/auth/token".format(self.base_url.rstrip("/"))
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200 or not data.get("data", {}).get("token"):
            raise ValueError("获取青龙 Token 失败: {}".format(data.get("message", "未知错误")))
        self.token = data["data"]["token"]
        return self.token

    def get_envs(self, name):
        token = self.get_token()
        url = "{}/open/envs".format(self.base_url.rstrip("/"))
        headers = {"Authorization": "Bearer {}".format(token)}
        params = {"searchValue": name}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            raise ValueError("获取青龙环境变量失败: {}".format(data.get("message", "未知错误")))
        return [env for env in data.get("data", []) if env.get("name") == name]


class ProxyManager:
    def __init__(self, proxy_api_url):
        self.proxy_api_url = proxy_api_url
        self.current_proxy = None
        self.expires_at = 0

    def _parse_proxy_text(self, text):
        proxy = text.strip().splitlines()[0].strip()
        if not proxy or ":" not in proxy:
            raise ValueError("代理接口返回格式无效: {}".format(text.strip()[:100]))
        return proxy

    def fetch_new_proxy(self):
        resp = requests.get(self.proxy_api_url, timeout=15)
        resp.raise_for_status()
        proxy = self._parse_proxy_text(resp.text)
        self.current_proxy = proxy
        self.expires_at = time.time() + PROXY_LEASE_SECONDS
        log_info("代理已更新: {} (有效期 {} 秒)".format(proxy, PROXY_LEASE_SECONDS))
        return proxy

    def needs_refresh(self):
        if not self.current_proxy:
            return True
        return time.time() >= (self.expires_at - PROXY_REFRESH_BUFFER)

    def get_proxy(self, force_refresh=False):
        if force_refresh or self.needs_refresh():
            return self.fetch_new_proxy()
        return self.current_proxy

    def mark_bad_proxy(self):
        if self.current_proxy:
            log_warn("当前代理不可用，立即更换: {}".format(self.current_proxy))
        self.current_proxy = None
        self.expires_at = 0

# ============================================================
#  常量
# ============================================================

GOLD_BASE_URL = "https://goldcoinnew.zhuishushenqi.com"
GOLD_BASE_URL_OLD = "https://goldcoin.zhuishushenqi.com"

APK_CHANNEL = "FTencent"
PACKAGE_NAME = "com.ushaqi.zhuishushenqi.adfree"
APP_VERSION = "3.45.92"
PROMOTER_ID = "100001505"
CLIENT_ID_BASE = "android-xiaomi"
DEVICE_MODEL = "23049RAD8C"
DEVICE_BRAND = "Xiaomi"
DEVICE_OS = "Android"
DEVICE_OS_VERSION = "15"

CHANNEL_ID = "F1d36851BC0e5943042b261dFcFEd0e5"
TOKEN_AES_KEY = "5fFf6D94079904826ab080B8179E9376"
H5_CHANNEL = "4e894B20a07c80331d1AC8A7e1b6c140"
H5_AES_KEY = "34Ba50f5a51Be0192B4a6Dbb0AC51c30"

H5_IV_CHARSET = (
    "ABCDEFGHIJKMNOPQRSTUVWXYZ"
    "abcdefghijkmnopqrstuvwxyz"
    "0123456789"
)

CM3_AES_KEY = b"+BNC7v+HXyt7bJlp"
CM3_AES_IV = b"581ec9051f4cb2e6"

H5_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 15; 23049RAD8C Build/AQ3A.250226.002; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/131.0.6778.260 Mobile Safari/537.36"
)
APP_USER_AGENT = (
    "ZhuiShuShenQi/{} ({} {}; {} Marble / Redmi {}; )"
    "[preload=false;locale=zh_CN;clientidbase={}]"
).format(APP_VERSION, DEVICE_OS, DEVICE_OS_VERSION, DEVICE_BRAND, DEVICE_MODEL, CLIENT_ID_BASE)

TASK_CONFIG = {
    "rw-self-chengyu": {"name": "成语小秀才", "interval": 5},
    "rw-self-datiwangzhe": {"name": "答题王者", "interval": 5},
    "rw-fuli-mall-task": {"name": "福利商城", "interval": 5},
}

MAX_RETRY = 2
TASK_WAIT_RANGE = (8, 15)
TASK_SWITCH_WAIT = 5
ROUND_WAIT_RANGE = (5, 8)
ACCOUNT_WAIT_RANGE = (3, 8)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

_COUNTER = random.randint(0, 0xFFFFFF)

# ============================================================
#  工具函数
# ============================================================

def md5_hex(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def next_counter():
    global _COUNTER
    value = _COUNTER
    _COUNTER = (_COUNTER + 1) & 0xFFFFFF
    return value

def generate_object_id(android_id):
    ts_hex = "{:08x}".format(int(time.time()) & 0xFFFFFFFF)
    machine_hex = md5_hex(android_id)[:6]
    pid_hex = "{:04x}".format(random.randint(1, 65535))
    counter_hex = "{:06x}".format(next_counter())
    return ts_hex + machine_hex + pid_hex + counter_hex

def encode_android_id(android_id):
    return base64.b64encode(android_id.encode("utf-8")).decode("ascii")

def generate_random_android_id(length=24):
    charset = string.ascii_lowercase + string.digits
    return "".join(random.choice(charset) for _ in range(length))

def generate_random_device_info():
    android_id = generate_random_android_id()
    device_id = encode_android_id(android_id)
    return android_id, device_id

def random_wait_seconds(wait_range):
    return random.randint(wait_range[0], wait_range[1])

def generate_third_token(h5_mode=False):
    if h5_mode:
        ts = int(time.time() * 1000)
        plaintext = json.dumps({"time": ts}, separators=(",", ":")).encode("utf-8")
        channel = H5_CHANNEL
        key = H5_AES_KEY.encode("utf-8")
        iv = "".join(random.choice(H5_IV_CHARSET) for _ in range(12)).encode("ascii")
    else:
        ts = int(time.time())
        plaintext = json.dumps({"time": ts}, separators=(",", ":")).encode("utf-8")
        channel = CHANNEL_ID
        key = bytes.fromhex(TOKEN_AES_KEY)
        iv = os.urandom(12)

    aad = channel.encode("utf-8")
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    cipher.update(aad)
    ct_bytes, tag_bytes = cipher.encrypt_and_digest(plaintext)

    if h5_mode:
        combined = binascii.hexlify(iv + ct_bytes + tag_bytes).decode()
        return "{}:{}".format(channel, combined)
    else:
        iv_hex = binascii.hexlify(iv).decode()
        ct_tag_hex = binascii.hexlify(ct_bytes + tag_bytes).decode()
        return "{}:{}:{}".format(channel, iv_hex, ct_tag_hex)

def encrypt_cm3(plaintext_str, android_id):
    cipher = AES.new(CM3_AES_KEY, AES.MODE_CBC, CM3_AES_IV)
    ct = cipher.encrypt(pad(plaintext_str.encode("utf-8"), 16))
    ct_list = bytearray(ct)
    first_byte = android_id.encode("utf-8")[0]
    ct_list[0] = ct_list[0] ^ first_byte
    return base64.b64encode(bytes(ct_list)).decode("ascii")

# ============================================================
#  日志美化
# ============================================================

def log_title(text):
    print("\n" + "=" * 50)
    print("  {}".format(text))
    print("=" * 50)

def log_section(text):
    print("\n  {} {}".format(text, "-" * (45 - len(text) * 2)))

def log_ok(text):
    print("    {} {}".format("\u2705", text))

def log_err(text):
    print("    {} {}".format("\u274c", text))

def log_info(text):
    print("    {} {}".format("\u2139\ufe0f", text))

def log_warn(text):
    print("    {} {}".format("\u26a0\ufe0f", text))

def log_coin(text):
    print("    {} {}".format("\U0001f4b0", text))

def log_task(text):
    print("    {} {}".format("\U0001f3af", text))

def log_star(text):
    print("    {} {}".format("\u2b50", text))

def log_loop(text):
    print("  {} {}".format("\U0001f504", text))

# ============================================================
#  客户端
# ============================================================

class ZssqClient:
    def __init__(self, token, android_id, device_id, uid, proxy_manager=None):
        self.token = token
        self.android_id = android_id
        self.device_id = device_id
        self.uid = uid
        self.zs_login_id = uid
        self.user_uid = uid
        self.first_install_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.proxy_manager = proxy_manager
        self.session = requests.Session()

    def _ensure_proxy(self, force_refresh=False):
        if not self.proxy_manager:
            return
        proxy = self.proxy_manager.get_proxy(force_refresh=force_refresh)
        proxy_url = "http://{}".format(proxy)
        self.session.proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

    def _request(self, method, url, **kwargs):
        last_error = None
        for force_refresh in (False, True):
            try:
                self._ensure_proxy(force_refresh=force_refresh)
                return self.session.request(method, url, **kwargs)
            except requests.RequestException as e:
                last_error = e
                if not self.proxy_manager:
                    break
                self.proxy_manager.mark_bad_proxy()
                log_warn("请求失败，准备切换代理重试: {}".format(e))
        if last_error is None:
            raise RuntimeError("请求失败，未捕获到具体异常")
        raise last_error

    def _build_ext_data(self, extra=None):
        ext = {
            "platform": "2",
            "product_line": 6,
            "apk_channel": APK_CHANNEL,
            "is_vip": False,
            "zs_login_id": self.zs_login_id,
            "channel_name": APK_CHANNEL,
            "channel_id": PROMOTER_ID,
            "new_user_welfare": False,
            "pub_app_first_install_time": self.first_install_time,
            "graytest_mark": APP_VERSION,
            "$app_version": APP_VERSION,
            "ua_channel_id": PROMOTER_ID,
            "ua_channel_name": APK_CHANNEL,
            "red_user_current_level": 0,
            "red_strategy_number": 0,
            "red_user_getmoney_initial_level": 1,
            "user_ad_strategypositionId": "429",
            "user_uid": self.user_uid,
            "ab_testname": "",
            "ab_groupname": "",
            "is_charging": False,
            "battery_power": -1,
            "user_type": "",
            "feature_code": "Welfare",
            "group_assignment": "default",
        }
        if extra:
            ext.update(extra)
        return json.dumps(ext, separators=(",", ":"))

    def _get_h5_headers(self):
        return {
            "Host": "goldcoinnew.zhuishushenqi.com",
            "Connection": "keep-alive",
            "sec-ch-ua-platform": '"Android"',
            "User-Agent": H5_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "sec-ch-ua": '"Android WebView";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?1",
            "Origin": "https://h5.zhuishushenqi.com",
            "X-Requested-With": PACKAGE_NAME,
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://h5.zhuishushenqi.com/",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    def _get_app_headers(self):
        return {
            "X-Device-Id": self.device_id,
            "X-User-Agent": APP_USER_AGENT,
            "x-android-id": self.device_id,
            "B-Zssq": self.device_id,
            "X-Channel": APK_CHANNEL,
            "weskitType": "free",
            "User-Agent": APP_USER_AGENT,
            "x-app-name": "zhuishuFree",
            "X-Uid": self.uid,
            "Content-Type": "application/json; charset=UTF-8",
            "Host": "goldcoinnew.zhuishushenqi.com",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }

    @staticmethod
    def _is_ok(result):
        if not isinstance(result, dict):
            return False
        return result.get("ok") is True or result.get("ecode") == 0

    @staticmethod
    def _get_error_msg(result):
        if not isinstance(result, dict):
            return str(result)
        for key in ("message", "msg", "error"):
            val = result.get(key)
            if val:
                return str(val)
        code = result.get("code")
        if code is not None:
            return "[{}]".format(code)
        return str(result)

    def daily_sign(self):
        url = "{}/user/do-sign".format(GOLD_BASE_URL_OLD)
        params = {
            "token": self.token,
            "taskAttach": 1,
            "b-zssq": self.device_id,
            "extData": self._build_ext_data(),
        }
        try:
            resp = self._request("GET", url, params=params, timeout=30)
            return resp.json()
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def get_profile(self):
        url = "{}/account/profile".format(GOLD_BASE_URL)
        params = {
            "token": self.token,
            "channel": "undefined",
            "b-zssq": self.device_id,
        }
        try:
            resp = self._request("GET", url, params=params, headers=self._get_h5_headers(), timeout=30)
            return resp.json()
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def get_task_overview(self):
        url = "{}/redPacket/tasks".format(GOLD_BASE_URL)
        params = {
            "token": self.token,
            "channel": APK_CHANNEL,
            "position": "",
            "taskVersion": 2011,
            "version": APP_VERSION,
            "readTimeReadWelfare": 0,
            "b-zssq": self.device_id,
        }
        try:
            resp = self._request("GET", url, params=params, headers=self._get_h5_headers(), timeout=30)
            return resp.json()
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _complete_task(self, action, task_name=None):
        url = "{}/redPacket/v3/completeTask".format(GOLD_BASE_URL)
        params = {
            "token": self.token,
            "b-zssq": self.device_id,
        }
        extra = {}
        if task_name:
            extra["task_name"] = task_name
        payload = {
            "action": action,
            "channel": APK_CHANNEL,
            "position": "goldTask",
            "taskVersion": 2011,
            "version": APP_VERSION,
            "thirdToken": generate_third_token(h5_mode=True),
            "extData": self._build_ext_data(extra=extra),
        }
        headers = self._get_h5_headers()
        headers["Content-Type"] = "application/json; charset=UTF-8"
        try:
            resp = self._request("POST", url, params=params, json=payload, headers=headers, timeout=30)
            return resp.json()
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _complete_task_app_headers(self, action):
        url = "{}/redPacket/v3/completeTask".format(GOLD_BASE_URL)
        sm = "202308070448523845e858524f1092956f31e90d27130401563d523b3f1b39"
        params = {
            "token": self.token,
            "sm": sm,
        }
        payload = {
            "action": action,
            "channel": APK_CHANNEL,
            "extData": self._build_ext_data(),
            "packageName": PACKAGE_NAME,
            "taskVersion": 2011,
            "thirdToken": generate_third_token(h5_mode=True),
            "version": APP_VERSION,
        }
        try:
            resp = self._request("POST", url, params=params, json=payload, headers=self._get_app_headers(), timeout=30)
            return resp.json()
        except Exception as e:
            return {"ok": False, "message": str(e)}

# ============================================================
#  环境变量解析
# ============================================================

def parse_users():
    """
    优先通过青龙 API 读取全部 zssq 环境变量，失败时回退到进程环境变量
    
    格式支持：
    1. device_id#android_id#token
    2. token#android_id
    3. token
    
    多账号：@ 分隔 或 换行分隔
    """
    env_values = []

    ql = QinglongClient()
    if ql.is_configured():
        try:
            envs = ql.get_envs("zssq")
            env_values = [str(env.get("value", "")).strip() for env in envs if str(env.get("value", "")).strip()]
            if env_values:
                log_info("通过青龙 API 读取到 {} 个 zssq 环境变量".format(len(env_values)))
        except Exception as e:
            log_warn("通过青龙 API 读取 zssq 失败，回退本地环境变量: {}".format(e))

    if not env_values:
        raw = os.environ.get("zssq", "").strip()
        if raw:
            env_values.append(raw)

    if not env_values:
        return []

    parts = []
    for raw in env_values:
        if not raw:
            continue
        for segment in raw.replace("\n", "@").split("@"):
            s = segment.strip()
            if s:
                parts.append(s)

    users = []
    for part in parts:
        fields = part.split("#")
        
        if len(fields) >= 3:
            users.append({
                "token": fields[2].strip(),
                "android_id": fields[1].strip(),
                "device_id": fields[0].strip(),
            })
        elif len(fields) == 2:
            android_id = fields[1].strip()
            device_id = base64.b64encode(android_id.encode()).decode('ascii')
            users.append({
                "token": fields[0].strip(),
                "android_id": android_id,
                "device_id": device_id,
            })
        elif len(fields) == 1 and fields[0].strip():
            users.append({
                "token": fields[0].strip(),
                "android_id": None,
                "device_id": None,
            })
        else:
            log_warn("\u26a0\ufe0f \u683c\u5f0f\u4e0d\u5b8c\u6574\uff0c\u8df4\u8fc7: {}...".format(part[:20]))
    
    return users

# ============================================================
#  任务执行
# ============================================================

def do_single_task(client, action):
    if action == "rw-self-chengyu":
        return client._complete_task("rw-self-chengyu", task_name="\u6210\u8bed\u5c0f\u79c0\u624d")
    elif action == "rw-self-datiwangzhe":
        return client._complete_task("rw-self-datiwangzhe", task_name="\u7b54\u9898\u738b\u8005")
    elif action == "rw-fuli-mall-task":
        return client._complete_task_app_headers("rw-fuli-mall-task")
    return None

def run_task(client, action, remaining, cfg):
    success = 0
    fail = 0
    i = 0
    while i < remaining:
        retry = 0
        done = False
        while retry <= MAX_RETRY:
            r = do_single_task(client, action)
            if r is None:
                break
            if ZssqClient._is_ok(r):
                gold_info = r.get("data", {}).get("gold", {})
                num = gold_info.get("num", 0)
                log_ok("\u7b2c{}/{}\u6b21 +{}\u91d1\u5e01 \u270c\ufe0f".format(i + 1, remaining, num))
                success += 1
                done = True
                break
            else:
                err = ZssqClient._get_error_msg(r)
                if retry < MAX_RETRY:
                    log_warn("\u7b2c{}/{}\u6b21 {} \u2192 \u91cd\u8bd5\u4e2d... ({}/{})".format(
                        i + 1, remaining, err, retry + 1, MAX_RETRY))
                    retry += 1
                    wait_seconds = random_wait_seconds(TASK_WAIT_RANGE)
                    log_info("\u91cd\u8bd5\u524d\u7b49\u5f85 {} \u79d2...".format(wait_seconds))
                    time.sleep(wait_seconds)
                else:
                    log_err("\u7b2c{}/{}\u6b21 {} \u2192 \u653e\u5f03".format(
                        i + 1, remaining, err))
                    retry += 1

        if done:
            i += 1
            if i < remaining:
                wait_seconds = random_wait_seconds(TASK_WAIT_RANGE)
                log_info("\u4e0b\u4e00\u6761\u4efb\u52a1\u524d\u7b49\u5f85 {} \u79d2...".format(wait_seconds))
                time.sleep(wait_seconds)
        else:
            fail += 1
            i += 1
            if i < remaining:
                wait_seconds = random_wait_seconds(TASK_WAIT_RANGE)
                log_info("\u4e0b\u4e00\u6761\u4efb\u52a1\u524d\u7b49\u5f85 {} \u79d2...".format(wait_seconds))
                time.sleep(wait_seconds)

    return success, fail

def get_pending_tasks(task_status):
    pending_tasks = []
    for action, cfg in TASK_CONFIG.items():
        status = task_status.get(action)
        if not status:
            continue
        completed = status["completed"]
        times = status["times"]
        if completed >= times:
            log_ok("{} {}/{} \u2705\u5df2\u5b8c\u6210".format(cfg["name"], completed, times))
        else:
            remaining = times - completed
            log_task("{} {}/{} \u2192 \u5f85\u6267\u884c {}\u6b21".format(
                cfg["name"], completed, times, remaining))
            pending_tasks.append((action, remaining, cfg))
    return pending_tasks

def run_probe_task(token, username, label, proxy_manager):
    android_id, device_id = generate_random_device_info()
    log_loop("\u8d26\u53f7 {} {}...".format(username, label))
    log_info("\u63a2\u6d4b android_id: {}".format(android_id))
    log_info("\u63a2\u6d4b device_id: {}".format(device_id))

    client = ZssqClient(token, android_id, device_id, generate_object_id(android_id), proxy_manager=proxy_manager)
    profile = client.get_profile()
    if not ZssqClient._is_ok(profile):
        log_warn("\u63a2\u6d4b\u524d\u67e5\u8be2\u91d1\u5e01\u5931\u8d25: {}".format(ZssqClient._get_error_msg(profile)))
        return False

    init_gold = profile.get("data", {}).get("gold", 0)
    log_coin("\u63a2\u6d4b\u524d\u91d1\u5e01: {}".format(init_gold))

    task_status = get_task_status(client)
    if task_status is None:
        log_warn("\u63a2\u6d4b\u6a21\u5f0f\u83b7\u53d6\u4efb\u52a1\u72b6\u6001\u5931\u8d25")
        return False

    pending_tasks = get_pending_tasks(task_status)
    if not pending_tasks:
        log_warn("\u63a2\u6d4b\u6a21\u5f0f\u65e0\u53ef\u6267\u884c\u4efb\u52a1\uff0c\u8df3\u8fc7\u5f53\u524d\u8d26\u53f7")
        return False

    action, _, cfg = pending_tasks[0]
    log_info("\u63a2\u6d4b\u6a21\u5f0f\u4ec5\u6267\u884c 1 \u6b21 {}".format(cfg["name"]))
    s, _ = run_task(client, action, 1, cfg)
    if s <= 0:
        log_warn("\u63a2\u6d4b\u4efb\u52a1\u672a\u6210\u529f\uff0c\u8df3\u8fc7\u5f53\u524d\u8d26\u53f7")
        return False

    final_profile = client.get_profile()
    if not ZssqClient._is_ok(final_profile):
        log_warn("\u63a2\u6d4b\u540e\u67e5\u8be2\u91d1\u5e01\u5931\u8d25")
        return False

    final_gold = final_profile.get("data", {}).get("gold", 0)
    earned = final_gold - init_gold
    log_coin("\u63a2\u6d4b\u524d: {} \u2192 \u63a2\u6d4b\u540e: {}".format(init_gold, final_gold))
    if earned > 0:
        log_ok("\u63a2\u6d4b\u6210\u529f\uff0c\u91d1\u5e01\u518d\u6b21\u589e\u52a0 +{}".format(earned))
        return True

    log_warn("\u63a2\u6d4b\u540e\u91d1\u5e01\u4ecd\u65e0\u53d8\u5316\uff0c\u8df3\u8fc7\u5f53\u524d\u8d26\u53f7")
    return False

def get_task_status(client):
    result = client.get_task_overview()
    if not ZssqClient._is_ok(result):
        return None
    task_map = {}
    for t in result.get("data", []):
        action = t.get("action", "")
        if action:
            task_map[action] = {
                "name": t.get("title", t.get("taskName", "")),
                "times": t.get("times", 0),
                "completed": t.get("completed", 0),
            }
    return task_map

def run_account_loop(account_idx, total_accounts, token, android_id, device_id, proxy_manager):
    """运行单个账号的循环任务，每轮强制刷新随机设备。"""
    init_android_id, init_device_id = generate_random_device_info()

    temp_uid = generate_object_id(init_android_id)
    temp_client = ZssqClient(token, init_android_id, init_device_id, temp_uid, proxy_manager=proxy_manager)
    profile = temp_client.get_profile()
    username = "未知用户"
    if ZssqClient._is_ok(profile):
        user_info = profile.get("data", {}).get("user", {})
        username = user_info.get("nickname", user_info.get("name", "未知用户"))
        if not username or username == "未知用户":
            username = profile.get("nickname", profile.get("name", "未知用户"))
    
    log_section("\U0001f464 \u8d26\u53f7\u4fe1\u606f")
    log_info("\u7528\u6237\u540d: {}".format(username))
    if android_id or device_id:
        log_info("\u5df2\u5ffd\u7565\u73af\u5883\u53d8\u91cf\u4e2d\u81ea\u5e26\u8bbe\u5907\u53c2\u6570\uff0c\u8fd0\u884c\u65f6\u5f3a\u5236\u4f7f\u7528\u968f\u673a\u8bbe\u5907")
    
    loop = 0

    while True:
        loop += 1
        android_id, device_id = generate_random_device_info()

        log_loop("\u8d26\u53f7[{}] {} \u7b2c {} \u8f6e\u5f00\u59cb...".format(
            account_idx, username, loop))
        log_info("\u672c\u8f6e\u968f\u673a android_id: {}".format(android_id))
        log_info("\u672c\u8f6e\u968f\u673a device_id: {}".format(device_id))

        client = ZssqClient(token, android_id, device_id, generate_object_id(android_id), proxy_manager=proxy_manager)

        log_section("\U0001f4b3 \u67e5\u8be2\u4f59\u989d")
        profile = client.get_profile()
        init_gold = None
        if ZssqClient._is_ok(profile):
            init_gold = profile.get("data", {}).get("gold", 0)
            log_coin("\u5f53\u524d\u91d1\u5e01: {}".format(init_gold))
            if init_gold == 80000:
                log_warn("\u68c0\u6d4b\u5230\u521d\u59cb\u91d1\u5e01\u4e3a 80000\uff0c\u8df3\u8fc7\u5f53\u524d\u8d26\u53f7")
                break
        else:
            log_warn("\u67e5\u8be2\u521d\u59cb\u91d1\u5e01\u5931\u8d25: {}".format(ZssqClient._get_error_msg(profile)))

        log_section("\U0001f4f2 \u6bcf\u65e5\u7b7e\u5230")
        sign_result = client.daily_sign()
        if ZssqClient._is_ok(sign_result):
            sign_gold = sign_result.get("gold", 0)
            if sign_gold:
                log_ok("\u7b7e\u5230\u6210\u529f +{}\u91d1\u5e01 \U0001f389".format(sign_gold))
            else:
                log_info("\u4eca\u65e5\u5df2\u7b7e\u5230 \U0001f60a")
        else:
            err = ZssqClient._get_error_msg(sign_result)
            if "HAS_SIGN" in str(err):
                log_info("\u4eca\u65e5\u5df2\u7b7e\u5230 \U0001f60a")
            else:
                log_warn("\u7b7e\u5230: {}".format(err))

        log_section("\U0001f4cb \u4efb\u52a1\u72b6\u6001")
        task_status = get_task_status(client)
        if task_status is None:
            log_err("\u83b7\u53d6\u4efb\u52a1\u72b6\u6001\u5931\u8d25\uff0c\u8df4\u8fc7\u4efb\u52a1\u6267\u884c")
            return

        pending_tasks = get_pending_tasks(task_status)

        if not pending_tasks:
            log_star("\u6240\u6709\u4efb\u52a1\u5df2\u5b8c\u6210\uff0c\u65e0\u9700\u64cd\u4f5c \U0001f60e")
        else:
            log_info("\u5171 {} \u4e2a\u4efb\u52a1\u5f65\u6267\u884c \U0001f525".format(len(pending_tasks)))
            total_success = 0
            total_fail = 0
            for idx, (action, remaining, cfg) in enumerate(pending_tasks):
                print("\n  \U0001f3b2 {} ({} \u6b21) \u5f00\u59cb...".format(cfg["name"], remaining))
                s, f = run_task(client, action, remaining, cfg)
                total_success += s
                total_fail += f
                log_info("{} \u5b8c\u6210 \u2705{}\u274c{}".format(cfg["name"], s, f))
                if idx < len(pending_tasks) - 1:
                    log_info("\u5207\u6362\u4e0b\u4e00\u4e2a\u4efb\u52a1\uff0c\u7b49\u5f85 {} \u79d2...".format(TASK_SWITCH_WAIT))
                    time.sleep(TASK_SWITCH_WAIT)

        log_section("\U0001f4ca \u6700\u7ec8\u7ed3\u679c")
        final_profile = client.get_profile()
        if ZssqClient._is_ok(final_profile):
            final_gold = final_profile.get("data", {}).get("gold", 0)
            init_display = str(init_gold) if init_gold is not None else "未知"
            log_coin("\u521d\u59cb: {} \u2192 \u6700\u7ec8: {}".format(init_display, final_gold))
            
            if init_gold is not None:
                earned = final_gold - init_gold
                if earned > 0:
                    log_ok("\u672c\u6b21\u83b7\u5f97 +{}\u91d1\u5e01 \U0001f911".format(earned))
                else:
                    log_info("\u672c\u6b21\u65e0\u53d8\u5316 \U0001f914")
                    log_warn("\u68c0\u6d4b\u5230\u91d1\u5e01\u4e0d\u518d\u589e\u957f\uff0c\u91cd\u5f00\u4e00\u6b21\u5355\u6761\u4efb\u52a1\u63a2\u6d4b")
                    if run_probe_task(token, username, "\u91d1\u5e01\u505c\u6ede\u540e\u63a2\u6d4b", proxy_manager):
                        wait_seconds = random_wait_seconds(ROUND_WAIT_RANGE)
                        log_info("{} \u79d2\u540e\u8fdb\u5165\u65b0\u4e00\u8f6e\u968f\u673a\u8bbe\u5907\u4efb\u52a1...".format(wait_seconds))
                        time.sleep(wait_seconds)
                        continue
                    break
            else:
                log_warn("\u65e0\u6cd5\u6bd4\u8f83\u91d1\u5e01\u53d8\u5316\uff0c\u505c\u6b62\u5f53\u524d\u8d26\u53f7\u5faa\u73af")
                break
        else:
            log_warn("\u67e5\u8be2\u6700\u7ec8\u4f59\u989d\u5931\u8d25\uff0c\u505c\u6b62\u5f53\u524d\u8d26\u53f7\u5faa\u73af")
            break

        next_android_id, next_device_id = generate_random_device_info()
        wait_seconds = random_wait_seconds(ROUND_WAIT_RANGE)
        log_info("\u672c\u8f6e\u4efb\u52a1\u7ed3\u675f\uff0c{} \u79d2\u540e\u81ea\u52a8\u5207\u6362\u65b0\u8bbe\u5907...".format(wait_seconds))
        log_info("\u4e0b\u8f6e android_id: {}".format(next_android_id))
        log_info("\u4e0b\u8f6e device_id: {}".format(next_device_id))
        time.sleep(wait_seconds)
    
    # 输出本轮统计
    log_section("\U0001f4ca \u672c\u8d26\u53f7\u603b\u7ed3")
    log_info("\u5b8c\u6210\u5faa\u73af: {} \u8f6e".format(loop))
    log_warn("\u5f53\u524d\u8d26\u53f7\u5df2\u505c\u6b62\uff0c\u63a5\u5165\u4e0b\u4e00\u8d26\u53f7")

# ============================================================
#  主入口
# ============================================================

def main():
    log_title("\U0001f4da \u8ffd\u4e66\u795e\u5668\u514d\u8d39\u7248 \u2014 \u91d1\u5e01\u4efb\u52a1(\u81ea\u52a8\u5237\u65b0\u7248)")
    print("  {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    proxy_manager = ProxyManager(PROXY_API_URL)

    users = parse_users()
    if not users:
        log_err("\u672a\u627e\u5230\u73af\u5883\u53d8\u91cf zssq \U0001f612")
        log_info("\u683c\u5f0f: token \u6216 token#android_id \u6216 device_id#android_id#token")
        log_info("\u591a\u7528\u6237: @ \u5206\u9694 \u6216 \u6362\u884c")
        log_info("\u6bcf\u4e2a\u8d26\u53f7\u6bcf\u8f6e\u5f3a\u5236\u5237\u65b0\u968f\u673a\u8bbe\u5907\uff0c\u76f4\u5230\u91d1\u5e01\u4e0d\u518d\u589e\u957f")
        sys.exit(1)

    log_ok("\u5171\u52a0\u8f7d {} \u4e2a\u8d26\u53f7 \U0001f389".format(len(users)))

    for i, user in enumerate(users, 1):
        try:
            token = user.get("token", "")
            android_id = user.get("android_id")
            device_id = user.get("device_id")
            
            if not token:
                log_err("\u65e0\u6548\u7528\u6237\u4fe1\u606f\uff0c\u8df4\u8fc7")
                continue
            
            run_account_loop(i, len(users), token, android_id, device_id, proxy_manager)

        except Exception as e:
            log_err("\u8d26\u53f7 {} \u6267\u884c\u51fa\u9519: {}".format(i, e))

        if i < len(users):
            wait_seconds = random_wait_seconds(ACCOUNT_WAIT_RANGE)
            log_info("\u7b49\u5f85 {} \u79d2\u540e\u5904\u7406\u4e0b\u4e00\u4e2a\u8d26\u53f7...".format(wait_seconds))
            time.sleep(wait_seconds)

    log_title("\U0001f3c1 \u5168\u90e8\u8d26\u53f7\u6267\u884c\u5b8c\u6bd5!")
    print("  {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  \u26a0\ufe0f \u7528\u6237\u53d6\u6d88")
        sys.exit(0)
