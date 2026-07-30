"""
基元律动自动注册

手机号码自动获取：通过「豪猪接码」平台 (haozhuma) 官方 HTTP 接口自动取号 + 取短信验证码，
实现完全无人值守的全自动注册。不可用时回退为手动输入模式。

流程（每个账号）：
  接码平台取号 -> tokenrhythm 发短信 -> 接码平台取验证码
  -> 携带邀请码注册 -> 提取 API Key -> 发送一条测试对话
  -> 查询余额 -> Key 保存到本地 JSON

限制：单个邀请码每日最多邀请 MAX_PER_DAY 次，到达上限自动停止。
豪猪入口：https://h5.haozhuma.com/reg.html?action=xiaohai886
平台入口：https://tokenrhythm.studio/login
"""
import json
import os
import random
import re
import string
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("请先安装依赖：py -m pip install requests")


class HaozhuAPIError(Exception):
    """豪猪接口业务/网络错误。"""


class HaozhuTimeoutError(Exception):
    """豪猪等待验证码超时。"""


# ========================= 配置区 =========================
BASE = "https://tokenrhythm.studio"
MODEL = "deepseek-v4-flash"
MAX_PER_DAY = 10                                   
INVITE_CODES = [
    "rf_tr_N-VbIyF18p54JWRrzIID_jLW",
    "rf_tr_WR1xRNey11U5XvRtZlkgBDmV",
]
# 豪猪接码平台配置
HZ_USER = "1"
HZ_PASS = "1"
HZ_SID  = "118255"
HZ_SERVER = "https://api.haozhuma.com"
AUTO_PHONE = True

# 手动模式兜底手机号
MANUAL_PHONE = "13800138000"

HZ_TOKEN_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hz_token_cache.json")
HZ_BLACKLIST_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hz_blacklist.json")

# 代理
PROXY_ENABLED = True
IPZAN_URL = ("https://service.ipzan.com/core-extract?num=1&no=20231101999629924324"
             "&minute=3&format=txt&protocol=1&pool=quality&mode=whitelist&secret=tim6luq6di44pg")
PROXY_LIFETIME = 180   
SEND_COOLDOWN = 5       

POLL_MODE = True

SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "account_keys.json")

UA_MOBILE = ("Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/85.0.4183.121 Mobile Safari/537.36")
UA_NEWER = ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36")
# =========================================================


# ========================= 代理 / 接码平台辅助 =========================
class ProxyManager:
    def __init__(self, url, lifetime=180, retries=3):
        self.url = url
        self.lifetime = lifetime
        self.retries = retries
        self.proxy = None
        self.acquired_at = 0.0

    def _fetch(self):
        last_err = None
        for _ in range(self.retries):
            try:
                # 取代理本身不走代理，使用直连请求
                r = requests.get(self.url, timeout=20)
                text = (r.text or "").strip()
                line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
                if ":" not in line:
                    last_err = f"返回格式异常: {text!r}"
                    continue
                self.proxy = line
                self.acquired_at = time.time()
                return
            except Exception as e:
                last_err = str(e)
                time.sleep(1)
        raise RuntimeError(f"获取代理失败: {last_err}")

    def ensure(self):
        """确保当前有可用代理；过期则重新提取。返回 'ip:port'。"""
        now = time.time()
        if self.proxy is None or (now - self.acquired_at) >= self.lifetime:
            print(">> 提取/更换代理 IP ...")
            self._fetch()
            print(f">> 当前代理：{self.proxy}（有效期约 {self.lifetime}s）")
        return self.proxy

    def proxies(self):
        """返回 requests 用的代理字典（http/https 均走同一代理）。"""
        if not PROXY_ENABLED:
            return {}
        ip_port = self.ensure()
        return {"http": f"http://{ip_port}", "https": f"http://{ip_port}"}


proxy_mgr = ProxyManager(IPZAN_URL, PROXY_LIFETIME) if PROXY_ENABLED else None


