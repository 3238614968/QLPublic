"""
安心记加班
@author: xiaohai
@date: 2026-04-11
环境变量: axjjb
格式: token 或 device_id#android_id#token
多用户: @ 隔开 或 换行
完成任务：
    1. 签到
    2. 激励视频
    3. 看资讯
    4. 转盘抽奖
    5. 扭蛋
    6. 钓鱼
    7. 下班打卡
    8. 任务列表领奖
    9. 新人奖励
    10. 红包兑换
    11. 提现信息查询
"""

import os
import sys
import json
import time
import random
import string
import base64
import uuid
import hashlib
import re
import urllib3
import requests
from datetime import datetime
from urllib.parse import urlencode

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
#  常量配置
# ============================================================

JJB_API = "https://jjbapi.julanling.com"
MARKET_API = "https://market-gateway.julanling.com"
API_KEY = "2ffc3e48c7086e0e1faa003d781c0e69"
SECRET_KEY = "secret=apitesfopq0fiejdkf"

APP_VERSION = "7.3.90"
SIGNATURE_CODE = "63559975"
CHANNEL = "vivo"
BRAND = "vivo"
MANUFACTURER = "vivo"
MODEL = "V2309A"
OS_VERSION = "33"

FIXED_ANDROID_ID = "a67e8c7b9f2d3e1c"
FIXED_DEVICE_ID = base64.b64encode(FIXED_ANDROID_ID.encode()).decode('ascii')

# 代理配置
PROXY_API_URL = ""
PROXY_LEASE_SECONDS = 120
PROXY_REFRESH_BUFFER = 10

TASK_WAIT_RANGE = (2, 5)
ACCOUNT_WAIT_RANGE = (3, 8)

# ============================================================
#  日志函数
# ============================================================

def log_section(text):
    print(f"\n  {text} {'-'*(45-len(text)*2)}")

def log_ok(text):
    print(f"    ✅ {text}")

def log_err(text):
    print(f"    ❌ {text}")

def log_info(text):
    print(f"    ℹ️ {text}")

def log_warn(text):
    print(f"    ⚠️ {text}")

def log_coin(text):
    print(f"    💰 {text}")

# ============================================================
#  青龙客户端
# ============================================================

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
        url = f"{self.base_url.rstrip('/')}/open/auth/token"
        params = {"client_id": self.client_id, "client_secret": self.client_secret}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200 or not data.get("data", {}).get("token"):
            raise ValueError(f"获取青龙 Token 失败: {data.get('message', '未知错误')}")
        self.token = data["data"]["token"]
        return self.token

    def get_envs(self, name):
        token = self.get_token()
        url = f"{self.base_url.rstrip('/')}/open/envs"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"searchValue": name}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            raise ValueError(f"获取青龙环境变量失败: {data.get('message', '未知错误')}")
        return [env for env in data.get("data", []) if env.get("name") == name]


class ProxyManager:
    def __init__(self, proxy_api_url):
        self.proxy_api_url = proxy_api_url
        self.current_proxy = None
        self.expires_at = 0

    def _parse_proxy_text(self, text):
        proxy = text.strip().splitlines()[0].strip()
        if not proxy or ":" not in proxy:
            raise ValueError(f"代理接口返回格式无效: {text.strip()[:100]}")
        return proxy

    def fetch_new_proxy(self):
        resp = requests.get(self.proxy_api_url, timeout=15)
        resp.raise_for_status()
        proxy = self._parse_proxy_text(resp.text)
        self.current_proxy = proxy
        self.expires_at = time.time() + PROXY_LEASE_SECONDS
        log_info(f"代理已更新: {proxy} (有效期 {PROXY_LEASE_SECONDS} 秒)")
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
            log_warn(f"当前代理不可用，立即更换: {self.current_proxy}")
        self.current_proxy = None
        self.expires_at = 0

# ============================================================
#  安心记加班
# ============================================================

