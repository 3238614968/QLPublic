"""
小时工计帐 - 青龙面板定时任务脚本
@author: xiaohai
环境变量: xsgjz
格式: device_id#android_id#token 或 仅 token
多用户: 多个同名环境变量 或 @ 分隔
代理: 默认启用，通过 PROXY_API_URL 环境变量配置
完成任务：
    1. 签到
    2. 激励视频
    3. 福利商城浏览
    4. 转盘抽奖
    5. 扭蛋抽奖
    6. 任务列表领奖
    7. 红包兑换
"""

import os
import sys
import json
import time
import random
import string
import base64
import hashlib
import asyncio
import logging

import httpx
import requests

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

PROXY_API_URL = os.getenv("PROXY_API_URL", "")
PROXY_LEASE_SECONDS = 120
PROXY_REFRESH_BUFFER = 10

HAR_DEVICE_TOKEN = "4249e79ab17b0a948a7503712647c443"
HAR_DEVICE_UNIQUE_CODE = "adlJEoF/sJUDABSzR0w8ysLd"
HAR_ANDROID_ID = "8f06b3aa5fc50be8"
SECURITY_SALT = "Bb50B9l8Fq6HayMp"
NONCE_SECRET = "Gcy9zywM9PJ8JaGCviMTdgjLd73xexjm"

XSG_API = "https://xsg-api.julanling.com"
MARKET_API = "https://market-gateway.julanling.com"


def log_title(text):
    print("\n" + "=" * 50)
    print("  {}".format(text))
    print("=" * 50)


def log_section(text):
    print("\n  {} {}".format(text, "-" * (45 - len(text) * 2)))


def log_ok(text):
    print("    \u2705 {}".format(text))


def log_err(text):
    print("    \u274c {}".format(text))


def log_info(text):
    print("    \u2139\ufe0f {}".format(text))


def log_warn(text):
    print("    \u26a0\ufe0f {}".format(text))


def log_coin(text):
    print("    \U0001f4b0 {}".format(text))


class ProxyManager:
    def __init__(self, proxy_api_url):
        self.proxy_api_url = proxy_api_url
        self.current_proxy = None
        self.expires_at = 0

    def _parse_proxy_text(self, text):
        proxy = text.strip().splitlines()[0].strip()
        if not proxy or ":" not in proxy:
            raise ValueError("代理接口返回格式无效")
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


class QinglongClient:
    def __init__(self):
        self.base_url = os.getenv("QL_API_URL", "http://127.0.0.1:5700")
        self.client_id = os.getenv("QL_CLIENT_ID")
        self.client_secret = os.getenv("QL_CLIENT_SECRET")
        self.token = None

    def is_configured(self):
        return bool(self.client_id and self.client_secret)

    def get_token(self):
        if self.token:
            return self.token
        url = "{}/open/auth/token".format(self.base_url.rstrip("/"))
        params = {"client_id": self.client_id, "client_secret": self.client_secret}
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


def parse_xsg_env_values(env_value):
    accounts = []
    if not env_value:
        return accounts
    values = env_value.split("@")
    for val in values:
        val = val.strip()
        if not val:
            continue
        parts = val.split("#")
        if len(parts) >= 3:
            accounts.append({"device_id": parts[0], "android_id": parts[1], "token": "#".join(parts[2:])})
        elif len(parts) == 1 and parts[0].startswith("eyJ"):
            accounts.append({"device_id": HAR_DEVICE_TOKEN, "android_id": HAR_ANDROID_ID, "token": parts[0]})
        else:
            accounts.append({"device_id": HAR_DEVICE_TOKEN, "android_id": HAR_ANDROID_ID, "token": val})
    return accounts


def parse_users():
    env_values = []
    ql = QinglongClient()
    if ql.is_configured():
        try:
            envs = ql.get_envs("xsgjz")
            env_values = [str(env.get("value", "")).strip() for env in envs if str(env.get("value", "")).strip()]
            if env_values:
                log_info("通过青龙 API 读取到 {} 个 xsgjz 环境变量".format(len(env_values)))
        except Exception as e:
            log_warn("通过青龙 API 读取 xsgjz 失败，回退本地环境变量: {}".format(e))

    if not env_values:
        raw = os.environ.get("xsgjz", "").strip()
        if raw:
            env_values.append(raw)

    if not env_values:
        return []

    all_accounts = []
    for raw in env_values:
        if not raw:
            continue
        accounts = parse_xsg_env_values(raw)
        all_accounts.extend(accounts)

    return all_accounts