def apply_proxy(session):
    """把当前代理设置到 requests 会话；未启用代理时清空。"""
    session.proxies = proxy_mgr.proxies() if proxy_mgr else {}


class HaozhuBalanceEmpty(Exception):
    """豪猪余额不足，无法继续注册新账号。"""
    pass


def get_hz_balance(sms):
    """读取豪猪账号余额（元），失败返回 None。"""
    try:
        summary = sms.get_summary()
        money = summary.get("money") if isinstance(summary, dict) else getattr(summary, "money", None)
        return float(money) if money is not None else None
    except Exception:
        return None


def hz_insufficient(e):
    """判断接码异常是否为余额不足导致。"""
    msg = str(e).lower()
    return any(k in msg for k in ("余额", "balance", "不足", "insufficient", "money"))

class HaozhuClient:
    def __init__(self, user, password, sid, server=HZ_SERVER, timeout=15,
                 poll_interval=3, wait_timeout=40, token_cache_path=None,
                 blacklist_path=None):
        self.user = user
        self.password = password
        self.sid = sid
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.wait_timeout = wait_timeout
        self.token_cache_path = token_cache_path
        self.blacklist_path = blacklist_path
        self.token = self._load_token()
        self.blacklist = self._load_blacklist()
        self.session = requests.Session()

    # ---- token 本地缓存 ----
    def _load_token(self):
        if self.token_cache_path and os.path.exists(self.token_cache_path):
            try:
                with open(self.token_cache_path, "r", encoding="utf-8") as f:
                    return json.load(f).get("token")
            except Exception:
                pass
        return None

    def _save_token(self, token):
        self.token = token
        if self.token_cache_path:
            try:
                os.makedirs(os.path.dirname(self.token_cache_path), exist_ok=True)
                with open(self.token_cache_path, "w", encoding="utf-8") as f:
                    json.dump({"token": token, "saved_at": time.time()}, f)
            except Exception:
                pass

    def _clear_token(self):
        self.token = None
        if self.token_cache_path and os.path.exists(self.token_cache_path):
            try:
                os.remove(self.token_cache_path)
            except Exception:
                pass

    # ---- 黑名单本地缓存 ----
    def _load_blacklist(self):
        if self.blacklist_path and os.path.exists(self.blacklist_path):
            try:
                with open(self.blacklist_path, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception:
                pass
        return set()

    def _save_blacklist(self):
        if self.blacklist_path:
            try:
                os.makedirs(os.path.dirname(self.blacklist_path), exist_ok=True)
                with open(self.blacklist_path, "w", encoding="utf-8") as f:
                    json.dump(sorted(self.blacklist), f)
            except Exception:
                pass

    def is_blacklisted(self, phone):
        return phone in self.blacklist

    # ---- HTTP 底层 ----
    # 豪猪接口不走代理（代理仅用于 tokenrhythm.studio）
    def _url(self, api):
        return f"{self.server}/sms/?api={api}"

    def _req(self, api, params=None):
        params = dict(params or {})
        if self.token:
            params.setdefault("token", self.token)
        last = None
        for _ in range(3):
            try:
                r = self.session.get(self._url(api), params=params,
                                     timeout=self.timeout)
                return r.json()
            except Exception as e:
                last = e
                time.sleep(1)
        raise HaozhuAPIError(f"{api} 请求失败: {last}")

    def _call(self, api, params=None):
        """业务调用：成功返回 data；token 失效则重新登录一次重试；其余失败抛异常。"""
        for attempt in range(2):
            data = self._req(api, params)
            if str(data.get("code")) in ("0", "200"):
                return data
            msg = str(data.get("msg", "")).lower()
            if attempt == 0 and any(k in msg for k in ("token", "令牌", "登录", "失效", "未授权", "auth")):
                self._clear_token()
                self.login()
                continue
            raise HaozhuAPIError(f"{api} 失败: {data}")
        raise HaozhuAPIError(f"{api} 失败")

    # ---- 对外接口 ----
    def ensure_token(self):
        if self.token:
            return self.token
        return self.login()

    def login(self):
        data = self._call("login", {"user": self.user, "pass": self.password})
        token = data.get("token")
        if not token:
            raise HaozhuAPIError(f"登录未返回 token: {data}")
        self._save_token(token)
        return token

    def get_summary(self):
        return self._call("getSummary")

    def get_phone(self, skip_blacklist=True):
        for _ in range(5):
            data = self._call("getPhone", {"sid": self.sid})
            phone = data.get("phone")
            if not phone:
                raise HaozhuAPIError(f"取号失败: {data}")
            if skip_blacklist and self.is_blacklisted(phone):
                print(f">> 号码 {phone} 已被拉黑，释放后重新取号 ...")
                self.release_phone(phone)
                time.sleep(1)
                continue
            return phone
        raise HaozhuAPIError("连续 5 次取号均为已拉黑号码，取号失败")

    def get_message(self, phone):
        """读取一次短信；无验证码返回 None（用于轮询）；token 失效自动重登。"""
        for attempt in range(2):
            try:
                data = self._req("getMessage", {"sid": self.sid, "phone": phone})
            except Exception:
                return None
            code = str(data.get("code", ""))
            # 0 / 200 = 成功拿到短信内容
            if code in ("0", "200"):
                yzm = data.get("yzm")
                if yzm:
                    return str(yzm)
                return _extract_code(data.get("sms", ""))
            # 未收到短信（例如 code=-1 msg="暂无短信"）：正常轮询中，返回 None
            msg = str(data.get("msg", "")).lower()
            if attempt == 0 and any(k in msg for k in ("token", "令牌", "登录", "失效", "未授权", "auth")):
                self._clear_token()
                self.login()
                continue
            # 其他非成功码也当作尚未收到短信（不抛异常，继续轮询）
            return None
        return None

    def poll_code(self, phone):
        print(f">> 等待验证码（最长 {self.wait_timeout}s，每 {self.poll_interval}s 查询一次）...", end="", flush=True)
        deadline = time.time() + self.wait_timeout
        while time.time() < deadline:
            yzm = self.get_message(phone)
            if yzm:
                print()  # 换行
                return yzm
            print(".", end="", flush=True)
            time.sleep(self.poll_interval)
        print()  # 换行
        raise HaozhuTimeoutError(f"等待验证码超时: {phone}")

    def release_phone(self, phone):
        """释放号码（cancelRecv），不来码或老号可释放；失败忽略。"""
        try:
            self._req("cancelRecv", {"sid": self.sid, "phone": phone})
        except Exception:
            pass

    def blacklist_phone(self, phone):
        """拉黑号码（addBlacklist），不再分配该号；并持久化到本地 JSON。"""
        self.blacklist.add(phone)
        self._save_blacklist()
        try:
            self._req("addBlacklist", {"sid": self.sid, "phone": phone})
        except Exception:
            pass


def _extract_code(text):
    """从短信正文中提取数字验证码。"""
    m = re.search(r"(?<!\d)(\d{4,8})(?!\d)", text)
    return m.group(1) if m else None


def gen_device():
    return "".join(random.choices(string.ascii_letters + string.digits, k=32))


def new_session():
    s = requests.Session()
    s.cookies.set("tr_ref_device", gen_device())
    s.headers.update({
        "authority": "tokenrhythm.studio",
        "user-agent": UA_MOBILE,
        "content-type": "application/json",
        "accept": "*/*",
        "origin": BASE,
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "accept-language": "zh-CN,zh;q=0.9",
        "referer": f"{BASE}/i/{INVITE_CODES[0]}" if INVITE_CODES else f"{BASE}/",
    })
    return s


def send_sms(s, phone, invite_code):
    """请求1：发送短信验证码（referer 带上当前邀请码）。"""
    s.headers["referer"] = f"{BASE}/i/{invite_code}"
    r = s.post(f"{BASE}/api/auth/sms/send",
               json={"phone": phone, "scene": "register"})
    return r.json()


def register(s, phone, code, invite_code):
    """请求2：验证码注册（带邀请码）。"""
    r = s.post(f"{BASE}/api/auth/register", json={
        "phone": phone,
        "verifyType": "sms",
        "smsCode": code,
        "inviteCode": invite_code,
        "termsAccepted": True,
        "termsVersion": "2026-07-09",
    })
    return r.json()


def get_referral_today(invite_code, device):
    
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "referer": f"{BASE}/account/referrals",
        "user-agent": UA_NEWER,
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
    }
    try:
        r = requests.get(f"{BASE}/api/referrals/me",
                         headers=headers,
                         cookies={"tr_session": invite_code, "tr_ref_device": device},
                         timeout=15)
        d = r.json().get("data", {})
    except Exception:
        return 0, MAX_PER_DAY
    return d.get("todayRewardedCount", 0), d.get("dailyRewardLimit", MAX_PER_DAY)