class AnxinJJB:
    def __init__(self, device_id=None, android_id=None, oaid=None, proxy_manager=None):
        self.device_id = device_id or uuid.uuid4().hex
        self.android_id = android_id or uuid.uuid4().hex[:16]
        self.oaid = oaid or uuid.uuid4().hex[:16]
        self.token = None
        self.guest_token = None
        self.user_info = None
        self.jjb_uid = None
        self.userid = None
        self.checkcode = None
        self.logintime = None
        self.dgq_uid = None
        self.session = requests.Session()
        self.session.verify = False
        self.proxy_manager = proxy_manager

        self.app_activate_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.version = APP_VERSION
        self.channel = CHANNEL
        self.brand = BRAND
        self.manufacturer = MANUFACTURER
        self.model = MODEL
        self.os_version = OS_VERSION

    def _ensure_proxy(self, force_refresh=False):
        if not self.proxy_manager:
            return
        proxy = self.proxy_manager.get_proxy(force_refresh=force_refresh)
        proxy_url = f"http://{proxy}"
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
                log_warn(f"请求失败，准备切换代理重试: {e}")
        if last_error is None:
            raise RuntimeError("请求失败，未捕获到具体异常")
        raise last_error

    def _gen_random_str(self, length=16):
        chars = string.ascii_lowercase + string.digits
        return ''.join(random.choice(chars) for _ in range(length))

    @staticmethod
    def _md5_hex(s):
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    @staticmethod
    def _extract_link(url_str):
        m = re.match(r'^http.*?://(.*?)(/*[?#].*$|[?#].*$|/*$)', url_str)
        if m:
            return m.group(1).strip()
        return url_str

    def _compute_signature(self, url, request_params, header_map):
        all_params = {}
        all_params.update(header_map)
        all_params.update(request_params)
        link = self._extract_link(url)
        all_params['link'] = link
        items = [f"{k}={v}" for k, v in all_params.items()]
        items.append(SECRET_KEY)
        items.sort()
        concat = ''.join(items)
        return self._md5_hex(concat)

    def _get_common_params_jjb(self, account_book_type="ZHGS"):
        return {
            "jid": self.jjb_uid or "Guest_User",
            "system_version": self.version,
            "signatureCode": SIGNATURE_CODE,
            "accountBookType": account_book_type,
            "channel": self.channel,
            "app_activate_date": self.app_activate_date,
            "android_id": self.android_id,
            "brand": self.brand,
            "device": self.device_id,
            "version": self.version,
            "oaid": self.oaid,
            "manufacturer": self.manufacturer,
        }

    def _get_headers_jjb(self, path, token=None, request_params=None, full_url=None):
        auth = f"Bearer {token}" if token else "Bearer"
        requesttime = str(int(time.time()))
        header_map = {
            "Clientinfo": "julanling_jjb",
            "Clientversion": self.version,
            "Devicetype": "3",
            "Requesttime": requesttime,
            "Devicetoken": self.device_id,
            "Isdebug": "0",
            "Userid": str(self.userid) if self.userid else "",
            "Logintime": str(self.logintime) if self.logintime else "",
            "Checkcode": self.checkcode or "",
        }
        url_for_sig = full_url or f"{JJB_API}{path}"
        signature = self._compute_signature(url_for_sig, request_params or {}, header_map)
        sign_host = self._extract_link(url_for_sig)
        return {
            "devicetype": "3",
            "logintime": str(self.logintime) if self.logintime else "",
            "clientversion": self.version,
            "userid": str(self.userid) if self.userid else "",
            "clientinfo": "julanling_jjb",
            "requesttime": requesttime,
            "devicetoken": self.device_id,
            "isdebug": "0",
            "checkcode": self.checkcode or "",
            "signature": signature,
            "authorization": auth,
            "sign-host": sign_host,
            "apikey": API_KEY,
            "cache-control": "no-cache",
            "accept-encoding": "gzip",
            "user-agent": "okhttp/3.14.9",
        }

    NONCE_SECRET = "Gcy9zywM9PJ8JaGCviMTdgjLd73xexjm"

    def _gen_nonce_headers(self):
        qhcy = str(int(time.time()))
        qqjq = self._gen_random_str(16)
        feko = self._gen_random_str(16)
        raw = f"{qhcy}&{qqjq}&{feko}&{self.NONCE_SECRET}"
        xq_plrr_csad = hashlib.sha1(raw.encode('utf-8')).hexdigest()
        return {
            "xq-plrr-csad": xq_plrr_csad,
            "mm-jfxi-qhcy": qhcy,
            "bx-rahc-qqjq": qqjq,
            "le-bjor-feko": feko,
        }

    def _get_headers_market(self, token=None):
        uid_info = json.dumps({
            "version": 7390,
            "versionName": self.version,
            "userType": "1",
            "sdkVersion": self.os_version,
            "statusBarHeight": 34,
            "toolBarHeight": 78,
            "oaid": self.oaid,
            "channel": self.channel,
            "uid": self.jjb_uid or "",
        })
        ua = (
            f"Mozilla/5.0 (Linux; Android 13; {self.model} Build/TKQ1.221114.001; wv) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/108.0.5359.128 "
            f"Mobile Safari/537.36;_android{uid_info}_android"
        )
        auth = f"Bearer {token}" if token else ""
        nonce = self._gen_nonce_headers()
        return {
            "pragma": "no-cache",
            "cache-control": "no-cache",
            "accept": "application/json, text/plain, */*",
            "xq-plrr-csad": nonce["xq-plrr-csad"],
            "mm-jfxi-qhcy": nonce["mm-jfxi-qhcy"],
            "le-bjor-feko": nonce["le-bjor-feko"],
            "authorization": auth,
            "bx-rahc-qqjq": nonce["bx-rahc-qqjq"],
            "user-agent": ua,
            "origin": "https://market-h5.julanling.com",
            "x-requested-with": "com.julanling.app",
            "sec-fetch-site": "same-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "referer": "https://market-h5.julanling.com/",
            "accept-encoding": "gzip, deflate",
            "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    def _get_market_base_params(self):
        return {
            "os": "ANDROID",
            "appVersion": self.version,
            "appChannel": self.channel,
            "deviceToken": "",
            "app_activate_date": self.app_activate_date,
        }

    # 登录相关方法
    def guest_login(self):
        path = "/user/guest"
        url = f"{JJB_API}{path}"
        data = {
            "device_id": self.android_id,
            "jid": "Guest_User",
            "current_ver": self.version,
            "os_version": self.os_version,
            "channel": self.channel,
            "imsi": "imsi",
            "resolution": "0x0",
            "version": self.version,
            "mac": "000000000000",
            "platform": "android",
            "manufacturer": self.manufacturer,
            "device_token": self.device_id,
            "system_version": self.version,
            "signatureCode": SIGNATURE_CODE,
            "accountBookType": "JJB",
            "imei": "imei",
            "model": self.model,
            "app_activate_date": self.app_activate_date,
            "android_id": self.android_id,
            "brand": self.brand,
            "device": self.device_id,
            "oaid": "",
        }
        headers = self._get_headers_jjb(path, token=None, request_params=data, full_url=url)
        headers["content-type"] = "application/x-www-form-urlencoded"
        try:
            resp = self._request("POST", url, headers=headers, data=urlencode(data), timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                self.guest_token = result["token"]
                self.token = result["token"]
                user = result["results"]
                self.userid = user["id"]
                self.jjb_uid = user["jjbUid"]
                self.logintime = result["extraInfo"]["authInfo"]["Logintime"]
                self.checkcode = result["extraInfo"]["authInfo"]["Checkcode"]
                return {"success": True, "token": self.token, "user": user}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_sms(self, mobile, sms_type=4):
        path = "/user_sms_verify_log/sendsms"
        url = f"{JJB_API}{path}"
        params = self._get_common_params_jjb("ZHGS")
        params["mobile"] = mobile
        params["type"] = str(sms_type)
        headers = self._get_headers_jjb(path, token=self.token, request_params=params, full_url=url)
        headers["content-type"] = "application/x-www-form-urlencoded"
        try:
            resp = self._request("POST", url, headers=headers, data=urlencode(params), timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "message": "SMS sent successfully"}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def verify_code_login(self, mobile, sms_code):
        path = "/user/verify_code_login"
        url = f"{JJB_API}{path}"
        yidun_params = json.dumps({
            "apdid_token": "",
            "user_agent": "Mozilla/5.0 (Linux; Android 13; V2309A Build/TKQ1.221114.001; wv) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/108.0.5359.128 "
                          "Mobile Safari/537.36",
            "wifi_mac": "000000000000",
            "imsi": "imsi",
            "imei": "imei",
            "platform": "android",
            "lat": "",
            "lng": "",
        })
        data = {
            "device_id": self.android_id,
            "jid": self.jjb_uid,
            "current_ver": self.version,
            "os_version": self.os_version,
            "channel": self.channel,
            "imsi": "imsi",
            "resolution": "0x0",
            "version": self.version,
            "mac": "000000000000",
            "platform": "android",
            "manufacturer": self.manufacturer,
            "device_token": "",
            "system_version": self.version,
            "signatureCode": SIGNATURE_CODE,
            "accountBookType": "ZHGS",
            "imei": "imei",
            "model": self.model,
            "app_activate_date": self.app_activate_date,
            "android_id": self.android_id,
            "brand": self.brand,
            "device": "",
            "oaid": self.oaid,
            "mobile": mobile,
            "sms_code": sms_code,
            "yidun_params": yidun_params,
            "inviteInfo": "",
        }
        headers = self._get_headers_jjb(path, token=self.token, request_params=data, full_url=url)
        headers["content-type"] = "application/x-www-form-urlencoded"
        encoded_body = urlencode(data)
        
        self.session.close()
        self.session = requests.Session()
        self.session.verify = False
        
        for attempt in range(3):
            try:
                resp = self._request("POST", url, headers=headers, data=encoded_body, timeout=30)
                result = resp.json()
                break
            except (requests.exceptions.ConnectionError, Exception) as e:
                if attempt < 2:
                    time.sleep(1)
                    self.session.close()
                    self.session = requests.Session()
                    self.session.verify = False
                else:
                    return {"success": False, "error": f"Connection error after 3 attempts: {e}"}
        
        if result.get("errorCode") == 0:
            self.token = result["token"] if result.get("token") else self.token
            jjb_auth = result["extraInfo"]["jjbAuthInfo"]
            dgq_auth = result["extraInfo"]["dgqAuthInfo"]
            jjb_user = result["results"]["jjbResults"]
            dgq_user = result["results"]["dgqResults"]
            self.userid = jjb_user["id"]
            self.jjb_uid = jjb_user["jjbUid"]
            self.dgq_uid = str(dgq_user.get("uid", ""))
            self.logintime = jjb_auth["Logintime"]
            self.checkcode = jjb_auth["Checkcode"]
            self.user_info = jjb_user
            return {
                "success": True,
                "token": self.token,
                "jjb_auth": jjb_auth,
                "dgq_auth": dgq_auth,
                "jjb_user": jjb_user,
                "dgq_user": dgq_user,
            }
        return {"success": False, "error": result.get("errorStr", "Unknown error")}

    def get_user_info(self):
        path = "/switch_my/get_user_info"
        url = f"{JJB_API}{path}"
        params = self._get_common_params_jjb("ZHGS")
        headers = self._get_headers_jjb(path, token=self.token, request_params=params, full_url=url)
        try:
            resp = self._request("GET", url, headers=headers, params=params, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_sign_info(self):
        path = "/market-center/api2/signIn/signInfo"
        url = f"{MARKET_API}{path}"
        params = self._get_market_base_params()
        headers = self._get_headers_market(token=self.token)
        try:
            resp = self._request("GET", url, headers=headers, params=params, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def sign_in(self):
        path = "/market-center/api2/signIn/signIn"
        url = f"{MARKET_API}{path}"
        headers = self._get_headers_market(token=self.token)
        headers["content-type"] = "application/json;charset=UTF-8"
        data = self._get_market_base_params()
        try:
            resp = self._request("POST", url, headers=headers, json=data, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def finish_sign_video(self, business_type="SIGN_VIDEO_1"):
        path = "/market-center/api2/signIn/finishSignVideo"
        url = f"{MARKET_API}{path}"
        headers = self._get_headers_market(token=self.token)
        headers["content-type"] = "application/json;charset=UTF-8"
        data = self._get_market_base_params()
        data["businessType"] = business_type
        try:
            resp = self._request("POST", url, headers=headers, json=data, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_task_list(self):
        path = "/market-center/api2/assignment/batchListByPositions"
        url = f"{MARKET_API}{path}"
        params = self._get_market_base_params()
        params["positions"] = "MONEY_CENTER_NEW_WELFARE,MONEY_CENTER_DAILY_WELFARE,MONEY_CENTER_GLOBAL_WELFARE,MONEY_CENTER_WEEK_WELFARE"
        headers = self._get_headers_market(token=self.token)
        try:
            resp = self._request("GET", url, headers=headers, params=params, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def receive_award(self, business_type):
        path = "/market-center/api2/assignment/receiveAwardByBusinessType"
        url = f"{MARKET_API}{path}"
        headers = self._get_headers_market(token=self.token)
        headers["content-type"] = "application/json;charset=UTF-8"
        data = self._get_market_base_params()
        data["businessType"] = business_type
        try:
            resp = self._request("POST", url, headers=headers, json=data, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_coin_detail(self):
        path = "/activity-third-account/api/coinV2/getDetail"
        url = f"{MARKET_API}{path}"
        params = self._get_market_base_params()
        headers = self._get_headers_market(token=self.token)
        try:
            resp = self._request("GET", url, headers=headers, params=params, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_draw_index(self):
        path = "/activity-third-account/api/cash/draw/drawIndex"
        url = f"{MARKET_API}{path}"
        params = self._get_market_base_params()
        headers = self._get_headers_market(token=self.token)
        try:
            resp = self._request("GET", url, headers=headers, params=params, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_member_info(self):
        path = "/member/api2/member/getMemberInfo"
        url = f"{MARKET_API}{path}"
        params = {
            "appVersion": self.version,
            "jid": self.jjb_uid,
            "os": "ANDROID",
            "channel": self.channel,
            "version": self.version,
            "operatingSystem": "Android",
            "appChannel": self.channel,
            "manufacturer": self.manufacturer,
            "deviceToken": self.device_id,
            "system_version": self.version,
            "signatureCode": SIGNATURE_CODE,
            "accountBookType": "ZHGS",
            "app_activate_date": self.app_activate_date,
            "android_id": self.android_id,
            "brand": self.brand,
            "device": "",
            "oaid": self.oaid,
        }
        requesttime = str(int(time.time()))
        header_map = {
            "Clientinfo": "julanling_jjb",
            "Clientversion": self.version,
            "Devicetype": "3",
            "Requesttime": requesttime,
            "Devicetoken": "",
            "Isdebug": "0",
            "Userid": str(self.userid) if self.userid else "",
            "Logintime": str(self.logintime) if self.logintime else "",
            "Checkcode": self.checkcode or "",
        }
        signature = self._compute_signature(url, params, header_map)
        sign_host = self._extract_link(url)
        headers = {
            "devicetype": "3",
            "logintime": str(self.logintime) if self.logintime else "",
            "clientversion": self.version,
            "userid": str(self.userid) if self.userid else "",
            "clientinfo": "julanling_jjb",
            "requesttime": requesttime,
            "devicetoken": "",
            "isdebug": "0",
            "checkcode": self.checkcode or "",
            "signature": signature,
            "authorization": f"Bearer {self.token}",
            "sign-host": sign_host,
            "apikey": API_KEY,
            "cache-control": "no-cache",
            "accept-encoding": "gzip",
            "user-agent": "okhttp/3.14.9",
        }
        try:
            resp = self._request("GET", url, headers=headers, params=params, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def login_with_sms(self, mobile):
        print(f"  [1/3] 游客登录...")
        guest_result = self.guest_login()
        if not guest_result["success"]:
            print(f"  游客登录失败: {guest_result.get('error')}")
            return guest_result
        print(f"  [2/3] 发送验证码到 {mobile}...")
        sms_result = self.send_sms(mobile)
        if not sms_result["success"]:
            print(f"  发送失败: {sms_result.get('error')}")
            return sms_result
        sms_code = input(f"  请输入验证码: ").strip()
        print(f"  [3/3] 验证登录...")
        login_result = self.verify_code_login(mobile, sms_code)
        if not login_result["success"]:
            print(f"  登录失败: {login_result.get('error')}")
            return login_result
        user_result = self.get_user_info()
        member_result = self.get_member_info()
        return {
            "success": True,
            "token": self.token,
            "jjb_uid": self.jjb_uid,
            "userid": self.userid,
            "dgq_uid": self.dgq_uid,
            "jjb_user": login_result.get("jjb_user"),
            "user_info": user_result.get("data"),
            "member_info": member_result.get("data"),
        }

    def to_dict(self, mobile=None):
        return {
            "mobile": mobile or "",
            "token": self.token,
            "jjb_uid": self.jjb_uid,
            "userid": self.userid,
            "dgq_uid": self.dgq_uid,
            "device_id": self.device_id,
            "android_id": self.android_id,
            "oaid": self.oaid,
            "app_activate_date": self.app_activate_date,
            "logintime": self.logintime,
            "checkcode": self.checkcode,
            "user_info": self.user_info,
        }

    @classmethod
    def from_dict(cls, data):
        client = cls(
            device_id=data.get("device_id"),
            android_id=data.get("android_id"),
            oaid=data.get("oaid"),
        )
        client.token = data.get("token")
        client.jjb_uid = data.get("jjb_uid")
        client.userid = data.get("userid")
        client.dgq_uid = data.get("dgq_uid")
        client.app_activate_date = data.get("app_activate_date", client.app_activate_date)
        client.logintime = data.get("logintime")
        client.checkcode = data.get("checkcode")
        client.user_info = data.get("user_info")
        return client

# ============================================================
#  任务运行器
# ============================================================

class TaskRunner(AnxinJJB):

    def get_coin_account(self):
        result = self._market_get("/activity-third-account/api/coinV2/getAccount")
        if result["success"]:
            d = result["data"]
            credits = d.get("credits", 0)
            about = d.get("aboutAmount", "0")
            total = d.get("totalCredits", 0)
            print(f"  金币账户: {credits} (约{about}元, 总计{total})")
        else:
            print(f"  金币账户查询失败: {result.get('error')}")
        return result

    def do_sign_in(self):
        sign_info = self.get_sign_info()
        if not sign_info["success"]:
            print(f"  获取签到信息失败: {sign_info.get('error')}")
            return False
        si = sign_info["data"]
        if si.get("isSignIn"):
            print(f"  今日已签到, 连续{si.get('continuousDays')}天")
        else:
            sign_result = self.sign_in()
            if sign_result["success"]:
                d = sign_result["data"]
                print(f"  签到成功! 连续{d.get('continuousDays')}天, +{d.get('amount')}金币")
            else:
                print(f"  签到失败: {sign_result.get('error')}")
                return False
        time.sleep(1)
        si = self.get_sign_info()
        if not si["success"]:
            return True
        video_type = si["data"].get("signVideoBusinessType")
        while video_type and si["data"].get("currentType") == "SIGN_VIDEO":
            vr = self.finish_sign_video(video_type)
            if vr["success"]:
                vd = vr["data"]
                print(f"  签到视频: +{vd.get('amount')}金币")
            else:
                break
            time.sleep(1)
            si = self.get_sign_info()
            if not si["success"]:
                break
            video_type = si["data"].get("signVideoBusinessType")
        return True

    def do_watch_video(self):
        print("  激励视频...")
        count = 0
        while True:
            result = self._finish_assignment("JJB_MONEY_CENTER_INCENTIVE_VIDEO")
            if result["success"]:
                awards = result["data"].get("awardInfos", [])
                amount = awards[0]["amount"] if awards else 0
                count += 1
                print(f"  看视频 +{amount}金币 (第{count}次)")
                time.sleep(3)
            else:
                if count == 0:
                    print(f"  激励视频: {result.get('error')}")
                break
        return count

    def do_read_news(self):
        print("  看资讯...")
        count = 0
        task_id = int(time.time()) % 1000000
        for _ in range(100):
            result = self._finish_task_news(task_id)
            if result["success"]:
                amount = result["data"].get("amount", 0)
                count += 1
                print(f"  看资讯 +{amount}金币 (第{count}篇)")
                task_id += 1
                time.sleep(5)
            else:
                break
        if count == 0:
            print(f"  看资讯: 无可用资讯")
        return count

    def do_dial(self):
        print("  转盘...")
        enter = self._finish_enter_task("JJB_DAILY_DIAL")
        if enter["success"]:
            status = enter["data"].get("status", "")
            if status != "DISABLE_RECEIVE":
                print(f"  转盘进入任务已完成")
        detail = self._dial_detail()
        if not detail["success"]:
            print(f"  转盘详情失败: {detail.get('error')}")
            return
        dr = detail["data"]
        valid_num = dr.get("dialValidNum", 0)
        gold = dr.get("goldAmount", 0)
        double_cards = dr.get("dialCardBag", {}).get("DOUBLE", 0)
        print(f"  转盘可用{valid_num}次, 当前{gold}金币, 翻倍卡{double_cards}张")
        for i in range(valid_num):
            draw = self._dial_lucky_draw()
            if not draw["success"]:
                print(f"  转盘失败: {draw.get('error')}")
                break
            dd = draw["data"]
            valid_num = dd.get("dialValidNum", valid_num - 1)
            double_cards = dd.get("dialCardBag", {}).get("DOUBLE", double_cards)
            award_type = dd.get("awardType", "")
            biz_no = dd.get("bizNo", "")
            amount = dd.get("amount", 0)
            if award_type == "GOLD":
                if biz_no:
                    if double_cards > 0:
                        coin_result = self._dial_double_coin(biz_no)
                        if coin_result["success"]:
                            da = coin_result["data"].get("amount", 0)
                            double_cards = max(0, double_cards - 1)
                            print(f"  转盘翻倍 +{da}金币 (剩余翻倍卡{double_cards}张)")
                        else:
                            coin_result = self._dial_receive_coin(biz_no)
                            if coin_result["success"]:
                                print(f"  转盘 +{coin_result['data'].get('amount', 0)}金币")
                            else:
                                print(f"  转盘领金币失败: {coin_result.get('error')}")
                    else:
                        coin_result = self._dial_receive_coin(biz_no)
                        if coin_result["success"]:
                            print(f"  转盘 +{coin_result['data'].get('amount', 0)}金币")
                        else:
                            print(f"  转盘领金币失败: {coin_result.get('error')}")
                else:
                    print(f"  转盘 +{amount}金币")
            elif award_type == "ADVERT_ONE_PIC":
                self._dial_advert_expose("ADVERT_ONE_PIC")
                print(f"  转盘抽到广告位")
            elif award_type == "DOUBLE_VIDEO":
                print(f"  转盘抽到双倍视频")
            elif award_type == "GOLD_VIDEO":
                print(f"  转盘抽到金币视频")
            else:
                print(f"  转盘抽到: {award_type}")
            time.sleep(5)
        detail2 = self._dial_detail()
        if detail2["success"]:
            box_resp = detail2["data"].get("dialBoxResp", {})
            self._dial_open_boxes(box_resp)

    def do_gacha(self):
        print("  扭蛋...")
        index = self._gacha_index()
        if not index["success"]:
            print(f"  扭蛋详情失败: {index.get('error')}")
            return
        idx = index["data"]
        remain_video = idx.get("remainVideoTimes", 0)
        remain = idx.get("remainTimes", 0)
        print(f"  扭蛋可用{remain}次, 广告{remain_video}次")
        for _ in range(remain_video):
            ad_result = self._gacha_finish_ad()
            if ad_result["success"]:
                ad = ad_result["data"]
                print(f"  扭蛋广告 +{ad.get('amount', 0)}次 (剩余{ad.get('remainTimes', 0)}次)")
            else:
                break
            time.sleep(5)
        index = self._gacha_index()
        if not index["success"]:
            return
        remain = index["data"].get("remainTimes", 0)
        for _ in range(remain):
            draw = self._gacha_lucky_draw()
            if draw["success"]:
                name = draw["data"].get("name", "")
                award_type = draw["data"].get("awardType", "")
                amount = draw["data"].get("amount", "")
                chip = draw["data"].get("chipNum", 0)
                if award_type == "ADVERT":
                    print(f"  扭蛋抽到空气")
                elif chip and int(chip) > 0:
                    print(f"  扭蛋抽到: {name} x{chip}")
                else:
                    print(f"  扭蛋抽到: {name} {amount}")
            else:
                print(f"  扭蛋失败: {draw.get('error')}")
                break
            time.sleep(5)
        chest = self._gacha_treasure_chest()
        if chest["success"]:
            chips = chest["data"].get("chips", [])
            for chip in chips:
                name = chip.get("name", "")
                amount = chip.get("amount", "")
                print(f"  扭蛋碎片: {name} {amount}")

    def do_fish(self):
        print("  钓鱼...")
        index = self._fish_index()
        if not index["success"]:
            print(f"  钓鱼详情失败: {index.get('error')}")
            return
        fi = index["data"]
        remain = fi.get("remainTimes", 0)
        coin_account = fi.get("coinAccount", "")
        cash_account = fi.get("cashAccount", "")
        attend_days = fi.get("attendDays", 0)
        print(f"  钓鱼可用{remain}次, 已钓{attend_days}天, 金币{coin_account}, 现金{cash_account}")
        fish_kinds = [4001, 4002, 4005, 4003, 4004]
        total_fished = 0
        for kind_id in fish_kinds:
            if remain <= 0:
                break
            for _ in range(6):
                if remain <= 0:
                    break
                draw = self._fish_lucky_draw(kind_id)
                if not draw["success"]:
                    break
                fd = draw["data"]
                remain = fd.get("remainTimes", 0)
                fish_info = fd.get("fishInfo", {})
                fish_name = fish_info.get("name", "")
                lottery = fd.get("fishLuckyLotteryResp", {})
                award_value = lottery.get("awardValue", "")
                award_type = lottery.get("awardType", "")
                has_double = lottery.get("hasDouble", False)
                biz_no = lottery.get("bizNo", "")
                if fish_name:
                    if award_type == "GOLD":
                        print(f"  钓到 {fish_name}: +{award_value}金币")
                    elif award_type == "CASH":
                        print(f"  钓到 {fish_name}: +{award_value}元")
                    else:
                        print(f"  钓到 {fish_name}: {award_value}")
                    if has_double and biz_no and award_type == "GOLD":
                        time.sleep(1)
                        double = self._fish_double(biz_no)
                        if double["success"]:
                            print(f"    双倍: +{double['data'].get('amount', 0)}金币")
                total_fished += 1
                time.sleep(2)
        print(f"  共钓{total_fished}次, 剩余{remain}次")
        expand = self._fish_index_expand()
        if expand["success"]:
            task_info = expand["data"].get("fishVideosTaskInfo", {})
            if task_info:
                task_remain = task_info.get("remainTaskTimes", 0)
                if task_remain > 0:
                    print(f"  钓鱼看视频任务剩余{task_remain}次")
                    for _ in range(task_remain):
                        ad_result = self._fish_finish_ad()
                        if ad_result["success"]:
                            award = ad_result["data"].get("award", 0)
                            task_remain = ad_result["data"].get("remainTaskTimes", 0)
                            print(f"  钓鱼广告 +{award} (剩余{task_remain}次)")
                        else:
                            break
                        time.sleep(3)

    def do_clock_out(self):
        print("  下班打卡...")
        core = self._clock_out_index_core()
        if core["success"]:
            cd = core["data"]
            btn_status = cd.get("buttonStatus", "")
            period = cd.get("currentPeriod", "")
            apply_count = cd.get("applyCount", 0)
            prize_pool = cd.get("prizePool", 0)
            apply_gold = cd.get("applyGold", 0)
            print(f"  打卡状态: {btn_status}, 奖池{prize_pool}, 打卡+{apply_gold}金币")
            if btn_status == "CUR_UN_APPLY":
                result = self._clock_out_apply()
                if result["success"]:
                    rd = result["data"]
                    rt = rd.get("resultType", "")
                    gold = rd.get("applyGold", 0)
                    total_gold = rd.get("goldAmount", 0)
                    print(f"  打卡成功! +{gold}金币 (总{total_gold})")
                    box_resp = rd.get("clockoutBoxProcessResp", {})
                    if box_resp:
                        box_status = box_resp.get("boxStatus", "")
                        clock_days = box_resp.get("clockDays", 0)
                        total_days = box_resp.get("totalDays", 0)
                        print(f"  打卡宝箱: {clock_days}/{total_days}天 ({box_status})")
                else:
                    print(f"  打卡失败: {result.get('error')}")
            elif btn_status == "CUR_APPLIED":
                print(f"  今日已打卡")
            else:
                print(f"  打卡状态: {btn_status}")
            box = self._clockout_box_process()
            if box["success"]:
                bd = box["data"]
                clock_days = bd.get("clockDays", 0)
                total_days = bd.get("totalDays", 0)
                box_status = bd.get("boxStatus", "")
                today = bd.get("todayFinish", False)
                print(f"  打卡宝箱进度: {clock_days}/{total_days}天, 今日{'已完成' if today else '未完成'}")
                if today and box_status == "FINISH":
                    award_list = self._clockout_box_award_list()
                    if award_list["success"]:
                        awards = award_list["data"].get("boxAwardList", [])
                        for idx, aw in enumerate(awards):
                            at = aw.get("awardType", "")
                            ac = aw.get("awardCount", 0)
                            desc = "金币" if at == "GOLD" else ("会员" if at == "MEMBER" else at)
                            print(f"  宝箱奖励[{idx+1}]: {ac}{desc}")
        else:
            result = self._clock_out_apply()
            if result["success"]:
                print(f"  打卡成功")
            else:
                print(f"  打卡: {result.get('error')}")

    def do_task_list_rewards(self):
        print("  任务列表领奖...")
        task_result = self.get_task_list()
        if not task_result["success"]:
            print(f"  获取任务列表失败: {task_result.get('error')}")
            return
        positions = ["MONEY_CENTER_NEW_WELFARE", "MONEY_CENTER_DAILY_WELFARE",
                     "MONEY_CENTER_GLOBAL_WELFARE", "MONEY_CENTER_WEEK_WELFARE"]
        assignment_list = task_result["data"].get("assignmentListResp", {})
        claimed = 0
        for pos in positions:
            group = assignment_list.get(pos)
            if not group:
                continue
            assignments = group.get("assignments", [group]) if isinstance(group, dict) else group
            if not isinstance(assignments, list):
                continue
            for task in assignments:
                status_info = task.get("assignmentStatusInfo", {})
                button = status_info.get("buttonInfo", {})
                msg = button.get("MSG", "")
                bt = task.get("businessType", "")
                status = status_info.get("businessStatus", "")
                title = task.get("title", task.get("statistics", ""))
                if status == "UNDER_WAY" and bt:
                    finish = self._finish_assignment(bt)
                    if finish["success"]:
                        print(f"  完成任务: {title}")
                        time.sleep(1)
                if msg in ["领金币", "待领取"] and bt:
                    award = self.receive_award(bt)
                    if award["success"]:
                        awards = award["data"].get("awardInfos", [])
                        amount = awards[0]["amount"] if awards else 0
                        print(f"  领奖: {title} +{amount}金币")
                        claimed += 1
                    time.sleep(1)
        if claimed == 0:
            print(f"  任务列表: 无可领取奖励")

    def do_new_user_rewards(self):
        print("  新人奖励...")
        is_new = self._is_new_user("MONEY_CENTER")
        if not is_new["success"]:
            print(f"  新人检查失败: {is_new.get('error')}")
            return
        if is_new["data"]:
            print(f"  是新用户, 尝试领取新人奖励...")
            new_tasks = ["JJB_OPEN_NOTICE", "JJB_MONEY_CENTER_NEW_DIAL", "JJB_BIND_MOBILE"]
            for bt in new_tasks:
                award = self.receive_award(bt)
                if award["success"]:
                    awards = award["data"].get("awardInfos", [])
                    amount = awards[0]["amount"] if awards else 0
                    print(f"  新人奖励 {bt}: +{amount}金币")
                else:
                    print(f"  新人奖励 {bt}: {award.get('error')}")
                time.sleep(1)
        else:
            print(f"  非新用户")

    def do_red_pack_exchange(self):
        print("  红包兑换...")
        result = self._red_pack_exchange()
        if result["success"]:
            d = result["data"]
            amount = d.get("exchangeRedPackAmount", "0")
            coin = d.get("requireCoinAmount", "0")
            show = d.get("showAble", False)
            if show:
                print(f"  红包兑换: {amount}元 -> {coin}金币")
            else:
                print(f"  红包兑换: 无可兑换")
        else:
            print(f"  红包兑换失败: {result.get('error')}")

    def do_draw_index(self):
        print("  提现信息...")
        result = self._draw_index()
        if result["success"]:
            d = result["data"]
            balance = d.get("balanceAmount", 0)
            about = d.get("aboutAmount", "0")
            cases = d.get("drawCaseResps", [])
            print(f"  余额: {balance} (约{about}元)")
            for case in cases:
                bt = case.get("businessType", "")
                cash = case.get("cash", "")
                credits = case.get("credits", "")
                cash_able = case.get("cashAble", False)
                print(f"    {cash}: {credits} {'[可提现]' if cash_able else ''}")
        else:
            print(f"  提现信息失败: {result.get('error')}")

    def run_all(self):
        print(f"\n{'='*40}")
        print(f"账号: {self.jjb_uid}")
        print(f"{'='*40}")
        self.get_coin_account()
        time.sleep(1)
        self.do_sign_in()
        time.sleep(1)
        self.do_watch_video()
        time.sleep(1)
        self.do_read_news()
        time.sleep(1)
        self.do_dial()
        time.sleep(1)
        self.do_gacha()
        time.sleep(1)
        self.do_fish()
        time.sleep(1)
        self.do_clock_out()
        time.sleep(1)
        self.do_task_list_rewards()
        time.sleep(1)
        self.do_new_user_rewards()
        time.sleep(1)
        self.do_red_pack_exchange()
        time.sleep(1)
        self.do_draw_index()
        time.sleep(1)
        self.get_coin_account()
        print(f"  全部任务执行完成!")

    # Market API 辅助方法
    def _market_post(self, path, data=None):
        url = f"{MARKET_API}{path}"
        headers = self._get_headers_market(token=self.token)
        headers["content-type"] = "application/json;charset=UTF-8"
        data = data or self._get_market_base_params()
        try:
            resp = self._request("POST", url, headers=headers, json=data, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _market_get(self, path, params=None):
        url = f"{MARKET_API}{path}"
        headers = self._get_headers_market(token=self.token)
        params = params or self._get_market_base_params()
        try:
            resp = self._request("GET", url, headers=headers, params=params, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _finish_assignment(self, business_type):
        data = self._get_market_base_params()
        data["businessType"] = business_type
        return self._market_post("/market-center/api2/assignment/finishAssignment", data)

    def _finish_task_news(self, task_id):
        path = "/market/finish_task"
        url = f"{JJB_API}{path}"
        params = self._get_common_params_jjb("JJB")
        params["task_type"] = "news"
        params["task_id"] = str(task_id)
        headers = self._get_headers_jjb(path, token=self.token, request_params=params, full_url=url)
        try:
            resp = self._request("GET", url, headers=headers, params=params, timeout=30)
            result = resp.json()
            if result.get("errorCode") == 0:
                return {"success": True, "data": result["results"]}
            return {"success": False, "error": result.get("errorStr", "Unknown")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _finish_enter_task(self, business_type):
        data = self._get_market_base_params()
        data["businessType"] = business_type
        data["operatingSystem"] = "ANDROID"
        return self._market_post("/market-center/api2/task/finishOnceEnterTask", data)

    def _is_new_user(self, page="MONEY_CENTER"):
        params = self._get_market_base_params()
        params["page"] = page
        return self._market_get("/market-center/api2/activity/common/isNewUser", params)

    def _red_pack_exchange(self):
        data = self._get_market_base_params()
        return self._market_post("/activity-third-account/api/cash/draw/redPackExchangeToCoin", data)

    def _draw_index(self):
        params = self._get_market_base_params()
        return self._market_get("/activity-third-account/api/cash/draw/drawIndex", params)

    def _dial_detail(self):
        return self._market_get("/market-center/api2/dial/detailCore")

    def _dial_lucky_draw(self):
        data = self._get_market_base_params()
        data["operatingSystem"] = "ANDROID"
        return self._market_post("/market-center/api2/dial/luckyDraw", data)

    def _dial_receive_coin(self, biz_no):
        data = self._get_market_base_params()
        data["bizNo"] = biz_no
        data["operatingSystem"] = "ANDROID"
        return self._market_post("/market-center/api2/dial/receiveDialCoin", data)

    def _dial_receive_double_card(self, biz_no):
        data = self._get_market_base_params()
        data["bizNo"] = biz_no
        data["operatingSystem"] = "ANDROID"
        return self._market_post("/market-center/api2/dial/receiveDoubleCardBag", data)

    def _dial_double_coin(self, biz_no):
        data = self._get_market_base_params()
        data["bizNo"] = biz_no
        data["operatingSystem"] = "ANDROID"
        return self._market_post("/market-center/api2/dial/receiveDialDoubleCoin", data)

    def _dial_advert_expose(self, advert_type="ADVERT_ONE_PIC"):
        data = self._get_market_base_params()
        data["advertType"] = advert_type
        data["operatingSystem"] = "ANDROID"
        return self._market_post("/market-center/api2/dial/advertExpose", data)

    def _dial_open_boxes(self, box_resp=None):
        if not box_resp:
            return
        box_infos = box_resp.get("boxInfos", [])
        for box in box_infos:
            status = box.get("status", "")
            bt = box.get("businessType", "")
            desc = box.get("desc", "")
            if status == "CAN_RECEIVE" and bt:
                data = self._get_market_base_params()
                data["businessType"] = bt
                data["operatingSystem"] = "ANDROID"
                result = self._market_post("/market-center/api2/dial/openBox", data)
                if result["success"]:
                    rd = result["data"]
                    awards = rd.get("openBoxAwards", [])
                    for award in awards:
                        at = award.get("awardType", "")
                        amt = award.get("amount", 0)
                        bz = award.get("bizNo", "")
                        if at == "GOLD":
                            print(f"  转盘宝箱({desc}): +{amt}金币")
                        elif at == "DOUBLE_VIDEO" and bz:
                            self._dial_advert_expose("DOUBLE_VIDEO")
                            time.sleep(1)
                            card = self._dial_receive_double_card(bz)
                            if card["success"]:
                                bag = card["data"].get("dialCardBag", {})
                                cnt = bag.get("DOUBLE", 0)
                                print(f"  转盘宝箱({desc}): 获得翻倍卡 (共{cnt}张)")
                            else:
                                print(f"  转盘宝箱翻倍卡: {card.get('error')}")
                        else:
                            print(f"  转盘宝箱({desc}): {at} x{amt}")
                    new_box = rd.get("dialBoxResp", {})
                    if new_box:
                        new_infos = new_box.get("boxInfos", [])
                        for ni in new_infos:
                            if ni.get("status") == "CAN_RECEIVE":
                                box_infos.append(ni)
                else:
                    print(f"  转盘宝箱({desc}): {result.get('error')}")
                time.sleep(1)

    def _gacha_index(self):
        return self._market_get("/market-center/api2/gacha/index")

    def _gacha_finish_ad(self):
        data = self._get_market_base_params()
        data["businessType"] = "JJB_DAILY_GACHA_INC_VIDEOS"
        return self._market_post("/market-center/api2/gacha/finishGachaTask", data)

    def _gacha_lucky_draw(self):
        data = self._get_market_base_params()
        data["operatingSystem"] = "ANDROID"
        return self._market_post("/market-center/api2/gacha/luckyDraw", data)

    def _gacha_treasure_chest(self):
        return self._market_get("/market-center/api2/gacha/treasureChest")

    def _clock_out_apply(self):
        data = self._get_market_base_params()
        data["period"] = time.strftime("%Y-%m-%d")
        return self._market_post("/market-center/api2/clockOut/clockOutApply", data)

    def _clock_out_index_core(self):
        params = self._get_market_base_params()
        return self._market_get("/market-center/api2/clockOut/indexCore", params)

    def _clockout_box_process(self):
        params = self._get_market_base_params()
        return self._market_get("/market-center/api2/clockOut/clockoutBoxProcess", params)

    def _clockout_box_award_list(self):
        params = self._get_market_base_params()
        return self._market_get("/market-center/api2/clockOut/clockoutBoxAwardList", params)

    def _fish_index(self):
        params = self._get_market_base_params()
        params["operatingSystem"] = "ANDROID"
        return self._market_get("/market-center/api2/fish/index", params)

    def _fish_index_expand(self):
        params = self._get_market_base_params()
        params["operatingSystem"] = "ANDROID"
        return self._market_get("/market-center/api2/fish/indexExpand", params)

    def _fish_finish_ad(self):
        data = self._get_market_base_params()
        data["businessType"] = "JJB_FISH_DAILY_SEE_VIDEOS"
        data["operatingSystem"] = "ANDROID"
        return self._market_post("/market-center/api2/fish/finishFishNormalTask", data)

    def _fish_lucky_draw(self, kind_id):
        data = self._get_market_base_params()
        data["kindId"] = kind_id
        data["operatingSystem"] = "ANDROID"
        return self._market_post("/market-center/api2/fish/luckyDraw", data)

    def _fish_double(self, biz_no):
        data = {
            "signatureCode": SIGNATURE_CODE,
            "operatingSystem": "Android",
            "appChannel": self.channel,
            "jid": self.jjb_uid,
            "deviceToken": self.device_id,
            "appVersion": self.version,
            "bizNo": biz_no,
        }
        return self._market_post("/market-center/api2/fish/incVideosGoldDouble", data)

# ============================================================
#  环境变量解析
# ============================================================

def parse_users():
    """
    优先通过青龙 API 读取全部 axjjb 环境变量，失败时回退到进程环境变量
    
    格式支持：
    1. device_id#android_id#token
    2. token#android_id (device_id = base64(android_id))
    3. token (使用固定 device_id/android_id)
    
    多账号：@ 分隔 或 换行分隔
    """
    env_values = []

    ql = QinglongClient()
    if ql.is_configured():
        try:
            envs = ql.get_envs("axjjb")
            env_values = [str(env.get("value", "")).strip() for env in envs if str(env.get("value", "")).strip()]
            if env_values:
                log_info(f"通过青龙 API 读取到 {len(env_values)} 个 axjjb 环境变量")
        except Exception as e:
            log_warn(f"通过青龙 API 读取 axjjb 失败，回退本地环境变量: {e}")

    if not env_values:
        raw = os.environ.get("axjjb", "").strip()
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
            # 格式: device_id#android_id#token
            users.append({
                "token": fields[2].strip(),
                "android_id": fields[1].strip(),
                "device_id": fields[0].strip(),
            })
        elif len(fields) == 2:
            # 格式: token#android_id (device_id 自动计算为 base64(android_id))
            android_id = fields[1].strip()
            device_id = base64.b64encode(android_id.encode()).decode('ascii')
            users.append({
                "token": fields[0].strip(),
                "android_id": android_id,
                "device_id": device_id,
            })
        elif len(fields) == 1 and fields[0].strip():
            # 格式: 仅 token (使用固定 device_id/android_id)
            users.append({
                "token": fields[0].strip(),
                "android_id": FIXED_ANDROID_ID,
                "device_id": FIXED_DEVICE_ID,
            })
        else:
            log_warn(f"⚠️ 格式不完整，跳过: {part[:20]}...")
    
    return users

# ============================================================
#  主入口
# ============================================================

def main():
    print(f"\n{'='*50}")
    print("  安心记加班 - 全任务自动执行")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    users = parse_users()
    if not users:
        log_err("未找到环境变量 axjjb 😔")
        log_info("格式: token 或 token#android_id 或 device_id#android_id#token")
        log_info("多用户: @ 分隔 或 换行")
        log_info("也可配置青龙环境变量: QL_API_URL, QL_CLIENT_ID, QL_CLIENT_SECRET")
        sys.exit(1)

    log_ok(f"共加载 {len(users)} 个账号 🎉")
    
    # 初始化代理管理器
    proxy_manager = ProxyManager(PROXY_API_URL)

    for i, user in enumerate(users, 1):
        try:
            token = user.get("token", "")
            android_id = user.get("android_id")
            device_id = user.get("device_id")
            
            if not token:
                log_err(f"账号{i} 无效，跳过")
                continue
            
            log_section(f"👤 账号[{i}/{len(users)}]")
            log_info(f"Token: {token[:20]}...")
            
            client = TaskRunner(device_id=device_id, android_id=android_id, proxy_manager=proxy_manager)
            client.token = token
            client.jjb_uid = token            
            client.run_all()
            
        except Exception as e:
            log_err(f"账号{i} 执行出错: {e}")
        
        if i < len(users):
            wait_seconds = random.randint(*ACCOUNT_WAIT_RANGE)
            log_info(f"等待 {wait_seconds} 秒后处理下一个账号...")
            time.sleep(wait_seconds)

    print(f"\n{'='*50}")
    print("  全部账号执行完毕! 🎉")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  ⚠️  用户取消")
        sys.exit(0)
