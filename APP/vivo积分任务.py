"""vivo 积分任务

完成任务：
  业务签到 / SPA 主签到 / 签到提醒 / 任务中心全量（12 身份 × 全部任务，
  逐条动作 + 重读校验）/ EC 浏览任务 / 社区读帖 / 活动帖互动 /
  SDK 行为上报 / 积分商城抽奖·竞猜·活动领奖·待领积分 / 抽奖活动 /
  游戏任务 / 砸金蛋 / 双11抽奖 / 积分答题 / 商城收藏 / 会员中心领券。

环境变量 `vivo_app`
==================
位置参数，用 `#` 分割（顺序固定）：

    openid#vivotoken#imei#oaid#unionid#phone

多账号
======
以下三种写法**都支持**：
1. `@` 分隔：`vivo_app=openid1#tok1@openid2#tok2`
2. 换行分隔：`vivo_app=openid1#tok1\nopenid2#tok2`
   （青龙里配置多个同名环境变量的值，运行时通常以换行拼接 —— 本脚本按换行切分）
3. 青龙新建多个同名环境变量 `vivo_app`（等价于上面的换行拼接）

依赖
====
    pip install requests        # 必需
    pip install pycryptodome    # 可选：积分商城抽奖/兑换的 jvq_param 加密（缺失则跳过抽奖）

用法（青龙）
============
  定时任务 -> 命令：`python3 vivo_app.py`  （本文件无需任何参数）

可调环境变量（选填）
--------------------
  VIVO_GAP      请求最小间隔秒，默认 0.6
  VIVO_PKGS     任务中心遍历的客户端身份，逗号分隔；默认全部 12 个
  VIVO_TASKS    1=跑任务中心全量（默认），0=只跑签到/活动等轻量项

其他信息
========
@auth:  xiaohai
@date:  2026-10-09
@desc:  vivo 积分任务（签到/任务中心/活动/商城/社区）

"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import re
import struct
import sys
import time
import uuid
import zlib
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except Exception:  # pragma: no cover
    print("[FATAL] 缺少 requests，请先 pip install requests")
    raise

ENV_NAME = "vivo_app"
UA_H5 = ("Mozilla/5.0 (Linux; Android 16; V2227A Build/TP1A.220624.014; wv) "
         "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
         "Chrome/120.0.0.0 Mobile Safari/537.36")
UA_WAP = ("Mozilla/5.0 (Linux; Android 16; V2227A) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
UA_SPACE = "VivoSpace/2.1 (Android)_(vivo.com)/13.4.1.5_13415 DeviceType/phone"

POINTH5 = "https://pointh5.vivo.com.cn"
POINTWAP = "https://pointwap.vivo.com.cn"
POINTSDK = "https://pointsdk.vivo.com.cn"
SIGNHOST = "https://sign.vivo.com.cn"
MEMBER = "https://member.vivo.com.cn"
BBS = "https://bbs.vivo.com.cn"
EDEN = "https://eden.vivo.com.cn"
MSHOPACT = "https://mshopact.vivo.com.cn"
SHOP_FBAPI = "https://shop.vivo.com.cn/wap/fbApi"
WUKONGAPI = "https://wukongapi.vivo.com.cn"
POINTACT = "https://pointactivity.vivo.com.cn"
MPC = "https://mpc.vivo.com.cn"

H5_REFERER = "https://zhan.vivo.com.cn/point/wk2207264eeaa8ec"
H5_ORIGIN = "https://zhan.vivo.com.cn"
WX_APPID = "wxeb4a6201a90783f4"
FORUM_SIG_KEY = "vivospaceprivkey=j56Hi8o1xzu6Gp"
SIGN_VERSION = "2"

BIZ_PACKAGE = {
    "appstore": "com.bbk.appstore",
    "gamecenter": "com.vivo.game",
    "vivospace": "com.vivo.space",
    "browser": "com.vivo.browser",
    "videoPlayer": "com.android.VideoPlayer",
    "wallet": "com.vivo.wallet",
    "iTheme": "com.bbk.theme",
    "iMusic": "com.android.bbkmusic",
    "minigame": "com.vivo.minigamecenter",
    "quickapp": "com.quickapp.center",
    "vhome": "com.vivo.vhome",
    "vivop": "",
}
PN_MAP = {
    "wallet": "com.vivo.wallet", "gamecenter": "com.vivo.game",
    "vivospace": "com.vivo.space", "appstore": "com.bbk.appstore",
    "browser": "com.vivo.browser", "videoPlayer": "com.android.VideoPlayer",
    "iTheme": "com.bbk.theme", "iMusic": "com.android.bbkmusic",
    "minigame": "com.vivo.minigamecenter", "quickapp": "com.quickapp.center",
    "vhome": "com.vivo.vhome", "应用商店": "com.bbk.appstore",
    "游戏中心": "com.vivo.game", "vivo官网": "com.vivo.space",
    "浏览器": "com.vivo.browser", "vivo视频": "com.android.VideoPlayer",
    "钱包": "com.vivo.wallet", "主题": "com.bbk.theme",
    "音乐": "com.android.bbkmusic", "小游戏": "com.vivo.minigamecenter",
    "快应用": "com.quickapp.center", "智慧生活": "com.vivo.vhome",
}
ALL_PKGS = ["com.vivo.space"] + [p for p in BIZ_PACKAGE.values() if p]
MAIN_SIGN_PKG = "com.vivo.wallet"

GAP = float(os.environ.get("VIVO_GAP") or "0.6")


def log(*a: Any) -> None:
    print(*a, flush=True)


def now_ms() -> int:
    return int(time.time() * 1000)


# ======================================================================
# 1. 环境变量解析
# ======================================================================
def _split_accounts(raw: str) -> List[str]:
    out: List[str] = []
    for chunk in re.split(r"[\r\n@]+", raw or ""):
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
    return out


def _parse_one(entry: str) -> Optional[Dict[str, str]]:
    """解析单个账号；命名形式(openid=..&..)或位置形式(openid#tok#imei#..)。"""
    if "openid=" in entry or "vivotoken=" in entry or "token=" in entry:
        d: Dict[str, str] = {}
        for kv in re.split(r"[&;]", entry):
            if "=" in kv:
                k, v = kv.split("=", 1)
                d[k.strip().lower()] = v.strip()
        openid = d.get("openid", "")
        token = d.get("vivotoken") or d.get("token") or ""
        if not openid or not token:
            return None
        return {"openid": openid, "vivotoken": token,
                "imei": d.get("imei", ""), "oaid": d.get("oaid", ""),
                "unionid": d.get("unionid", ""), "phone": d.get("phone", "")}
    parts = [p.strip() for p in entry.split("#")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    g = lambda i: parts[i] if i < len(parts) else ""   # noqa: E731
    return {"openid": parts[0], "vivotoken": parts[1], "imei": g(2),
            "oaid": g(3), "unionid": g(4), "phone": g(5)}


def load_accounts() -> List[Dict[str, str]]:
    raw = os.environ.get(ENV_NAME, "") or ""
    # 兼容个别青龙把同名多值用逗号/分号拼接的情形（仅在无 # 且无 @ 时启用）
    accts: List[Dict[str, str]] = []
    seen = set()
    for entry in _split_accounts(raw):
        a = _parse_one(entry)
        if not a:
            log(f"[WARN] 无法解析账号条目：{entry[:24]}…（应为 openid#vivotoken#…）")
            continue
        key = a["openid"]
        if key in seen:
            continue
        seen.add(key)
        accts.append(a)
    return accts


# ======================================================================
# 2. 设备指纹（按 openid 确定性生成，跨次稳定）
# ======================================================================
def _luhn(body14: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body14)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def gen_imei(seed: str) -> str:
    h = hashlib.sha256(seed.encode()).hexdigest()
    tac = "86" + str(int(h[0:6], 16) % 1000000).zfill(6)
    serial = str(int(h[6:18], 16) % 1000000).zfill(6)
    body = (tac + serial)[:14]
    return body + _luhn(body)


def gen_oaid(seed: str) -> str:
    return str(uuid.UUID(hashlib.md5(("oaid:" + seed).encode()).hexdigest()))


# ======================================================================
# 3. wave 签名
# ======================================================================
NET_PREFIX = (
    "R7cF8SoztcIzrECAIiz%2F0buRl2GGnQF4QJwyuINtTLWSIjNI9TsfuvNQTxiq%2BP0yAVt1ZRSyy5As"
    "SfeNGwoa6AmPkwxjpni6quiOuuID1wZbrrj2PvG9%2B069AZLTu%2Fyr0luWfhhKDPGtYpuk4xBZrHvs"
    "bVvgjHGwqY6zIQFFGy6lNVfTvKV%2BMqOVHM8X6Qiphp2ckuQfZd5oqXcQSpUwEC8GnzKSv2XA7QfOG4"
    "kdVwxu9WNAUas0fky1Cq%2F%2Bj%2BttR6TRzWwBFMQZEX02LTQn6YyB6LyftWv%2F0YPlU6Hh38scNS"
    "TeTaOW1j8vMxdGB6bWg1fZNIq3T7t%2FU3W%2FQyz"
)


def _wave_net(s: str) -> int:
    rev = hashlib.md5((NET_PREFIX + s).encode()).hexdigest()[::-1]
    return (int(rev[7:14], 16) + int(rev[14:24], 16)) & 0xFFFFFFFF


def _utf8_only(s: str) -> str:
    raw = s.encode()
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        b = raw[i]
        if (b & 0xF8) == 0xF0 and n - i >= 4:
            i += 4
            continue
        out.append(b)
        i += 1
    return out.decode("utf-8", "replace")


def get_sign(values: List[str]) -> str:
    from urllib.parse import quote
    sb = "".join(":" + str(v) for v in values)
    inner = _wave_net(_utf8_only(sb))
    return quote(f"{SIGN_VERSION}|{inner}", safe="")


def _url_values(url: str) -> List[str]:
    if not url:
        return []
    s = url.split("?", 1)[1] if "?" in url else url
    if not s or "=" not in s:
        return []
    out, seen = [], {}
    from urllib.parse import unquote
    for part in s.split("&"):
        kv = part.split("=")
        k = kv[0]
        v = unquote(kv[1]) if len(kv) > 1 else ""
        if k in seen:
            try:
                out.remove(seen[k])
            except ValueError:
                pass
        else:
            out.append(v)
        seen[k] = v
    return out


def m_value_for_post(param_map: Dict[str, Any], extra_url: str = "") -> str:
    vals = ["" if v is None else str(v) for v in param_map.values()]
    vals.extend(_url_values(extra_url))
    vals.sort()
    return get_sign(vals)


# ======================================================================
# 4. jvq_param 信封
# ======================================================================
class MaodunCipher:
    MAGIC = 203
    PROTOCOL_TYPE = 0x0020
    KEY_VERSION = 0x0001
    ENC_TYPE = 0x41
    KEY_TOKEN = b"com.vivo.point.mall"
    CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    _PEM_BODY = (
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAhYDpLPqfVnPs2zV6IPTBt743XC8vNSiuqaQX"
        "opavStHuTgopj7iuwvPCGae7d1tB0stu6lVIFG/NBcQeOz308+AI5kcdIfA5HNri4vlT0cUCDTs02l/B"
        "EsZRGuk1MhL5ky+ka00JzVXbS4Or6QnMHrX6H/vmrDsbsyiCuO7NZj6zZA3VWov2GO7UaziuMgmrMYcT"
        "1WvRK9P3NMMaA4oZrA5+yhNCrYXFaSOmJGNOpSBS8LMI+aGOsjVyoJdBrhdrwaQuKALQ3jAqNmN+6OdJR"
        "04JIxpCNJUauCiKarWwTOjD7F6UdNSd2N6N/YMIYlA6A6xCC+ot9Y7uD3VsejKcuQIDAQAB")

    def __init__(self):
        import base64
        from Crypto.Cipher import AES, PKCS1_v1_5
        from Crypto.PublicKey import RSA
        self._b64 = base64
        self._AES = AES
        self._PKCS = PKCS1_v1_5
        pem = ("-----BEGIN PUBLIC KEY-----\n"
               + "\n".join(self._PEM_BODY[i:i + 64] for i in range(0, len(self._PEM_BODY), 64))
               + "\n-----END PUBLIC KEY-----")
        self._key = RSA.import_key(pem)
        self.aes_key = "".join(random.choice(self.CHARS) for _ in range(16))
        self.aes_iv = "".join(random.choice(self.CHARS) for _ in range(16))

    def _c(self):
        return self._AES.new(self.aes_key.encode(), self._AES.MODE_CBC, self.aes_iv.encode())

    def encrypt(self, params: Dict[str, Any]) -> str:
        plain = json.dumps(params, separators=(",", ":"), ensure_ascii=False).encode()
        pad = 16 - (len(plain) % 16)
        body = self._c().encrypt(plain + bytes([pad]) * pad)
        rsa_ct = self._PKCS.new(self._key).encrypt(self.aes_key.encode())
        buf = (struct.pack(">h", self.PROTOCOL_TYPE) + struct.pack(">h", self.MAGIC)
               + struct.pack(">h", self.KEY_VERSION) + bytes([self.ENC_TYPE])
               + bytes([len(self.KEY_TOKEN)]) + self.KEY_TOKEN + self.aes_iv.encode()
               + struct.pack(">h", len(rsa_ct)) + rsa_ct)
        crc = struct.pack(">Q", zlib.crc32(buf) & 0xFFFFFFFF) + buf
        blob = struct.pack(">H", len(crc) + 2) + crc + body
        return (self._b64.b64encode(blob).decode()
                .replace("+", "-").replace("/", "_").replace("=", ""))

    def decrypt(self, text: str) -> str:
        t = text.replace("-", "+").replace("_", "/")
        t = "".join(c for c in t if c.isalnum() or c in "+/")
        raw = self._b64.b64decode(t + "=" * (-len(t) % 4))
        tok_len = raw[17]
        total = tok_len + 34
        key_len = struct.unpack(">h", raw[total:total + 2])[0]
        body = raw[total + 2 + key_len:]
        plain = self._c().decrypt(body + b"\x00" * (-len(body) % 16))
        return plain[:-plain[-1]].decode("utf-8", "replace")


# ======================================================================
# 5. 账号会话
# ======================================================================
class Account:
    def __init__(self, raw: Dict[str, str]):
        self.openid = raw["openid"]
        self.vivotoken = raw["vivotoken"]
        self.unionid = raw.get("unionid", "")
        self.phone = raw.get("phone", "")
        self.imei = raw.get("imei") or gen_imei(self.openid)
        self.oaid = raw.get("oaid") or gen_oaid(self.openid)
        self.label = f"{self.openid[:8]}…{self.openid[-4:]}" + (f"({self.phone})" if self.phone else "")

    def mask_token(self) -> str:
        t = self.vivotoken
        return t[:6] + "…" + t[-4:] if len(t) > 12 else t


class VivoApi:
    """一个账号一个实例；统一封装所有可完成任务。"""

    def __init__(self, acct: Account):
        self.acct = acct
        self.s = requests.Session()
        self._last = 0.0
        self._install_cookies()

    # ------------------------------------------------------------ 基础
    def _install_cookies(self) -> None:
        a = self.acct
        dom = ".vivo.com.cn"
        for k, v in (("vvc_imei", a.imei), ("vvc_oaid", a.oaid),
                     ("vvc_openid", a.openid), ("vvc_r", a.vivotoken),
                     ("vivo_account_cookie_iqoo_openid", a.openid),
                     ("vivo_account_cookie_iqoo_vivotoken", a.vivotoken)):
            self.s.cookies.set(k, v, domain=dom)

    def _gap(self) -> None:
        d = time.time() - self._last
        if d < GAP:
            time.sleep(GAP - d)
        self._last = time.time()

    def cookie_header(self, pkg: Optional[str] = "") -> str:
        a = self.acct
        parts = [f"vvc_openid={a.openid}", f"vvc_r={a.vivotoken}"]
        if pkg:
            parts.append(f"vvc_pn={pkg}")
        parts.append(f"vvc_imei={a.imei}")
        return ";".join(parts)

    def _h5_headers(self, pkg: Optional[str] = "", extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {"User-Agent": UA_H5, "Referer": H5_REFERER, "Origin": H5_ORIGIN,
             "Accept": "application/json, text/plain, */*",
             "cookie": self.cookie_header(pkg)}
        if extra:
            h.update(extra)
        return h

    def h5_call(self, host: str, path: str, *, method: str = "GET",
                params: Optional[Dict[str, Any]] = None, pkg: Optional[str] = "",
                extra_cookie: str = "", timeout: int = 20) -> Dict[str, Any]:
        """任务族通用调用（pointh5 / sign / pointsdk / pointwap）。"""
        self._gap()
        url = host.rstrip("/") + path
        url += ("&" if "?" in url else "?") + f"t={now_ms()}"
        ck = self.cookie_header(pkg) + ((";" + extra_cookie) if extra_cookie else "")
        h = {"User-Agent": UA_H5, "Referer": H5_REFERER, "Origin": H5_ORIGIN,
             "Accept": "application/json, text/plain, */*", "cookie": ck}
        payload = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            if method.upper() == "GET":
                r = self.s.get(url, params=payload, headers=h, timeout=timeout)
            else:
                r = self.s.post(url, json=payload,
                                headers={**h, "content-type": "application/json"},
                                timeout=timeout)
            return r.json()
        except Exception as e:                              # noqa: BLE001
            return {"_err": str(e)[:120]}

    @staticmethod
    def ok(res: Any) -> bool:
        if not isinstance(res, dict):
            return False
        if "retcode" in res:
            try:
                return int(res["retcode"]) == 0
            except (TypeError, ValueError):
                return False
        try:
            return int(res.get("code", -1)) in (0, 200)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def sign_ok(res: Any) -> bool:
        """签到达成态：0/200 成功、10002/10003 已签。"""
        if not isinstance(res, dict):
            return False
        return str(res.get("code", res.get("retcode"))) in ("0", "200", "10002", "10003")

    # ============================================================ 用户信息（自检）
    def user_info(self) -> Dict[str, Any]:
        self._gap()
        h = {"content-type": "application/json;charset=UTF-8", "wxAppId": WX_APPID,
             "vivo-token": self.acct.vivotoken}
        try:
            r = self.s.post(MPC + "/api/wx/user/userInfo", headers=h, timeout=20)
            return r.json()
        except Exception as e:                              # noqa: BLE001
            return {"_err": str(e)[:120]}

    # ============================================================ 签到
    def sign_biz_list(self, pkg: str = "com.vivo.space") -> List[Dict[str, Any]]:
        r = self.h5_call(POINTH5, "/api/mall/task/center/new/signBizList.do",
                         params={"noToast": "1"}, pkg=pkg)
        d = r.get("data")
        return d if isinstance(d, list) else []

    def sign_do(self, sys_code: str, pkg: Optional[str] = "_auto_") -> Dict[str, Any]:
        """POST sign.vivo.com.cn/api/sign/dosign.do（sysCode + vvc_pn 成对）。"""
        if pkg == "_auto_":
            pkg = BIZ_PACKAGE.get(sys_code, "")
        return self.h5_call(SIGNHOST, "/api/sign/dosign.do", method="POST",
                            params={"sysCode": sys_code}, pkg=pkg or "")

    def sign_all(self) -> List[Dict[str, Any]]:
        """11 业务端签到。"""
        out = []
        for b in self.sign_biz_list():
            biz, pkg, name = b.get("bizCode"), b.get("packageName"), b.get("bizName")
            if str(b.get("isSign")) == "1":
                out.append({"biz": biz, "name": name, "res": "already"})
                continue
            r = self.sign_do(biz, pkg=pkg or "")
            out.append({"biz": biz, "name": name, "res": "ok" if self.sign_ok(r) else r.get("msg") or r.get("code"),
                        "code": r.get("code")})
        return out

    def sign_main(self) -> Dict[str, Any]:
        """vivo+ SPA 主签到"""
        home = self.h5_call(POINTH5, "/api/sign/home.do", pkg=MAIN_SIGN_PKG)
        d = home.get("data") or {}
        if str(d.get("hasSigned")) in ("1", "True", "true"):
            return {"action": "already", "hasSigned": d.get("hasSigned")}
        r = self.h5_call(POINTH5, "/api/sign/start.do", method="POST",
                         params={"taskId": d.get("taskId")}, pkg=MAIN_SIGN_PKG)
        code = r.get("retcode", r.get("code"))
        return {"action": "signed" if str(code) == "0" else
                ("already" if str(code) == "10002" else "failed"), "code": code}

    def sign_notify(self, on: int = 1) -> Dict[str, Any]:
        return self.h5_call(SIGNHOST, "/api/sign/notify/notify.do", method="POST",
                            params={"notifyFlag": int(on)}, pkg=MAIN_SIGN_PKG)

    # ============================================================ 任务中心
    def _tasks_for_pkg(self, pkg: str) -> Dict[Any, Dict[str, Any]]:
        out: Dict[Any, Dict[str, Any]] = {}
        for t in range(0, 7):
            r = self.h5_call(POINTH5, "/api/task/listByType.do",
                             params={"type": str(t)}, pkg=pkg)
            if str(r.get("retcode")) != "0":
                continue
            d = r.get("data")
            items = d if isinstance(d, list) else ((d or {}).get("list") or (d or {}).get("tasks") or [])
            for x in items:
                if isinstance(x, dict) and x.get("id") is not None:
                    x = dict(x)
                    x["_pkg"] = pkg
                    out[x["id"]] = x
        return out

    def sign_biz_names(self) -> Dict[str, str]:
        return {b.get("bizName"): b.get("bizCode") for b in self.sign_biz_list()
                if b.get("bizName")}

    def _biz_for_name(self, name: str, mapping: Dict[str, str]) -> Optional[str]:
        for bn, biz in mapping.items():
            if bn and bn in str(name):
                return biz
        return "vivop" if "签到" in str(name) else None

    def receive_point(self, task: Dict[str, Any]) -> Dict[str, Any]:
        p = {"taskId": task.get("id")}
        for k in ("type", "subType", "taskSource"):
            if task.get(k) is not None:
                p[k] = task[k]
        return self.h5_call(POINTH5, "/api/task/receivePoint.do", method="POST", params=p)

    def ec_receive_point(self, task: Dict[str, Any]) -> Dict[str, Any]:
        p = {"noLoading": True, "from": "EC", "taskId": task.get("id"),
             "businessCode": task.get("businessCode") or "vivop"}
        for k in ("taskSource", "type", "subType"):
            if task.get(k) is not None:
                p[k] = task[k]
        return self.h5_call(POINTH5, "/api/task/ec/receivePoint.do", method="POST", params=p)

    def task_wish(self, tid: Any) -> Dict[str, Any]:
        return self.h5_call(POINTSDK, "/web/task/wish", method="POST",
                            params={"taskId": tid, "noLoading": True, "noToast": True})

    def complete_web_task(self, key_id: Any) -> Dict[str, Any]:
        return self.h5_call(POINTH5, "/api/task/completeWebTask.do", method="POST",
                            params={"taskKeyId": str(key_id)})

    def vivop_complete(self, key_id: Any) -> Dict[str, Any]:
        return self.h5_call(POINTH5, "/api/task/vivop/complete.do", method="POST",
                            params={"keyId": key_id, "businessCode": "vivop"})

    # ------ 社区读帖 ------
    def _community_body(self) -> Dict[str, str]:
        ts = str(now_ms())
        nonce = hashlib.md5((self.acct.imei + ts + str(random.randint(0, 999))).encode()).hexdigest()
        return {"sig": get_sign(sorted([ts, nonce, "j56Hi8o1xzu6Gp"])), "timestamp": ts, "nonce": nonce}

    def _community_post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self._gap()
        b = self._community_body()
        b.update(body)
        try:
            r = self.s.post(BBS + path, data=json.dumps(b, ensure_ascii=False).encode(),
                            headers={"User-Agent": UA_SPACE,
                                     "Content-Type": "application/json;charset=utf-8"},
                            timeout=20)
            return r.json()
        except Exception as e:                              # noqa: BLE001
            return {"_err": str(e)[:120]}

    def community_hot_threads(self, limit: int = 8) -> List[str]:
        tids: List[str] = []

        def grab(blob: str) -> None:
            for a, b in re.findall(r"(?:/newbbs/thread/|/thread/)(\d+)|[?&]tid=(\d+)", str(blob)):
                tids.append(a or b)
        for pn in (1, 2, 3):
            r = self._eden_post("client/bbk/activity/index", {"pageNum": str(pn), "cfrom": "652"})
            acts = (((r.get("value") or {}).get("activities")) or [])
            if not acts:
                break
            for a in acts:
                grab(str(a.get("url") or "") + str(a.get("cardActivity") or ""))
        if not tids:
            try:
                r2 = requests.get(BBS + "/recommend/homeRecommend",
                                  headers={"User-Agent": UA_H5}, timeout=15)
                try:
                    for s in ((r2.json().get("data") or {}).get("recommendSections") or []):
                        if s.get("tid") is not None:
                            tids.append(str(s["tid"]))
                except Exception:                            # noqa: BLE001
                    tids = [a or b for a, b in re.findall(r"(?:/thread/(\d+)|tid=(\d+))", r2.text)]
            except Exception:                                # noqa: BLE001
                pass
        seen, out = set(), []
        for t in tids:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out[:limit]

    def community_read(self, count: int = 3) -> Dict[str, Any]:
        tids = self.community_hot_threads(limit=max(count, 5))
        if not tids:
            return {"ok": False, "msg": "无帖子 id（不编造）"}
        read = []
        for tid in tids[:count]:
            r = self._community_post("/api/community/client/thread/detail", {"tid": str(tid)})
            read.append({"tid": tid, "code": r.get("code")})
            time.sleep(0.8)
        return {"ok": True, "read": read}

    def community_engage(self, limit: int = 30) -> Dict[str, Any]:
        """活动帖协议侧参与：点赞 + 收藏 + 阅读计数 """
        tids = self.community_hot_threads(limit=limit)
        okc = ("0", "16002", "16016")
        res = []
        for tid in tids:
            rec = {"tid": tid}
            for act, path, extra in (("like", "/api/community/client/thread/like", {"type": 1}),
                                     ("favorite", "/api/community/client/thread/favorite", {"type": 1}),
                                     ("read", "/api/community/client/thread/incrReadNum", {})):
                body = {"tid": str(tid)}
                body.update(extra)
                r = self._community_post(path, body)
                rec[act] = r.get("code")
                _ = okc
            res.append(rec)
            time.sleep(0.5)
        return {"total": len(res), "results": res}

    def complete_task(self, t: Dict[str, Any], mapping: Dict[str, str]) -> Dict[str, Any]:
        tid, pkg, name = t.get("id"), t.get("_pkg") or "com.vivo.space", str(t.get("name") or "")
        rec = {"id": tid, "pkg": pkg, "name": name, "result": None}
        if t.get("taskComplete") is True:
            rec["result"] = "already"
            return rec
        # ① 阅读类
        if "阅读" in name and "帖子" in name:
            m = re.search(r"(\d+)", name)
            rec["read"] = self.community_read(int(m.group(1)) if m else 3)
        # ② 签到类
        if "签到" in name:
            biz = self._biz_for_name(name, mapping)
            if biz:
                rec["sign"] = self.sign_do(biz)
        # ③ 无条件点击上报
        rec["wish"] = self.task_wish(tid)
        # ④ 网页任务
        if t.get("keyId"):
            rec["web"] = self.complete_web_task(t["keyId"])
            rec["vivop"] = self.vivop_complete(t["keyId"])
        # ⑤ 领奖（两条口都试）
        rec["ec_recv"] = self.ec_receive_point(t)
        if not self.ok(rec["ec_recv"]):
            rec["recv"] = self.receive_point(t)
        after = self._tasks_for_pkg(pkg).get(tid) or {}
        rec["after"] = {"completedTimes": after.get("completedTimes"),
                        "taskComplete": after.get("taskComplete")}
        if after.get("taskComplete") is True:
            rec["result"] = "completed"
        elif after.get("completedTimes") != t.get("completedTimes"):
            rec["result"] = "progressed"
        else:
            rec["result"] = "no_change"
        return rec

    def complete_all_tasks(self, pkgs: List[str]) -> Dict[str, Any]:
        mapping = self.sign_biz_names()
        tally: Dict[str, int] = {}
        for pkg in pkgs:
            for _tid, t in self._tasks_for_pkg(pkg).items():
                r = self.complete_task(t, mapping)
                tally[r["result"]] = tally.get(r["result"], 0) + 1
        return tally

    # ============================================================ SDK 行为上报
    def sdk_report_all(self, count: int = 5) -> Dict[str, Any]:
        total = 0
        uploaded = 0
        for pkg in dict.fromkeys(ALL_PKGS):
            cfg = self.h5_call(POINTSDK, "/sdk/config/action/list", method="POST",
                               params={"pkgName": pkg})
            acts = ((cfg.get("data") or {}).get("actions") or [])
            for a in acts:
                aid = a.get("actionId")
                if not aid:
                    continue
                total += 1
                up = self.h5_call(POINTSDK, "/sdk/action/upload", method="POST",
                                  params={"openid": self.acct.openid, "token": self.acct.vivotoken,
                                          "pkgName": pkg, "actionId": aid, "count": str(count),
                                          "notifyPattern": "1"})
                if self.ok(up):
                    uploaded += 1
                self.h5_call(POINTSDK, "/sdk/task/receive/point", method="POST",
                             params={"openid": self.acct.openid, "token": self.acct.vivotoken,
                                     "pkgName": pkg, "actionId": aid, "notifyPattern": "1"})
        return {"actions": total, "uploaded": uploaded}

    # ============================================================ 积分商城
    def _pm_headers(self, json_body: bool = False) -> Dict[str, str]:
        h = {"User-Agent": UA_H5, "Referer": POINTH5 + "/", "Origin": POINTH5}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _pm_get(self, path: str, **params) -> Dict[str, Any]:
        self._gap()
        params.setdefault("noToast", 1)
        try:
            return self.s.get(POINTH5 + path, params=params, headers=self._pm_headers(),
                              timeout=25).json()
        except Exception:                                   # noqa: BLE001
            return {}

    def _pm_post(self, path: str, **params) -> Dict[str, Any]:
        self._gap()
        params.pop("noToast", None)
        try:
            return self.s.post(POINTH5 + path, params={"pts_sys_t": now_ms()},
                               json=params, headers=self._pm_headers(True), timeout=25).json()
        except Exception:                                   # noqa: BLE001
            return {}

    def _pm_enc(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            c = MaodunCipher()
        except Exception as e:                              # noqa: BLE001
            return {"retcode": -2, "message": "缺 pycryptodome，跳过加密接口：" + str(e)[:60]}
        self._gap()
        body = {"jvq_param": c.encrypt(params), "pts_sys_ver": "20260921_v6.1.6"}
        try:
            d = self.s.post(POINTH5 + path, params={"pts_sys_t": now_ms()}, json=body,
                            headers=self._pm_headers(True), timeout=25).json()
        except Exception as e:                              # noqa: BLE001
            return {"retcode": -1, "message": str(e)[:120]}
        if isinstance(d, dict) and d.get("jvq_response"):
            try:
                return json.loads(c.decrypt(d["jvq_response"]))
            except Exception as e:                          # noqa: BLE001
                return {"retcode": -1, "message": "decrypt:" + str(e)[:80]}
        return d if isinstance(d, dict) else {}

    def pm_points(self) -> int:
        return int((self._pm_get("/api/mall/v5/home/header.do").get("data") or {}).get("points") or 0)

    def pm_categories(self) -> List[Dict[str, Any]]:
        out = (self._pm_get("/api/mall/v5/home/categories.do").get("data") or {}).get("categories") or []
        ec = (self._pm_get("/api/mall/v5/ec/categories.do").get("data") or {}).get("categories") or []
        seen, merged = set(), []
        for c in list(out) + list(ec):
            if c.get("id") not in seen:
                seen.add(c["id"])
                merged.append(c)
        return merged

    def pm_items(self, cid: int) -> List[Dict[str, Any]]:
        return (self._pm_get("/api/mall/v5/home/items.do", categoryId=cid).get("data") or {}).get("items") or []

    def pm_draw(self) -> Dict[str, Any]:
        budget = self.pm_points()
        results = []
        for cat in self.pm_categories():
            cid = cat.get("id")
            ch = str(cat.get("channel") or "point").lower()
            ch2, src = ("point", 0) if ch == "point" else ("EC", 4)
            for it in self.pm_items(cid):
                if it.get("playMethod") != 1:
                    continue
                price = it.get("finalPoints")
                if price is None:
                    price = it.get("price")
                if price is None or price > budget:
                    continue
                store = it.get("availStock")
                if store is not None and store <= 0:
                    continue
                r = self._pm_enc("/api/mall/item/draw.do",
                                 {"itemId": it.get("id"), "categoryId": cid,
                                  "acceptError": True, "noLoading": True, "scene": "",
                                  "ticket": "", "constId": "", "channel": ch2, "source": src})
                won = bool(isinstance(r.get("data"), dict)
                           and (r["data"].get("orderId") or r["data"].get("compensateGift")))
                results.append({"itemId": it.get("id"), "name": it.get("name"),
                                "retcode": r.get("retcode"), "won": won})
                budget -= int(price or 0)
                if budget <= 0:
                    break
        return {"pointsBefore": budget, "results": results, "pointsAfter": self.pm_points()}

    def pm_guess(self) -> Dict[str, Any]:
        ga = (self._pm_get("/api/nmall/home/explicit/list.do").get("data") or {}).get("guessActivity") or {}
        if not ga.get("id"):
            return {"skip": "无竞猜活动"}
        if ga.get("isChipIn") or (ga.get("currentTimes") or 0) >= (ga.get("limitTimes") or 0):
            return {"skip": "本期已下注"}
        opts = ga.get("options") or []
        if not opts:
            return {"skip": "无选项"}
        r = self._pm_post("/api/activity/guess/chipIn.do", activityId=ga["id"],
                          points=ga.get("minChip") or 5, optionCode=opts[0].get("code"),
                          scene="", ticket="", constId="")
        return {"activity": ga.get("name"), "resp": r.get("retcode")}

    def pm_events(self) -> Dict[str, Any]:
        """活动任务领奖"""
        evs = (self._pm_get("/api/mall/task/center/head.do").get("data") or {}).get("mallEvens") or []
        done = []
        for e in evs:
            key = e.get("eventKey")
            if not key:
                continue
            r = self._pm_post("/api/mall/event/receive.do", eventKey=key, _t=now_ms())
            done.append({"name": e.get("name"), "retcode": r.get("retcode"),
                         "got": (r.get("data") or {}).get("receivePoint")})
        return {"total": len(evs), "results": done}

    def pm_claim(self) -> Dict[str, Any]:
        """首页福利 + 未到账积分 + 待领积分。"""
        out: Dict[str, Any] = {}
        wf = (self._pm_get("/api/mall/v5/home/dynamic.do").get("data") or {}).get("welfare") or {}
        if wf.get("id"):
            out["welfare"] = self._pm_post("/api/mall/v5/home/receiveWelfare.do", welfareId=wf["id"]).get("retcode")
        up = (self._pm_get("/api/task/unrecvPoint.do").get("data") or {})
        if up.get("unrecvPoint"):
            out["tz_grant"] = self._pm_post("/api/mall/tz/grant.do").get("retcode")
        return out

    # ============================================================ 抽奖活动 / 游戏 / 悟空活动
    def lottery_all(self) -> Dict[str, Any]:
        acts = (self.h5_call(POINTH5, "/api/lottery/list.do", params={}).get("data") or {}).get("activities") or []
        now = now_ms()
        n = 0
        for a in acts:
            if not (int(a.get("endTime") or 0) > now >= int(a.get("startTime") or 0)):
                continue
            aid = a.get("id")
            det = (self.h5_call(POINTH5, "/api/lottery/detail.do", params={"actId": aid}).get("data") or {})
            st = (self.h5_call(POINTH5, "/api/lottery/userStats.do", params={"actId": aid}).get("data") or {}) \
                if det.get("login") else {}
            args = {k: (det.get(k) or st.get(k)) for k in ("scene", "ticket", "constId")}
            if any(v is None for v in args.values()):
                continue                                    # 缺参数不猜
            r = self.h5_call(POINTH5, "/api/lottery/draw.do", method="POST",
                             params={"actId": aid, **args})
            if str(r.get("retcode")) == "0":
                n += 1
        return {"drawn": n}

    def game_tasks(self) -> Dict[str, Any]:
        rewarded = 0
        for pm in (0, 1, 2):
            r = self.h5_call(POINTH5, "/api/gameact/gameList.do", method="POST",
                             params={"playMethod": pm})
            for g in ((r.get("data") or {}).get("games") or []):
                key = {0: "openReward", 1: "downloadReward", 2: "appointmentReward"}.get(pm)
                rw = (g.get(key) or {}) if key else {}
                if str(rw.get("status")) != "1":
                    continue
                rr = self.h5_call(POINTH5, "/api/gameact/getReward.do", method="POST",
                                  params={"noLoading": True, "gameCfgId": g.get("id"),
                                          "token": g.get("token"), "pkgName": g.get("pkgName"),
                                          "objective": rw.get("objective"), "cookie": self.acct.imei,
                                          "scene": g.get("scene"), "ticket": g.get("ticket"),
                                          "constId": g.get("constId")})
                if str(rr.get("retcode")) == "0":
                    rewarded += 1
        return {"rewarded": rewarded}

    def _wk(self, path: str, *, method: str = "GET", body: Optional[Dict[str, Any]] = None,
            params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """wukongapi 调用（不带 Origin，wukongapi 对跨域 Origin 返回空）。"""
        self._gap()
        h = {"User-Agent": UA_SPACE, "Referer": H5_REFERER}
        url = WUKONGAPI + path
        try:
            if method.upper() == "GET":
                (params or {}).setdefault("t", now_ms())
                return self.s.get(url, params=params, headers=h, timeout=25).json()
            h["Content-Type"] = "application/json"
            return self.s.post(url, params={"t": now_ms()}, json=body or {}, headers=h, timeout=25).json()
        except Exception as e:                              # noqa: BLE001
            return {"_err": str(e)[:120]}

    def golden_egg(self) -> Dict[str, Any]:
        """砸金蛋（每日免费 1 次）。"""
        aid, site = "1024744348656337152", "wk241209756168f3"
        av = (self._wk("/wukongapc/lottery/user/available/query",
                       params={"activityId": aid, "siteId": site}).get("data") or {})
        if int(av.get("freeAvaCnt") or 0) <= 0:
            return {"skip": "无免费次数"}
        r = self._wk("/wukongapc/lottery/draw", method="POST", body={
            "activityId": aid, "topicId": site, "siteId": site,
            "extend": {"hostUrl": H5_REFERER + "&h5JumpBackFlag=1",
                       "locationAuthorizedDTO": {"hasUserLocation": False, "province": "", "city": ""}}})
        return {"code": r.get("code"), "reward": ((r.get("data") or {}).get("rewardName"))}

    def double11(self) -> Dict[str, Any]:
        """26年双11会员会场预热抽奖（每日免费 2 次）。"""
        aid, site = "1030298797554535360", "wk2609233beab7e8"
        drew, total = 0, 0
        for _ in range(5):
            av = (self._wk("/wukongapc/lottery/user/available/query",
                           params={"activityId": aid, "groupId": 1}).get("data") or {})
            if int(av.get("freeAvaCnt") or 0) <= 0:
                break
            r = self._wk("/wukongapc/lottery/draw", method="POST", body={
                "activityId": aid, "topicId": site, "siteId": site,
                "extend": {"hostUrl": H5_REFERER + "&h5JumpBackFlag=1",
                           "locationAuthorizedDTO": {"hasUserLocation": False,
                                                     "province": "", "city": ""}}})
            name = str(((r.get("data") or {}).get("rewardName")) or "")
            m = re.search(r"(\d+)", name)
            if m and "积分" in name:
                total += int(m.group(1))
            drew += 1
            if r.get("code") != 0:
                break
        return {"drawn": drew, "points": total}

    def question(self) -> Dict[str, Any]:
        """积分答题"""
        aid, site = "703318389396865152", "wk240410b3206fb4"
        secret = ("OnjzJBzkMzMviJDe3FlZo4cnHOiZZDZswxq/wBfcpKLvAOp70yTcwXrRQm+ffp/"
                  "ZTsdtROyC0VitlgF0bkY9tg==")
        kb = {"以下属于圆舞曲的是": "《蓝色多瑙河》",
              "下列不属于旅游主题公园的是？": "长江三峡",
              "名画《清明上河图》描绘的是当年何处近郊清明时节社会各阶层的生活景象": "汴京",
              "一天中气温最低出现在": "日出前后",
              "中国的四大菜系不包括哪一个？": "湘菜"}
        d0 = self._wk("/wukongapc/question/user/start", method="POST",
                      body={"activityId": aid, "siteId": site})
        tok = (d0.get("data") or {}).get("answerToken")
        if not tok:
            return {"err": d0.get("msg") or "start 无 answerToken"}
        right, pts = 0, 0
        for _ in range(10):
            q = (self._wk("/wukongapc/question/user/question/pull",
                          params={"activityId": aid, "answerToken": tok, "siteId": site}).get("data") or {})
            qid = q.get("questionId")
            if not qid:
                break
            content = str(q.get("content") or "")
            ans = None
            if content in kb:
                for o in (q.get("options") or []):
                    if str(o.get("value")) == kb[content]:
                        ans = o.get("key")
            ans = ans or "A"
            st = (self._wk("/wukongapc/activity/config/sign/token",
                           params={"activityId": aid, "apiName": "submitAnswer", "siteId": site}).get("data") or "")
            fs = {"activityId": aid, "questionId": qid, "apiName": "submitAnswer",
                  "userAnswer": ans, "answerToken": tok, "aladdinToken": st, "siteId": site}
            plain = "".join(f"{k}{v}" for k, v in sorted(fs.items()))
            sign = hmac.new(secret.encode(), plain.encode(), hashlib.sha1).hexdigest()
            aladdin = f"apiName=submitAnswer&signature={sign}&aladdinToken={st}"
            self._gap()
            try:
                r = self.s.post(WUKONGAPI + "/wukongapc/question/user/submit",
                                params={"t": now_ms()},
                                json={"activityId": aid, "questionId": qid, "userAnswer": ans,
                                      "answerToken": tok, "siteId": site},
                                headers={"User-Agent": UA_SPACE, "Content-Type": "application/json",
                                         "Referer": H5_REFERER, "aladdin_sign": aladdin},
                                timeout=25).json()
            except Exception as e:                          # noqa: BLE001
                return {"err": str(e)[:100]}
            if str(r.get("code")) != "0":
                return {"answered_right": right, "err": r.get("msg")}
            dd = r.get("data") or {}
            if dd.get("rightFlag"):
                right += 1
                pts += int(dd.get("gotPoints") or 0)
            if (dd.get("remainQuesNum") or 0) <= 0:
                break
            time.sleep(1.0)
        return {"answered_right": right, "points": pts}

    def powerball(self) -> Dict[str, Any]:
        """彩票下注"""
        h = {"User-Agent": UA_SPACE, "Referer": "https://zhan.vivo.com.cn/point/wk2305190f212481"}
        try:
            self._gap()
            r = self.s.get(POINTACT + "/api/powerball/baseInfo.do", params={"activityId": 26},
                           headers=h, timeout=25).json()
        except Exception as e:                              # noqa: BLE001
            return {"err": str(e)[:100]}
        return {"isLogin": ((r.get("data") or {}).get("isLogin")), "note": "下注需顶象 ticket，跳过"}

    # ============================================================ EC 浏览任务
    def ec_web_tasks(self) -> Dict[str, Any]:
        done, earned = 0, 0
        for bc, pn in PN_MAP.items():
            if cn_name(bc):
                continue                                    # 只按枚举码各跑一次
            tasks = self._ec_list(pn)
            for t in tasks:
                if t.get("webTask") != 1:
                    continue
                if int(t.get("completedTimes") or 0) >= int(t.get("totalTime") or 1):
                    continue
                c = self.h5_call(POINTH5, "/api/task/completeWebTask.do", method="POST",
                                 params={"taskKeyId": str(t.get("keyId") or t.get("taskKeyId"))})
                if str(c.get("retcode")) == "0":
                    body = {"noLoading": True, "from": "EC", "taskId": t.get("id"), "businessCode": bc}
                    for k in ("taskSource", "type", "subType"):
                        if t.get(k) is not None:
                            body[k] = t[k]
                    rv = self.h5_call(POINTH5, "/api/task/ec/receivePoint.do", method="POST", params=body)
                    earned += int((rv.get("data") or {}).get("rewardPoints") or 0)
                    done += 1
        return {"completed": done, "earned": earned}

    def _ec_list(self, pn: str) -> List[Dict[str, Any]]:
        self._gap()
        try:
            r = self.s.get(POINTWAP + "/api/task/ec/taskList.do",
                           params={"t": now_ms(), "city": "", "from": "EC", "type": 0,
                                   "businessCode": "", "tasks": ""},
                           headers={"User-Agent": UA_H5, "Referer": H5_ORIGIN + "/",
                                    "cookie": self.cookie_header(pn)}, timeout=20)
            return (r.json().get("data") or {}).get("tasks") or []
        except Exception:                                   # noqa: BLE001
            return []

    # ============================================================ 商城收藏
    def mall_favorite(self) -> Dict[str, Any]:
        self._gap()
        try:
            cfg = self.s.get(MSHOPACT + "/tool/config", headers={"User-Agent": UA_WAP}, timeout=15).json()
            tc = json.loads((cfg.get("data") or {}).get("tempCookies") or "{}")
            if tc.get("cookieId"):
                self.s.cookies.set("vivo_fe_vftcookid", tc["cookieId"], domain=".vivo.com.cn")
            if tc.get("fakeSessionId"):
                self.s.cookies.set("vivo_fe_vftsessionid", tc["fakeSessionId"], domain=".vivo.com.cn")
        except Exception as e:                              # noqa: BLE001
            return {"err": "商城会话失败:" + str(e)[:80]}
        pairs = self._shop_pairs()
        if not pairs:
            return {"err": "无商品对"}
        spu, sku = pairs[0]
        tok = ""
        try:
            tok = str((self.s.get(SHOP_FBAPI + "/v1/my/token",
                                  headers={"User-Agent": UA_WAP}, timeout=15).json()).get("data") or "")
        except Exception:                                   # noqa: BLE001
            pass
        self._gap()
        try:
            r = self.s.post(SHOP_FBAPI + "/v2/my/favorite/add",
                            params={"skuId": str(sku), "spuIds": str(spu), "token": tok},
                            headers={"User-Agent": UA_WAP,
                                     "Referer": "https://shop.vivo.com.cn/wap/index.html"},
                            timeout=15).json()
        except Exception as e:                              # noqa: BLE001
            return {"err": str(e)[:80]}
        return {"spuId": spu, "skuId": sku, "code": r.get("code"),
                "pointsReward": (r.get("data") or {}).get("pointsReward")}

    def _shop_pairs(self) -> List[Tuple[str, str]]:
        self._gap()
        try:
            r = self.s.post(EDEN + "/v3/home/index",
                            data=self._eden_signed({"pageNum": "1", "cfrom": "652"}),
                            headers={"User-Agent": UA_SPACE,
                                     "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
                            timeout=20)
            txt = json.dumps(r.json(), ensure_ascii=False)
        except Exception:                                   # noqa: BLE001
            return []
        seen, pairs = set(), []
        for m in re.finditer(r"/wap/product/(\d+)\?skuId=(\d+)", txt):
            p = (m.group(1), m.group(2))
            if p not in seen:
                seen.add(p)
                pairs.append(p)
        return pairs[:20]

    # ============================================================ 会员中心
    def member_claim(self) -> Dict[str, Any]:
        home = self._member("/home/page/v5/queryInfo.do", {})
        d = home.get("data") or {}
        items = []
        for zc in ((d.get("zoneInfo") or {}).get("zoneContentInfoList") or []):
            for c in (zc.get("zoneCommodityInfoList") or []):
                if c.get("userStatus") == 0:
                    items.append(c)
        done = []
        for it in items:
            if it.get("receiveIds"):
                r = self._member("/benefit/v5/receiveCoupon.do",
                                 {"receiveIds": ",".join(str(i) for i in
                                                         (it["receiveIds"] if isinstance(it["receiveIds"], (list, tuple)) else [it["receiveIds"]]))})
            elif it.get("benefitId"):
                r = self._member("/benefit/v5/receiveFlashBenefit.do", {"benefitId": it["benefitId"]})
            else:
                r = self._member_json("/benefit/receiveByRisk.do",
                                      {"benefitId": it.get("id"), "version": "3.8"})
            done.append({"name": it.get("name"), "retcode": r.get("retcode")})
        return {"claimable": len(items), "results": done}

    def _member(self, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        self._gap()
        h = {"User-Agent": UA_SPACE, "Referer": MEMBER + "/wap/index", "Origin": MEMBER,
             "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}
        try:
            return self.s.post(MEMBER + "/api" + path, data=data, headers=h, timeout=20).json()
        except Exception as e:                              # noqa: BLE001
            return {"_err": str(e)[:100]}

    def _member_json(self, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        self._gap()
        h = {"User-Agent": UA_SPACE, "Referer": MEMBER + "/wap/index",
             "Content-Type": "application/json", "channel": "1",
             "vivo_member_source_type": "VIVO_PLUS_MINI_APP",
             "mina_openid": self.acct.openid,
             "mina_vivotoken": self.acct.vivotoken}
        try:
            return self.s.post(MEMBER + "/api" + path, json=data, headers=h, timeout=20).json()
        except Exception as e:                              # noqa: BLE001
            return {"_err": str(e)[:100]}

    # ============================================================ eden 公共
    def _eden_signed(self, params: Dict[str, Any]) -> Dict[str, str]:
        p = dict(params)
        p.update(self._base_params())
        p["sign"] = m_value_for_post(p, FORUM_SIG_KEY)
        return p

    def _base_params(self) -> Dict[str, Any]:
        return {"imei": self.acct.imei, "oaid": self.acct.oaid, "model": "V2227A",
                "av": "33", "an": "13", "pkgName": "com.vivo.space", "brand": "vivo",
                "density": "3.0", "screensize": "1080_2400", "nt": "wifi",
                "app_ver": "13415", "pictype": "webp", "elapsedtime": str(now_ms() % (1 << 31)),
                "gitId": "b80d0b7c310fcfe9e274db81863eba4b33a2b503"}

    def _eden_post(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._gap()
        try:
            r = self.s.post(EDEN + "/" + path, data=self._eden_signed(params),
                            headers={"User-Agent": UA_SPACE,
                                     "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
                            timeout=20)
            return r.json()
        except Exception:                                   # noqa: BLE001
            return {}


def cn_name(code: str) -> bool:
    """判断是否为中文业务名"""
    return any("一" <= ch <= "鿿" for ch in code)


# ======================================================================
# 6. 单账号主流程
# ======================================================================
def run_account(acct: Account, do_tasks: bool, pkgs: List[str]) -> None:
    log("=" * 62)
    log(f"账号 {acct.label}  imei={acct.imei}")
    log("=" * 62)
    api = VivoApi(acct)

    ui = api.user_info()
    if str(ui.get("code")) == "31140003":
        log("[登录态] mpc 小程序网关不适用（passport 会话正常情况）；任务后端以 task/list 为准")
    elif str(ui.get("code")) in ("0", "200"):
        d = ui.get("data") or {}
        log(f"[登录态] 等级={d.get('levelName')} 积分={d.get('points')} 手机={d.get('phone')}")
    else:
        log(f"[登录态] userInfo 返回：{str(ui)[:120]}")

    log(f"[签到] 业务端：{api.sign_all()}")
    log(f"[签到] SPA主签到：{api.sign_main()}")
    try:
        api.sign_notify(1)
    except Exception:                                       # noqa: BLE001
        pass

    if do_tasks:
        tally = api.complete_all_tasks(pkgs)
        log(f"[任务中心] {tally}")
        log(f"[SDK行为上报] {api.sdk_report_all()}")
        log(f"[EC浏览任务] {api.ec_web_tasks()}")
        log(f"[社区读帖] {api.community_read(3)}")
        log(f"[活动帖互动] {api.community_engage(30)}")

    log(f"[商城抽奖] {api.pm_draw()}")
    log(f"[商城竞猜] {api.pm_guess()}")
    log(f"[商城领奖] {api.pm_events()} {api.pm_claim()}")
    log(f"[抽奖活动] {api.lottery_all()}")
    log(f"[游戏任务] {api.game_tasks()}")
    log(f"[砸金蛋] {api.golden_egg()}")
    log(f"[双11抽奖] {api.double11()}")
    log(f"[积分答题] {api.question()}")
    log(f"[商城收藏] {api.mall_favorite()}")
    log(f"[会员中心] {api.member_claim()}")
    log(f"[彩票] {api.powerball()}")
    log(f"[完成] {acct.label}\n")


# ======================================================================
# 7. 入口
# ======================================================================
def main() -> int:
    accounts = load_accounts()
    if not accounts:
        log(f"[FATAL] 环境变量 ${ENV_NAME} 为空或格式错误。")
        log(f"        格式：{ENV_NAME}=openid#vivotoken#imei#oaid#unionid#phone")
        log(f"        多账号：用 @ 或换行分隔；或在青龙建多个同名环境变量。")
        return 1

    do_tasks = os.environ.get("VIVO_TASKS", "1") != "0"
    pkgs_env = os.environ.get("VIVO_PKGS", "").strip()
    pkgs = [p.strip() for p in pkgs_env.split(",") if p.strip()] if pkgs_env else ALL_PKGS

    log(f"vivo 任务 | 账号 {len(accounts)} 个 | 请求间隔 {GAP}s | "
        f"任务中心={'开' if do_tasks else '关'} | 身份 {len(pkgs)} 个")
    for i, raw in enumerate(accounts, 1):
        log(f"  #{i} {raw['openid'][:8]}…  token={raw['vivotoken'][:6]}…")
    log("")

    for i, raw in enumerate(accounts, 1):
        log(f">>> [{i}/{len(accounts)}]")
        try:
            run_account(Account(raw), do_tasks, pkgs)
        except Exception as e:                              # noqa: BLE001
            log(f"[ERROR] 账号 {raw['openid'][:8]}… 异常：{type(e).__name__}: {e}\n")
            continue
    log("全部账号处理完毕。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