def chat(s, session_token, content="你好"):
    """请求3：发送一条对话（SSE 流式）。返回模型最终回复文本。"""
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "content-type": "application/json",
        "origin": BASE,
        "referer": f"{BASE}/playground",
        "user-agent": UA_NEWER,
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
    }
    r = s.post(f"{BASE}/api/playground/send",
               json={"model": MODEL, "messages": [{"role": "user", "content": content}], "stream": True},
               headers=headers, cookies={"tr_session": session_token}, stream=True)

    parts, done_content = [], None
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "done":
            done_content = (obj.get("message") or {}).get("content")
            break
        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            c = ((choices[0] or {}).get("delta") or {}).get("content")
            if c:
                parts.append(c)
    return "".join(parts) or (done_content or "")


def usage(s, session_token, device):
    """对话后查询余额使用信息。"""
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": f"{BASE}/account",
        "user-agent": UA_NEWER,
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
    }
    r = s.get(f"{BASE}/api/usage-summary",
              headers=headers, cookies={"tr_session": session_token, "tr_ref_device": device})
    return r.json()


def load_accounts():
    if os.path.exists(SAVE_FILE):
        try:
            loaded = json.load(open(SAVE_FILE, "r", encoding="utf-8"))
            return loaded if isinstance(loaded, list) else [loaded]
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_accounts(data):
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_balance(phone, balance, own_invite):
    """按手机号回写该账号的余额与自身邀请码。"""
    data = load_accounts()
    for rec in data:
        if rec.get("phone") == phone:
            rec["balanceCny"] = balance
            rec["ownInviteCode"] = own_invite
            break
    else:
        data.append({"phone": phone, "balanceCny": balance, "ownInviteCode": own_invite})
    save_accounts(data)