def _decode_jwt_payload(token):
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding
            decoded = base64.urlsafe_b64decode(payload)
            return json.loads(decoded)
    except Exception:
        pass
    return {}


def _is_token_valid(token):
    payload = _decode_jwt_payload(token)
    if not payload:
        return False
    iat = payload.get("iat", 0)
    if iat and (time.time() - iat) > 86400 * 7:
        return False
    return True


class XiaoShiGongClient:
    def __init__(self, device_id=None, android_id=None, oaid=None):
        self.device_id = device_id or HAR_DEVICE_TOKEN
        self.android_id = android_id or HAR_ANDROID_ID
        self.oaid = oaid or ""
        self.token = None
        self.xsg_uid = None
        self.session = httpx.AsyncClient(verify=False, follow_redirects=True)
        self.app_activate_date = time.strftime("%Y-%m-%d %H:%M:%S")
        self.version = "4.7.50"
        self.channel = "xsgjz_vivo"
        self.brand = "Redmi"
        self.manufacturer = "vivo"
        self.model = "23049RAD8C"
        self.os_version = "33"
        self.app_package = "com.julangling.xsgjz"
        self.cid = ""

    async def aclose(self):
        await self.session.aclose()

    def _gen_random_str(self, length=16):
        chars = string.ascii_lowercase + string.digits
        return "".join(random.choice(chars) for _ in range(length))

    def _gen_nonce_headers(self):
        qhcy = str(int(time.time()))
        qqjq = self._gen_random_str(16)
        feko = self._gen_random_str(16)
        raw = "{}&{}&{}&{}".format(qhcy, qqjq, feko, NONCE_SECRET)
        xq_plrr_csad = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return {"xq-plrr-csad": xq_plrr_csad, "mm-jfxi-qhcy": qhcy, "bx-rahc-qqjq": qqjq, "le-bjor-feko": feko}

    def _get_form_data(self, extra=None):
        data = {
            "appPackage": self.app_package, "appVersion": self.version, "operatingSystem": "ANDROID",
            "appChannel": self.channel, "appActivateDate": self.app_activate_date, "deviceToken": self.device_id,
            "manufacturer": self.manufacturer, "cidUm": "", "deviceUniqueCode": HAR_DEVICE_UNIQUE_CODE,
            "sdkVersion": self.os_version, "model": self.model, "brand": self.brand,
            "androidID": self.android_id, "oaid": self.oaid, "cid": self.cid,
        }
        if extra:
            data.update(extra)
        return data

    def _gen_security(self):
        t = str(int(time.time()))
        raw = "p={}&c={}&v={}&d={}&u={}&t={}".format(self.app_package, self.channel, self.version, self.device_id, HAR_DEVICE_UNIQUE_CODE, t)
        b64 = base64.b64encode(raw.encode("utf-8")).decode("utf-8").strip()
        sig = hashlib.md5((b64 + SECURITY_SALT).encode()).hexdigest()
        return "{}.{}".format(b64, sig)

    def _get_headers_xsg(self, token=None, with_security=False):
        auth = "Bearer {}".format(token) if token else "Bearer"
        headers = {"authorization": auth, "content-type": "application/x-www-form-urlencoded", "user-agent": "okhttp/3.14.9", "accept-encoding": "gzip"}
        if with_security:
            headers["security"] = self._gen_security()
        return headers

    def _get_headers_xsg_h5(self, token=None):
        uid_info = json.dumps({"version": 4750, "versionName": self.version, "userType": "1", "sdkVersion": self.os_version, "statusBarHeight": 34, "toolBarHeight": 78, "imei": "imei", "oaid": self.oaid, "channel": self.channel, "uid": self.xsg_uid or ""})
        ua = "Mozilla/5.0 (Linux; Android 13; {} Build/TKQ1.221114.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/108.0.5359.128 Mobile Safari/537.36;_android{}_android".format(self.model, uid_info)
        auth = "Bearer {}".format(token) if token else ""
        nonce = self._gen_nonce_headers()
        return {"accept": "application/json, text/plain, */*", "xq-plrr-csad": nonce["xq-plrr-csad"], "mm-jfxi-qhcy": nonce["mm-jfxi-qhcy"], "le-bjor-feko": nonce["le-bjor-feko"], "authorization": auth, "bx-rahc-qqjq": nonce["bx-rahc-qqjq"], "user-agent": ua, "security": self._gen_security(), "origin": "https://xsg-api.julanling.com", "x-requested-with": "com.julangling.xsgjz", "sec-fetch-site": "same-origin", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty", "referer": "https://xsg-api.julanling.com/pages/actives/xsg_wallet/index.html?&needRefresh=true", "accept-encoding": "gzip, deflate", "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"}

    def _get_headers_market(self, token=None):
        uid_info = json.dumps({"version": 4750, "versionName": self.version, "userType": "1", "sdkVersion": self.os_version, "statusBarHeight": 34, "toolBarHeight": 78, "oaid": self.oaid, "channel": self.channel, "uid": self.xsg_uid or ""})
        ua = "Mozilla/5.0 (Linux; Android 13; {} Build/TKQ1.221114.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/108.0.5359.128 Mobile Safari/537.36;_android{}_android".format(self.model, uid_info)
        auth = "Bearer {}".format(token) if token else ""
        nonce = self._gen_nonce_headers()
        return {"pragma": "no-cache", "cache-control": "no-cache", "accept": "application/json, text/plain, */*", "xq-plrr-csad": nonce["xq-plrr-csad"], "mm-jfxi-qhcy": nonce["mm-jfxi-qhcy"], "le-bjor-feko": nonce["le-bjor-feko"], "authorization": auth, "bx-rahc-qqjq": nonce["bx-rahc-qqjq"], "user-agent": ua, "origin": "https://market-h5.julanling.com", "x-requested-with": "com.julangling.xsgjz", "sec-fetch-site": "same-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty", "referer": "https://market-h5.julanling.com/", "accept-encoding": "gzip, deflate", "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"}

    def _get_market_base_params(self):
        return {"os": "ANDROID", "appVersion": self.version, "appChannel": self.channel, "deviceToken": self.device_id, "app_activate_date": self.app_activate_date}

    async def _xsg_post_form(self, path, extra=None, token=None, with_security=False):
        url = "{}{}".format(XSG_API, path)
        headers = self._get_headers_xsg(token=token or self.token, with_security=with_security)
        data = self._get_form_data(extra)
        resp = await self.session.post(url, headers=headers, data=data, timeout=30)
        result = resp.json()
        if result.get("errorCode") == 0:
            return {"success": True, "data": result.get("results"), "token": result.get("token")}
        return {"success": False, "error": result.get("errorStr", "Unknown error")}

    async def _xsg_h5_post(self, path, extra=None):
        url = "{}{}".format(XSG_API, path)
        headers = self._get_headers_xsg_h5(token=self.token)
        headers["content-type"] = "application/x-www-form-urlencoded"
        data = {"appPackage": self.app_package, "appChannel": self.channel, "appVersion": self.version, "operatingSystem": "ANDROID"}
        if extra:
            data.update(extra)
        resp = await self.session.post(url, headers=headers, data=data, timeout=30)
        result = resp.json()
        if result.get("errorCode") == 0:
            return {"success": True, "data": result.get("results"), "token": result.get("token")}
        return {"success": False, "error": result.get("errorStr", "Unknown error")}

    async def _market_get(self, path):
        url = "{}{}".format(MARKET_API, path)
        headers = self._get_headers_market(token=self.token)
        params = self._get_market_base_params()
        resp = await self.session.get(url, headers=headers, params=params, timeout=30)
        result = resp.json()
        if result.get("errorCode") == 0:
            return {"success": True, "data": result.get("results")}
        return {"success": False, "error": result.get("errorStr", "Unknown error")}

    async def _market_post(self, path, data=None):
        url = "{}{}".format(MARKET_API, path)
        headers = self._get_headers_market(token=self.token)
        headers["content-type"] = "application/json;charset=UTF-8"
        body = self._get_market_base_params()
        if data:
            body.update(data)
        resp = await self.session.post(url, headers=headers, json=body, timeout=30)
        result = resp.json()
        if result.get("errorCode") == 0:
            return {"success": True, "data": result.get("results")}
        return {"success": False, "error": result.get("errorStr", "Unknown error")}

    async def query_coin_account(self):
        return await self._xsg_h5_post("/h5/api/activityThirdAccount/coin/getAccount")

    async def get_vip_status(self):
        return await self._xsg_post_form("/app/user/getVipStatus")

    async def get_task_list(self):
        url = "{}/market-center/api2/assignment/batchListByPositions".format(MARKET_API)
        headers = self._get_headers_market(token=self.token)
        params = self._get_market_base_params()
        params["positions"] = "MONEY_CENTER_NEW_WELFARE,MONEY_CENTER_DAILY_WELFARE,MONEY_CENTER_GLOBAL_WELFARE,MONEY_CENTER_WEEK_WELFARE"
        resp = await self.session.get(url, headers=headers, params=params, timeout=30)
        result = resp.json()
        if result.get("errorCode") == 0:
            return {"success": True, "data": result["results"]}
        return {"success": False, "error": result.get("errorStr", "Unknown error")}

    async def finish_task(self, business_type):
        return await self._market_post("/market-center/api2/assignment/finishAssignment", {"businessType": business_type})

    async def receive_award(self, business_type):
        return await self._market_post("/market-center/api2/assignment/receiveAwardByBusinessType", {"businessType": business_type})

    async def get_sign_info(self):
        return await self._market_get("/market-center/api2/signIn/signInfo")

    async def sign_in(self):
        return await self._market_post("/market-center/api2/signIn/signIn")

    async def finish_sign_video(self, business_type="SIGN_VIDEO_1"):
        return await self._market_post("/market-center/api2/signIn/finishSignVideo", {"businessType": business_type})

    async def is_new_user(self):
        return await self._market_get("/market-center/api2/activity/common/isNewUser?page=MONEY_CENTER")

    async def _finish_enter_task(self, business_type):
        return await self._market_post("/market-center/api2/task/finishOnceEnterTask", {"businessType": business_type, "operatingSystem": "ANDROID"})

    async def _dial_detail(self):
        return await self._market_get("/market-center/api2/dial/detailCore")

    async def _dial_lucky_draw(self):
        return await self._market_post("/market-center/api2/dial/luckyDraw", {"operatingSystem": "ANDROID"})

    async def _dial_receive_coin(self, biz_no):
        return await self._market_post("/market-center/api2/dial/receiveDialCoin", {"bizNo": biz_no, "operatingSystem": "ANDROID"})

    async def _dial_double_coin(self, biz_no):
        return await self._market_post("/market-center/api2/dial/receiveDialDoubleCoin", {"bizNo": biz_no, "operatingSystem": "ANDROID"})

    async def _dial_receive_double_card(self, biz_no):
        return await self._market_post("/market-center/api2/dial/receiveDoubleCardBag", {"bizNo": biz_no, "operatingSystem": "ANDROID"})

    async def _dial_advert_expose(self, advert_type="ADVERT_ONE_PIC"):
        return await self._market_post("/market-center/api2/dial/advertExpose", {"advertType": advert_type, "operatingSystem": "ANDROID"})

    async def _dial_inc_video(self):
        return await self._market_post("/market-center/api2/dial/incVideosGold", {"operatingSystem": "ANDROID"})

    async def _gacha_index(self):
        return await self._market_get("/market-center/api2/gacha/index")

    async def _gacha_finish_ad(self):
        return await self._market_post("/market-center/api2/gacha/finishGachaTask", {"businessType": "XSG_DAILY_GACHA_INC_VIDEOS"})

    async def _gacha_lucky_draw(self):
        return await self._market_post("/market-center/api2/gacha/luckyDraw", {"operatingSystem": "ANDROID"})

    async def _gacha_treasure_chest(self):
        return await self._market_get("/market-center/api2/gacha/treasureChest")

    async def _red_pack_exchange(self):
        return await self._market_post("/activity-third-account/api/cash/draw/redPackExchangeToCoin")

    async def _draw_index(self):
        return await self._market_get("/activity-third-account/api/cash/draw/drawIndex")


class XsgTaskScript:
    def __init__(self, token, device_id, android_id, proxy_manager=None):
        self.client = XiaoShiGongClient(device_id=device_id, android_id=android_id)
        self.client.token = token
        payload = _decode_jwt_payload(token)
        self.client.xsg_uid = str(payload.get("axUid", payload.get("uid", "")))
        self.proxy_manager = proxy_manager

    async def run_all(self):
        try:
            log_section("初始金币")
            await self._do_coin_account("初始")
            await asyncio.sleep(1)
            log_section("签到")
            await self._do_sign_in()
            await asyncio.sleep(1)
            log_section("激励视频")
            await self._do_watch_video()
            await asyncio.sleep(1)
            log_section("福利商城")
            await self._do_watch_mall()
            await asyncio.sleep(1)
            log_section("转盘")
            await self._do_dial()
            await asyncio.sleep(1)
            log_section("扭蛋")
            await self._do_gacha()
            await asyncio.sleep(1)
            log_section("任务领奖")
            await self._do_task_list_rewards()
            await asyncio.sleep(1)
            log_section("新人奖励")
            await self._do_new_user_rewards()
            await asyncio.sleep(1)
            log_section("红包兑换")
            await self._do_red_pack_exchange()
            await asyncio.sleep(1)
            log_section("VIP状态")
            await self._do_vip_status()
            await asyncio.sleep(1)
            log_section("提现信息")
            await self._do_draw_index()
            await asyncio.sleep(1)
            log_section("最终金币")
            await self._do_coin_account("最终")
            log_ok("全部任务执行完成!")
        except Exception as e:
            log_err("执行出错: {}".format(str(e)))
        finally:
            await self.client.aclose()

    async def _do_coin_account(self, label=""):
        result = await self.client.query_coin_account()
        if result["success"] and result.get("data"):
            d = result["data"]
            log_coin("{}金币: {}(\u2248{}元) 累计{} 冻结{}".format(label, d.get("credits", 0), d.get("aboutAmount", "0"), d.get("totalCredits", 0), d.get("freezeCredits", 0)))
        else:
            log_err("{}金币查询失败: {}".format(label, result.get("error", "未知")))

    async def _do_sign_in(self):
        sign_info = await self.client.get_sign_info()
        if not sign_info["success"] or not sign_info.get("data"):
            log_err("签到信息失败: {}".format(sign_info.get("error", "无数据")))
            return
        si = sign_info["data"]
        if si.get("isSignIn"):
            log_ok("今日已签到, 连续{}天".format(si.get("continuousDays")))
        else:
            sign_result = await self.client.sign_in()
            if sign_result["success"] and sign_result.get("data"):
                d = sign_result["data"]
                log_ok("签到成功! 连续{}天, +{}金币".format(d.get("continuousDays"), d.get("amount")))
            else:
                log_err("签到失败: {}".format(sign_result.get("error", "无数据")))
                return
        await asyncio.sleep(1)
        si_resp = await self.client.get_sign_info()
        if not si_resp["success"] or not si_resp.get("data"):
            return
        video_type = si_resp["data"].get("signVideoBusinessType")
        while video_type and si_resp["data"].get("currentType") == "SIGN_VIDEO":
            vr = await self.client.finish_sign_video(video_type)
            if vr["success"] and vr.get("data"):
                log_ok("签到视频: +{}金币".format(vr["data"].get("amount")))
            else:
                break
            await asyncio.sleep(1)
            si_resp = await self.client.get_sign_info()
            if not si_resp["success"] or not si_resp.get("data"):
                break
            video_type = si_resp["data"].get("signVideoBusinessType")

    async def _do_watch_video(self):
        count = 0
        while True:
            result = await self.client.finish_task("XSG_MONEY_CENTER_INCENTIVE_VIDEO")
            if result["success"] and result.get("data"):
                awards = result["data"].get("awardInfos", [])
                amount = awards[0]["amount"] if awards else 0
                remain = result["data"].get("remainTimes", 0)
                count += 1
                log_ok("看视频 +{}金币 (第{}次, 剩余{}次)".format(amount, count, remain))
                cool = result["data"].get("coolTime", 0)
                await asyncio.sleep(max(cool, 3))
            else:
                if count == 0:
                    log_warn("激励视频: {}".format(result.get("error", "已达上限")))
                break

    async def _do_watch_mall(self):
        count = 0
        while True:
            result = await self.client.finish_task("XSG_CSJ_MALL_INCREASE")
            if result["success"] and result.get("data"):
                awards = result["data"].get("awardInfos", [])
                amount = awards[0]["amount"] if awards else 0
                count += 1
                log_ok("浏览商城 +{}金币 (第{}次)".format(amount, count))
                await asyncio.sleep(12)
            else:
                if count == 0:
                    log_warn("浏览商城: {}".format(result.get("error", "已达上限")))
                break

    async def _do_dial(self):
        enter = await self.client._finish_enter_task("XSG_DAILY_DIAL")
        if enter["success"] and enter.get("data"):
            if enter["data"].get("status", "") != "DISABLE_RECEIVE":
                log_ok("转盘进入任务已完成")
        detail = await self.client._dial_detail()
        if not detail["success"] or not detail.get("data"):
            log_err("转盘详情: {}".format(detail.get("error", "无数据")))
            return
        dr = detail["data"]
        valid_num = dr.get("dialValidNum", 0)
        double_cards = dr.get("dialCardBag", {}).get("DOUBLE", 0)
        log_info("转盘可用{}次, 翻倍卡{}张".format(valid_num, double_cards))
        total_drawn = 0

        async def _handle_award(dd):
            nonlocal valid_num, double_cards, total_drawn
            award_type = dd.get("awardType", "")
            biz_no = dd.get("bizNo", "")
            amount = dd.get("amount", 0)
            total_drawn += 1
            if award_type == "GOLD":
                if biz_no:
                    if double_cards > 0:
                        cr = await self.client._dial_double_coin(biz_no)
                        if cr["success"] and cr.get("data"):
                            double_cards = max(0, double_cards - 1)
                            log_ok("转盘翻倍 +{}金币 (剩余翻倍卡{}张)".format(cr["data"].get("amount", 0), double_cards))
                        else:
                            cr = await self.client._dial_receive_coin(biz_no)
                            if cr["success"] and cr.get("data"):
                                log_ok("转盘 +{}金币".format(cr["data"].get("amount", 0)))
                    else:
                        cr = await self.client._dial_receive_coin(biz_no)
                        if cr["success"] and cr.get("data"):
                            log_ok("转盘 +{}金币".format(cr["data"].get("amount", 0)))
                else:
                    log_ok("转盘 +{}金币".format(amount))
            elif award_type == "ADVERT_ONE_PIC":
                await self.client._dial_advert_expose("ADVERT_ONE_PIC")
                log_ok("转盘看广告 +1次")
            elif award_type == "DOUBLE_VIDEO":
                await self.client._dial_advert_expose("DOUBLE_VIDEO")
                if biz_no:
                    await asyncio.sleep(1)
                    card = await self.client._dial_receive_double_card(biz_no)
                    if card["success"] and card.get("data"):
                        cnt = card["data"].get("dialCardBag", {}).get("DOUBLE", 0)
                        double_cards = cnt
                        log_ok("转盘看视频获翻倍卡 (共{}张)".format(cnt))
            elif award_type == "GOLD_VIDEO":
                await self.client._dial_advert_expose("GOLD_VIDEO")
                await asyncio.sleep(1)
                inc = await self.client._dial_inc_video()
                if inc["success"] and inc.get("data"):
                    new_valid = inc["data"].get("dialValidNum", valid_num)
                    added = new_valid - valid_num
                    valid_num = new_valid
                    log_ok("转盘看金币视频 +{}次 (共{}次)".format(added, valid_num))

        while valid_num > 0:
            draw = await self.client._dial_lucky_draw()
            if not draw["success"] or not draw.get("data"):
                log_err("转盘失败: {}".format(draw.get("error", "无数据")))
                break
            dd = draw["data"]
            valid_num = dd.get("dialValidNum", 0)
            double_cards = dd.get("dialCardBag", {}).get("DOUBLE", double_cards)
            await _handle_award(dd)
            await asyncio.sleep(2)

        detail2 = await self.client._dial_detail()
        if detail2["success"] and detail2.get("data"):
            box_resp = detail2["data"].get("dialBoxResp", {})
            await self._dial_open_boxes(box_resp)
            new_valid = detail2["data"].get("dialValidNum", 0)
            if new_valid > 0:
                valid_num = new_valid
                while valid_num > 0:
                    draw = await self.client._dial_lucky_draw()
                    if not draw["success"] or not draw.get("data"):
                        break
                    dd = draw["data"]
                    valid_num = dd.get("dialValidNum", 0)
                    double_cards = dd.get("dialCardBag", {}).get("DOUBLE", double_cards)
                    await _handle_award(dd)
                    await asyncio.sleep(2)
        log_info("转盘共转{}次".format(total_drawn))

    async def _dial_open_boxes(self, box_resp=None):
        if not box_resp:
            return
        box_infos = list(box_resp.get("boxInfos", []))
        for box in box_infos:
            status = box.get("status", "")
            bt = box.get("businessType", "")
            desc = box.get("desc", "")
            if status == "CAN_RECEIVE" and bt:
                data = {"businessType": bt, "operatingSystem": "ANDROID"}
                result = await self.client._market_post("/market-center/api2/dial/openBox", data)
                if result["success"] and result.get("data"):
                    rd = result["data"]
                    for award in rd.get("openBoxAwards", []):
                        at = award.get("awardType", "")
                        amt = award.get("amount", 0)
                        bz = award.get("bizNo", "")
                        if at == "GOLD":
                            log_ok("转盘宝箱({}): +{}金币".format(desc, amt))
                        elif at == "DOUBLE_VIDEO" and bz:
                            await self.client._dial_advert_expose("DOUBLE_VIDEO")
                            await asyncio.sleep(1)
                            card = await self.client._dial_receive_double_card(bz)
                            if card["success"] and card.get("data"):
                                cnt = card["data"].get("dialCardBag", {}).get("DOUBLE", 0)
                                log_ok("转盘宝箱({}): 获得翻倍卡 (共{}张)".format(desc, cnt))
                        else:
                            log_info("转盘宝箱({}): {} x{}".format(desc, at, amt))
                    new_box = rd.get("dialBoxResp", {})
                    if new_box:
                        for ni in new_box.get("boxInfos", []):
                            if ni.get("status") == "CAN_RECEIVE":
                                box_infos.append(ni)
                await asyncio.sleep(1)

    async def _do_gacha(self):
        total_drawn = 0
        round_num = 0
        while True:
            round_num += 1
            index = await self.client._gacha_index()
            if not index["success"] or not index.get("data"):
                if round_num == 1:
                    log_err("扭蛋详情: {}".format(index.get("error", "无数据")))
                break
            idx = index["data"]
            remain_video = idx.get("remainVideoTimes", 0)
            remain = idx.get("remainTimes", 0)
            if remain <= 0 and remain_video <= 0:
                if round_num == 1:
                    log_warn("扭蛋: 无可用次数")
                break
            if round_num == 1:
                log_info("扭蛋可用{}次, 广告{}次".format(remain, remain_video))
            if remain_video > 0:
                for _ in range(remain_video):
                    ad_result = await self.client._gacha_finish_ad()
                    if ad_result["success"] and ad_result.get("data"):
                        ad = ad_result["data"]
                        log_ok("扭蛋看视频 +{}次 (剩余{}次)".format(ad.get("amount", 0), ad.get("remainTimes", 0)))
                    else:
                        break
                    await asyncio.sleep(3)
                index = await self.client._gacha_index()
                if not index["success"] or not index.get("data"):
                    break
                remain = index["data"].get("remainTimes", 0)
            if remain <= 0:
                break
            for _ in range(remain):
                draw = await self.client._gacha_lucky_draw()
                if draw["success"] and draw.get("data"):
                    name = draw["data"].get("name", "")
                    award_type = draw["data"].get("awardType", "")
                    amount = draw["data"].get("amount", "")
                    chip = draw["data"].get("chipNum", 0)
                    total_drawn += 1
                    if award_type == "ADVERT":
                        log_info("扭蛋抽到空气")
                    elif chip and int(chip) > 0:
                        log_ok("扭蛋抽到: {} x{}".format(name, chip))
                    else:
                        log_ok("扭蛋抽到: {} {}".format(name, amount))
                else:
                    log_err("扭蛋失败: {}".format(draw.get("error", "无数据")))
                    break
                await asyncio.sleep(2)
        chest = await self.client._gacha_treasure_chest()
        if chest["success"] and chest.get("data"):
            for chip in chest["data"].get("chips", []):
                log_info("扭蛋碎片: {} {}".format(chip.get("name", ""), chip.get("amount", "")))
        log_info("扭蛋共抽{}次".format(total_drawn))

    async def _do_task_list_rewards(self):
        task_result = await self.client.get_task_list()
        if not task_result["success"] or not task_result.get("data"):
            log_err("获取任务列表: {}".format(task_result.get("error", "无数据")))
            return
        positions = ["MONEY_CENTER_NEW_WELFARE", "MONEY_CENTER_DAILY_WELFARE", "MONEY_CENTER_GLOBAL_WELFARE", "MONEY_CENTER_WEEK_WELFARE"]
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
                    finish = await self.client.finish_task(bt)
                    if finish["success"]:
                        log_ok("完成任务: {}".format(title))
                        await asyncio.sleep(1)
                if msg in ["\u9886\u91d1\u5e01", "\u5f85\u9886\u53d6"] and bt:
                    award = await self.client.receive_award(bt)
                    if award["success"] and award.get("data"):
                        awards = award["data"].get("awardInfos", [])
                        amount = awards[0]["amount"] if awards else 0
                        log_ok("\u9886\u5956: {} +{}\u91d1\u5e01".format(title, amount))
                        claimed += 1
                    await asyncio.sleep(1)
        if claimed == 0:
            log_warn("任务列表: 无可领取奖励")

    async def _do_new_user_rewards(self):
        is_new = await self.client.is_new_user()
        if not is_new["success"]:
            log_warn("新人检查: {}".format(is_new.get("error", "未知错误")))
            return
        if is_new.get("data"):
            log_ok("是新用户, 尝试领取新人奖励...")
            for bt in ["XSG_FIRST_LOGIN"]:
                award = await self.client.receive_award(bt)
                if award["success"] and award.get("data"):
                    awards = award["data"].get("awardInfos", [])
                    amount = awards[0]["amount"] if awards else 0
                    log_ok("新人奖励 {}: +{}金币".format(bt, amount))
                else:
                    log_warn("新人奖励 {}: {}".format(bt, award.get("error", "无数据")))
                await asyncio.sleep(1)
        else:
            log_info("非新用户")

    async def _do_red_pack_exchange(self):
        result = await self.client._red_pack_exchange()
        if result["success"] and result.get("data"):
            d = result["data"]
            if d.get("showAble", False):
                log_ok("红包兑换: {}元 -> {}金币".format(d.get("exchangeRedPackAmount", "0"), d.get("requireCoinAmount", "0")))
            else:
                log_info("红包兑换: 无可兑换")
        else:
            log_warn("红包兑换: {}".format(result.get("error", "无数据")))

    async def _do_vip_status(self):
        result = await self.client.get_vip_status()
        if result["success"] and result.get("data"):
            d = result["data"]
            if d.get("vip", False):
                log_ok("VIP: 是, 状态: {}, 到期: {}".format(d.get("vipStatus", "UNKNOWN"), d.get("expireTime", "")))
            else:
                log_info("VIP: 否 (状态: {})".format(d.get("vipStatus", "UNKNOWN")))
        else:
            log_warn("VIP状态: {}".format(result.get("error", "无数据")))

    async def _do_draw_index(self):
        result = await self.client._draw_index()
        if result["success"] and result.get("data"):
            d = result["data"]
            log_coin("余额: {}(约{}元)".format(d.get("balanceAmount", 0), d.get("aboutAmount", "0")))
            for case in d.get("drawCaseResps", []):
                cash_able = case.get("cashAble", False)
                log_info("  {}: {} {}".format(case.get("cash", ""), case.get("credits", ""), "[\u53ef\u63d0\u73b0]" if cash_able else ""))
        else:
            log_warn("提现信息: {}".format(result.get("error", "无数据")))


def main():
    log_title("小时工计帐 - 青龙面板定时任务")
    users = parse_users()
    if not users:
        log_err("未找到任何账号，请检查环境变量 xsgjz")
        return

    log_info("共解析到 {} 个账号".format(len(users)))
    proxy_manager = ProxyManager(PROXY_API_URL) if PROXY_API_URL else None

    for idx, acc in enumerate(users, 1):
        token = acc["token"]
        device_id = acc["device_id"]
        android_id = acc["android_id"]
        log_title("账号 {}/{}".format(idx, len(users)))
        if not _is_token_valid(token):
            log_err("Token无效或已过期, 跳过")
            continue
        script = XsgTaskScript(token=token, device_id=device_id, android_id=android_id, proxy_manager=proxy_manager)
        asyncio.run(script.run_all())
        if idx < len(users):
            wait = random.randint(3, 8)
            log_info("等待 {} 秒后执行下一个账号...".format(wait))
            time.sleep(wait)

    log_title("全部账号执行完毕")


if __name__ == "__main__":
    main()