def is_invite_limit(reg):
    """判断是否为邀请码达每日上限。"""
    if reg.get("code") == 0:
        return False
    msg = str(reg.get("message", "")).lower()
    return any(k in msg for k in ("上限", "已达", "超过", "邀请", "invite", "limit", "次数"))


def process_account(s, sms, phone, code, invite_code):
    """完成单个账号的注册/对话/余额/保存。返回 ('ok'|'limit'|'error', info)。"""
    reg = register(s, phone, code, invite_code)
    if reg.get("code") != 0:
        if is_invite_limit(reg):
            return "limit", reg
        return "error", reg

    data = reg["data"]
    token = data["token"]
    api_key = data["initialApiKey"]["key"]
    user = data["user"]
    device = s.cookies.get("tr_ref_device")

    reply = chat(s, token, "你好")
    us = usage(s, token, device)
    u = us.get("data", {})
    balance = u.get("availableBalanceCny")
    own_invite = invite_code
    ref = data.get("referral") or {}
    applied_code = ref.get("inviteCode") or ref.get("code")
    if applied_code:
        own_invite = applied_code

    record = {
        "phone": phone,
        "userId": user["id"],
        "sessionToken": token,
        "apiKey": api_key,
        "apiKeyId": data["initialApiKey"]["id"],
        "apiKeyName": data["initialApiKey"].get("name"),
        "inviteCode": invite_code,      
        "ownInviteCode": own_invite,   
        "balanceCny": balance,          
        "model": MODEL,
        "createdAt": user.get("joinedAt"),
    }
    accounts = load_accounts()
    accounts.append(record)
    save_accounts(accounts)

    return "ok", {
        "phone": phone, "userId": user["id"], "apiKey": api_key,
        "ownInvite": own_invite, "reply": reply,
        "balance": balance, "cost": u.get("costCny"),
    }


def manual_loop(s, invite_code):
    """手动模式：输入手机号 + 验证码，用指定邀请码跑一次。"""
    phone = input(f"请输入手机号（回车默认 {MANUAL_PHONE}）：").strip() or MANUAL_PHONE
    sms_res = send_sms(s, phone, invite_code)
    if sms_res.get("code") != 0:
        print("   发送失败：", sms_res)
        return
    d = sms_res.get("data", {})
    print(f"   已发送至 {d.get('maskedTarget')}（{d.get('ttlSeconds')}s 有效）")
    code = input("   请输入短信验证码：").strip()
    if not code:
        print("   未输入验证码，退出。")
        return
    status, info = process_account(s, None, phone, code, invite_code)
    print_result(status, info)


def auto_loop(s, sms, session_tokens):
    
    total_ok = 0

    for idx, invite_code in enumerate(INVITE_CODES):
        print(f"\n########## 邀请码 [{idx + 1}/{len(INVITE_CODES)}] {invite_code} ##########")
        while True:
        
            token, device = session_tokens.get("_last", (None, gen_device()))
            today, limit = get_referral_today(token, device)
            if today >= limit:
                print(f"   [该码今日已奖励 {today}/{limit}，达上限，切换下一个]")
                break

            print(f"\n----- 本码第 {today + 1}/{limit} 个账号 -----")
        
            apply_proxy(s)
            bal = get_hz_balance(sms)
            if bal is not None and bal <= 0:
                print(">> 豪猪余额不足（0），停止自动注册。")
                raise HaozhuBalanceEmpty

            try:
                phone = sms.get_phone()
                print(f">> 接码平台取号：{phone}")
                if SEND_COOLDOWN:
                    time.sleep(SEND_COOLDOWN)
            except (HaozhuAPIError, HaozhuTimeoutError) as e:
                if hz_insufficient(e):
                    print("   豪猪余额不足，停止注册：", e)
                    raise HaozhuBalanceEmpty
                print("   取号失败：", e)
                break

            sms_res = send_sms(s, phone, invite_code)
            if sms_res.get("code") != 0:
                print("   tokenrhythm 发短信失败：", sms_res)
                try:
                    sms.release_phone(phone)
                except Exception:
                    pass
                retry = sms_res.get("data", {}).get("retryAfterSeconds") \
                    if isinstance(sms_res.get("data"), dict) else None
                if retry:
                    wait = int(retry) + 3
                    print(f"   等待 {wait}s 后重试 ...")
                    time.sleep(wait)
                else:
                    time.sleep(10)
                continue

            try:
                print(">> 等待接码平台收到验证码 ...")
                code = sms.poll_code(phone)
                print(f">> 收到验证码：{code}")
            except HaozhuTimeoutError:
                print("   等待验证码超时，拉黑该号码后重试。")
                try:
                    sms.blacklist_phone(phone)
                except Exception:
                    pass
                continue

            status, info = process_account(s, sms, phone, code, invite_code)
            try:
                sms.release_phone(phone)
            except Exception:
                pass

            if status == "ok":
                print_result(status, info)
                total_ok += 1
                token = info.get("token") or ""
                device = s.cookies.get("tr_ref_device") or gen_device()
                session_tokens[phone] = (token, device)
                session_tokens["_last"] = (token, device)  # 供后续核对 todayRewardedCount
            elif status == "limit":
                print("   邀请码已达每日上限（接口返回），切换下一个。")
                print("   限制响应：", info)
                break
            else:
                print("   注册失败：", info)
                # 失败（非上限）换号重试
                continue

    print(f"\n=== 本轮完成，成功注册 {total_ok} 个账号（共 {len(INVITE_CODES)} 个邀请码）===")


def build_invite_queue(session_tokens):
    queue = list(INVITE_CODES)
    if POLL_MODE:
        for rec in load_accounts():
            oc = rec.get("ownInviteCode") or rec.get("inviteCode")
            tok = rec.get("sessionToken")
            if oc and tok and oc not in queue:
                queue.append(oc)
                session_tokens.setdefault(oc, (tok, gen_device()))
    return queue


def count_today_from_file(invite_code):
    """无登录数据时，按本地 JSON + 今日日期估算某指定码今日已用名额。"""
    today = time.strftime("%Y-%m-%d")
    return sum(1 for r in load_accounts()
               if r.get("inviteCode") == invite_code
               and str(r.get("createdAt", "")).startswith(today))


def code_full(s, invite_code, session_tokens, file_count):
    tok, dev = session_tokens.get(invite_code, (None, None))
    if tok:
        today, limit = get_referral_today(tok, dev or gen_device())
        return today >= limit, today, limit
    limit = MAX_PER_DAY
    return file_count >= limit, file_count, limit


def poll_loop(s, sms, session_tokens):
    """多码轮询模式：优先指定码，满后自动切换到已注册账号的邀请码，直至全部耗尽。"""
    total_ok = 0
    queue = build_invite_queue(session_tokens)

    while True:
        candidate = None
        for code in queue:
            fc = count_today_from_file(code)
            full, today, limit = code_full(s, code, session_tokens, fc)
            if not full:
                candidate = (code, today, limit)
                break
        if candidate is None:
            print("\n[所有邀请码今日均已达上限，本轮结束]")
            break

        invite_code, today, limit = candidate
        print(f"\n########## 邀请码 {invite_code} （今日 {today}/{limit}）##########")
        while True:
            full, today, limit = code_full(s, invite_code, session_tokens,
                                           count_today_from_file(invite_code))
            if full:
                print(f"   [该码今日已 {today}/{limit}，达上限，切换下一个]")
                break

            print(f"\n----- 本码第 {today + 1}/{limit} 个账号 -----")
            # 刷新代理 + 检查豪猪余额，余额耗尽则停止全部注册
            apply_proxy(s)
            bal = get_hz_balance(sms)
            if bal is not None and bal <= 0:
                print(">> 豪猪余额不足（0），停止自动注册。")
                raise HaozhuBalanceEmpty

            try:
                phone = sms.get_phone()
                print(f">> 接码平台取号：{phone}")
                if SEND_COOLDOWN:
                    time.sleep(SEND_COOLDOWN)
            except (HaozhuAPIError, HaozhuTimeoutError) as e:
                if hz_insufficient(e):
                    print("   豪猪余额不足，停止注册：", e)
                    raise HaozhuBalanceEmpty
                print("   取号失败：", e)
                break

            sms_res = send_sms(s, phone, invite_code)
            if sms_res.get("code") != 0:
                print("   tokenrhythm 发短信失败：", sms_res)
                try:
                    sms.release_phone(phone)
                except Exception:
                    pass
                # 发送过于频繁：按服务端建议等待后换号重试（不中断整体流程）
                retry = sms_res.get("data", {}).get("retryAfterSeconds") \
                    if isinstance(sms_res.get("data"), dict) else None
                if retry:
                    wait = int(retry) + 3
                    print(f"   等待 {wait}s 后重试 ...")
                    time.sleep(wait)
                else:
                    time.sleep(10)
                continue

            try:
                print(">> 等待接码平台收到验证码 ...")
                code = sms.poll_code(phone)
                print(f">> 收到验证码：{code}")
            except HaozhuTimeoutError:
                print("   等待验证码超时，拉黑该号码后重试。")
                try:
                    sms.blacklist_phone(phone)
                except Exception:
                    pass
                continue

            status, info = process_account(s, sms, phone, code, invite_code)
            try:
                sms.release_phone(phone)
            except Exception:
                pass

            if status == "ok":
                print_result(status, info)
                total_ok += 1
                tok = info.get("token") or ""
                dev = s.cookies.get("tr_ref_device") or gen_device()
                session_tokens[phone] = (tok, dev)
                # 指定码注册后，其自身登录态存入队列，便于后续精确核对
                if invite_code not in session_tokens:
                    session_tokens[invite_code] = (tok, dev)
            elif status == "limit":
                print("   邀请码已达每日上限（接口返回），切换下一个。")
                print("   限制响应：", info)
                break
            else:
                print("   注册失败：", info)
                continue

    print(f"\n=== 本轮完成，成功注册 {total_ok} 个账号 ===")


def print_result(status, info):
    if status != "ok":
        return
    print(f"   手机号      : {info['phone']}")
    print(f"   用户 ID    : {info['userId']}")
    print(f"   API Key    : {info['apiKey']}")
    print(f"   自身邀请码 : {info['ownInvite']}")
    print(f"   模型回复   : {info['reply']}")
    print(f"   可用余额   : {info['balance']} CNY（本次消耗 {info['cost']} CNY）")


def main():
    print("=== 基元律动 自动注册工具 ===")
    mode = "多码轮询" if POLL_MODE else "顺序队列"
    print(f"模式：{mode}；邀请码 {len(INVITE_CODES)} 个指定，每码每日上限：{MAX_PER_DAY}")
    if PROXY_ENABLED:
        print(f"代理：已启用（IPZAN，单 IP 有效期约 {PROXY_LIFETIME}s，到期自动更换）")
    else:
        print("代理：未启用")
    print("策略：豪猪余额 > 0 时持续自动注册，余额耗尽即停止。")

    s = new_session()
    session_tokens = {}

    use_auto = AUTO_PHONE and HZ_USER and HZ_PASS and HZ_SID
    if AUTO_PHONE and not (HZ_USER and HZ_PASS and HZ_SID):
        print("[提示] 未填写 HZ_USER / HZ_PASS / HZ_SID，回退手动模式。")

    if use_auto:
        try:
            hz = HaozhuClient(
                HZ_USER, HZ_PASS, HZ_SID,
                server=HZ_SERVER,
                token_cache_path=HZ_TOKEN_CACHE,
                blacklist_path=HZ_BLACKLIST_CACHE,
            )
            try:
                summary = hz.get_summary()
                print(f"接码平台余额：{summary.get('money')} 元（Token 缓存：{HZ_TOKEN_CACHE}）")
            except Exception as e:
                print("接码平台查询余额失败：", e)
            try:
                if POLL_MODE:
                    poll_loop(s, hz, session_tokens)
                else:
                    auto_loop(s, hz, session_tokens)
            except HaozhuBalanceEmpty:
                print("\n[豪猪余额不足，已停止自动注册]")
        except Exception as e:
            print("接码平台登录/运行异常：", e)
            print("已回退手动模式，请用首个邀请码手动输入。")
            manual_loop(s, INVITE_CODES[0] if INVITE_CODES else "")
    else:
        manual_loop(s, INVITE_CODES[0] if INVITE_CODES else "")

    print(f"账号信息已保存至：{SAVE_FILE}")


if __name__ == "__main__":
    main()
