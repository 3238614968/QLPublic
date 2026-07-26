"""
new Env('ZEEHO 电智行');
cron: 0 8 * * *

ZEEHO 极核电动车 青龙面板自动化脚本

完成任务：
- 签到
- 发帖
- 评论
- 点赞
- 分享

@ZEEHO_TOKEN 必填，一个或多个 token，多账号用 @ 或换行分隔
支持格式：
 bearer xxx                         纯 access_token
 bearer xxx|refresh_token|expires_at 带 refresh
 {"access_token":"...","refresh_token":"..."}  JSON

环境变量：
  ZEEHO_DEBUG    可选，设为 1/true 开启调试日志

依赖：requests

@time 2026-07-26
@author xiaohai
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import sys
import time
import urllib.parse
from datetime import date, timedelta
from typing import Any, Mapping

import requests

# ============================================================
# 配置常量（来自 zeeho_auto/config.py）
# ============================================================
APPID = "S7qPWPU1"
APPSECRET = "c5e0da7f4da28df805694ec3dd1fc6792e9df99d"
BASE_URL = "https://tapi.zeehoev.com/"
H5_BASE_URL = "https://h5.zeehoev.com/"
CM_URL = BASE_URL + "v1.0/mine/cfmotoservermine/"
CO_URL = BASE_URL + "v1.0/social/cfmotoserversocial/"
SUCCESS_CODE = 10000
BASIC_AUTH = "Basic bWNrOjEyMzQ1Ng=="

USER_AGENT = (
    "okhttp/4.9.3 "
    "ZEEHO/2.6.23 (Android 16; Build/TQ3A.230805.001)"
)
X_APP_INFO = (
    "appVersion=2.6.23&platform=android&systemVersion=16"
    "&deviceModel=Pixel+6&deviceId=python-client"
)

REFRESH_THRESHOLD_SECONDS = 12 * 3600

# 内置发帖文案
_FALLBACK_QUOTES = [
    "生活就像骑电动车，上坡很累，下坡很爽，平路最无聊。",
    "不是路到了尽头，而是该转弯了。",
    "梦想还是要有的，万一哪天充电时就实现了呢。",
    "充电五分钟，通话两小时，那是手机；我的电动车能跑两小时。",
    "愿你走出半生，归来仍是少年，电池还是满的。",
    "人生没有白走的路，每一步都算数，每一度电都不浪费。",
    "电动车主的快乐很简单：满电出发，一路绿灯。",
    "世界那么大，骑上 ZEEHO 去看看。",
]


# ============================================================
# 日志
# ============================================================
def _is_debug() -> bool:
    val = os.getenv("ZEEHO_DEBUG", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        # 降级为 ASCII，替换无法编码的字符
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def log(msg: str) -> None:
    _safe_print(msg)


def debug(msg: str) -> None:
    if _is_debug():
        print(f"[DEBUG] {msg}")


# ============================================================
# 通知
# ============================================================
try:
    from notify import send as _ql_send  # type: ignore
    _HAS_NOTIFY = True
except Exception:
    _HAS_NOTIFY = False


def notify(title: str, content: str) -> None:
    if _HAS_NOTIFY:
        try:
            _ql_send(title, content)
            return
        except Exception as e:
            debug(f"通知发送失败，降级 print: {e}")
    _safe_print(f"\n{'='*50}")
    _safe_print(f"【{title}】")
    _safe_print(content)
    _safe_print(f"{'='*50}\n")


# ============================================================
# 脱敏
# ============================================================
def mask_phone(value: Any) -> str:
    text = str(value)
    if "****" in text:
        return text
    if len(text) == 11 and text.isdigit():
        return f"{text[:3]}****{text[-4:]}"
    return "<redacted>"


def mask_token(token: str) -> str:
    if not token:
        return "<empty>"
    parts = token.split(" ", 1)
    raw = parts[1] if len(parts) == 2 else token
    if len(raw) <= 8:
        return raw[:2] + "****"
    return f"{raw[:4]}...{raw[-4:]}"


# ============================================================
# 签名算法（来自 zeeho_auto/signer.py）
# ============================================================
def _gen_nonce() -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    rand_part = "".join(secrets.choice(alphabet) for _ in range(16))
    return f"{rand_part}{int(time.time() * 1000)}"


def _normalize_query(params: Mapping[str, Any] | None) -> str:
    if not params:
        return ""
    sorted_items = sorted(params.items(), key=lambda kv: kv[0])
    parts = []
    for k, v in sorted_items:
        encoded_v = urllib.parse.quote(str(v), safe="")
        parts.append(f"{k}={encoded_v}")
    return "&".join(parts)


def _sha1_hex(data: str) -> str:
    return hashlib.sha1(data.encode("utf-8")).hexdigest()


def _md5_hex(data: str) -> str:
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def sign_request(
    *,
    method: str,
    query_params: Mapping[str, Any] | None = None,
    body: str = "",
) -> dict[str, str]:
    """生成签名所需的全部请求头。
    """
    method_lower = method.lower().strip()
    if method_lower in ("get", "delete"):
        query_for_sign = _normalize_query(query_params)
        body_for_sign = ""
    else:
        query_for_sign = _normalize_query(query_params) if query_params else ""
        body_for_sign = body or ""

    nonce = _gen_nonce()
    timestamp = str(int(time.time() * 1000))
    param_part = f"appId={APPID}&nonce={nonce}&timestamp={timestamp}"
    sign_source = query_for_sign + body_for_sign + param_part + APPSECRET
    signature = _md5_hex(_sha1_hex(sign_source))

    return {
        "Cfmoto-X-Param": param_part,
        "Cfmoto-X-Sign": signature,
        "Cfmoto-X-Sign-Type": "0",
        "appId": APPID,
        "nonce": nonce,
        "signature": signature,
        "timestamp": timestamp,
    }


# ============================================================
# API 异常
# ============================================================
class ApiError(Exception):
    def __init__(self, code: int, msg: str, raw: Any = None):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg
        self.raw = raw

    @property
    def is_auth_expired(self) -> bool:
        if self.code == 401:
            return True
        text = f"{self.msg or ''} {self}".lower()
        markers = ("未登录", "token", "失效", "unauthorized", "401 client error")
        return any(m in text for m in markers)


# ============================================================
# HTTP 客户端（来自 zeeho_auto/client.py）
# ============================================================
class ZeehoClient:
    """独立 HTTP 客户端实例，持有自己的 Session、Token 和用户信息。"""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "Accept-Language": "zh-CN",
            "User-Agent": USER_AGENT,
            "X-App-Info": X_APP_INFO,
        })
        self._token: str | None = None
        self._user_info: dict[str, Any] = {}

    def set_token(self, token: str) -> None:
        self._token = token
        self.session.headers["Authorization"] = token

    def clear_token(self) -> None:
        self._token = None
        self.session.headers.pop("Authorization", None)

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def user_info(self) -> dict[str, Any]:
        return self._user_info

    @user_info.setter
    def user_info(self, value: dict[str, Any]) -> None:
        self._user_info = dict(value or {})

    @staticmethod
    def _normalize_auth_scheme(token: str, style: str) -> str:
        if style != "Bearer":
            return token
        parts = token.split(" ", 1)
        if len(parts) != 2:
            return token
        scheme = parts[0]
        scheme = scheme[:1].upper() + scheme[1:].lower() if scheme else scheme
        return f"{scheme} {parts[1]}"

    def _request(
        self,
        method: str,
        base_url: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any = None,
        extra_headers: Mapping[str, str] | None = None,
        auth_style: str = "auto",
        inject_user_id: bool = False,
    ) -> Any:
        url = base_url.rstrip("/") + "/" + path.lstrip("/")

        body_str = ""
        if json_body is not None:
            body_str = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False)

        sign_headers = sign_request(
            method=method,
            query_params=query,
            body=body_str,
        )

        if auth_style == "basic":
            auth_value = BASIC_AUTH
        elif self._token:
            auth_value = self._normalize_auth_scheme(self._token, auth_style)
        else:
            auth_value = BASIC_AUTH

        headers: dict[str, Any] = {"Authorization": auth_value}

        if body_str:
            headers["Content-Type"] = "application/json; charset=utf-8"

        if inject_user_id:
            uid = self._user_info.get("id")
            if uid is not None:
                headers["user_id"] = str(uid)

        if extra_headers:
            headers.update(extra_headers)
        headers.update(sign_headers)

        debug(f"{method} {url} query={query}")

        resp = self.session.request(
            method=method,
            url=url,
            params=query,
            data=body_str.encode("utf-8") if body_str else None,
            headers=headers,
            timeout=40,
            verify=True,
        )

        try:
            payload = resp.json()
        except ValueError:
            if resp.status_code == 401:
                raise ApiError(401, "Token 已失效，请重新登录", raw=resp.text)
            resp.raise_for_status()
            raise ApiError(-1, f"非 JSON 响应: {resp.text[:200]}", raw=resp.text)

        debug(f"resp <- code={payload.get('code')} msg={payload.get('msg')}")

        if resp.status_code == 401:
            raise ApiError(
                401,
                payload.get("msg") or payload.get("message") or "Token 已失效，请重新登录",
                raw=payload,
            )

        code = payload.get("code")
        try:
            code_num = int(code)
        except (TypeError, ValueError):
            code_num = -1
        if code_num != SUCCESS_CODE:
            raise ApiError(
                code_num,
                payload.get("msg") or payload.get("message") or "未知错误",
                raw=payload,
            )
        return payload

    def get(self, base_url: str, path: str, **kw: Any) -> Any:
        return self._request("GET", base_url, path, **kw)

    def post(self, base_url: str, path: str, **kw: Any) -> Any:
        return self._request("POST", base_url, path, **kw)

    def put(self, base_url: str, path: str, **kw: Any) -> Any:
        return self._request("PUT", base_url, path, **kw)

    def delete(self, base_url: str, path: str, **kw: Any) -> Any:
        return self._request("DELETE", base_url, path, **kw)


# ============================================================
# API 层（来自 zeeho_auto/api.py）
# ============================================================
class ZeehoAPI:
    """封装所有需要的 API 端点。"""

    def __init__(self, cli: ZeehoClient) -> None:
        self.c = cli

    # ---------- 资料与积分（tapi 域名）----------
    def get_me_base_info(self) -> dict:
        return self.c.get(CM_URL, "baseInfo_v2")["data"]

    def total_integral(self) -> dict:
        return self.c.get(CM_URL, "integral/totalIntegral")["data"]

    def get_other_info(self) -> dict:
        return self.c.get(CM_URL, "baseInfo/others_v2")["data"]

    def save_other_info(self, body: dict) -> Any:
        return self.c.post(CM_URL, "saveOthersInfo", json_body=body)["data"]

    def update_user_info(self, body: dict) -> Any:
        return self.c.put(CM_URL, "baseInfo_v3", json_body=body)["data"]

    def task_snapshot(self, user_id: int | str) -> dict:
        return self.c.get(
            CM_URL, "integral/getByUserId",
            query={"userId": user_id},
        )["data"]

    def adjust_by_share(self) -> Any:
        return self.c.get(CM_URL, "integral/adjustByShare")["data"]

    # ---------- 签到 ----------
    def h5_signin_status(self) -> dict | None:
        data = self.c.get(
            H5_BASE_URL, "cfmotoservermine/signin",
            auth_style="Bearer", inject_user_id=True,
        )["data"]
        return data or None

    def sign_in_h5(self) -> dict:
        return self.c.post(
            H5_BASE_URL, "cfmotoservermine/signin",
            json_body=None,
            auth_style="Bearer", inject_user_id=True,
        )["data"]

    def h5_signin_continuity(self, date_str: str) -> dict:
        return self.c.get(
            H5_BASE_URL, "cfmotoservermine/signInSupplement/continuity",
            query={"dateTime": date_str},
            auth_style="Bearer", inject_user_id=True,
        )["data"] or {}

    # ---------- 社区 ----------
    def feed_list(self, page: int = 1, page_size: int = 20) -> list:
        data = self.c.get(
            CO_URL, "community/qbTzInfoNewV2",
            query={
                "page": page, "pageSize": page_size,
                "postModule": 2, "slidingType": 1,
            },
        )["data"]
        return data if isinstance(data, list) else []

    def like_favorite(self, post_id: str, kind_flag: str = "0") -> bool:
        return bool(self.c.post(
            CO_URL, "socialCommu/likeFavoriteInfo",
            json_body={"kindFlag": kind_flag, "postId": int(post_id)},
        )["data"])

    def publish_article(self, content: str) -> bool:
        body = {
            "activityId": "", "activitypostid": "", "addrPosition": "",
            "files": "[]", "location": "", "postSubInfo": None,
            "postcontent": content, "sendTos": "", "topicid": "",
        }
        return bool(self.c.post(CO_URL, "commonArticle", json_body=body)["data"])

    def delete_article(self, article_id: str, post_type: int = 1) -> bool:
        return bool(self.c.delete(
            CO_URL, "commonArticle/deleteArticle",
            query={"articleId": article_id, "postType": post_type},
        )["data"])

    def share_article(self, article_id: str) -> Any:
        return self.c.put(CO_URL, f"article/share/{article_id}")["data"]

    # ---------- token 刷新 ----------
    def refresh_token(self, refresh_token: str) -> dict:
        return self.c.get(
            CM_URL, "user/access_token_v2",
            query={
                "grant_type": "refresh_token",
                "username": "",
                "password": refresh_token,
                "sign": "",
            },
            extra_headers={"Authorization": BASIC_AUTH},
        )["data"]


# ============================================================
# 工具函数
# ============================================================
def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def _task_list(payload: dict) -> list:
    return _as_list(payload.get("ps0003_01_002_VOList") or payload.get("taskList"))


def _task_code(task: dict) -> str:
    return str(task.get("code") or task.get("integralCode") or "")


def _complete_flag(task: dict | None) -> str:
    if not task:
        return ""
    flag = task.get("completeFlag")
    return "" if flag in (None, "") else str(flag)


def _task_is_done(task: dict | None) -> bool:
    return bool(_complete_flag(task))


def _integral_total(value: Any) -> int | float | None:
    if isinstance(value, dict):
        for key in ("integralTotal", "total"):
            if value.get(key) is not None:
                value = value[key]
                break
        else:
            return None
    return value if isinstance(value, (int, float)) else None


def _normalize_birthday(value: Any) -> str:
    text = str(value or "").strip().replace(".", "-")
    if not text or text.lower() in {"none", "null", "0000-00-00"}:
        return ""
    return text


def _build_other_info_body(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    data: list[dict[str, Any]] = []
    for item in _as_list(payload.get("data")):
        if not isinstance(item, dict):
            continue
        options = [o for o in _as_list(item.get("options")) if isinstance(o, dict)]
        options = [o for o in options if o.get("code") and o.get("codeType")]
        if not options:
            continue
        count = 1 if int(item.get("selectType") or 1) <= 1 else min(2, len(options))
        selected = random.sample(options, count)
        data.append({
            "check": True,
            "codeType": str(selected[0]["codeType"]),
            "codes": ",".join(str(o["code"]) for o in selected),
        })
    if not data:
        raise ApiError(-1, "完善资料接口没有可提交的候选项", raw=payload)
    return {"data": data}


def _build_birthday_body(base: dict[str, Any], birthday: str) -> dict[str, Any]:
    return {
        "avatar": base.get("avatar") or "",
        "backgroundPic": base.get("backgroundPic") or "",
        "birthday": birthday,
        "city": base.get("city") or "",
        "idPictures": base.get("idPictures") or "",
        "nickName": base.get("nickName") or "",
        "otherBaseBo": None,
        "province": base.get("province") or "",
        "sex": base.get("sex") or "",
        "signature": base.get("signature") or "",
    }


def _pick_post_id(feed: list, *, exclude_self: str | None = None) -> str | None:
    candidates = []
    for item in feed:
        if not isinstance(item, dict):
            continue
        post_id = str(item.get("tuuid") or "")
        if not post_id:
            continue
        if exclude_self and str(item.get("userid") or "") == exclude_self:
            continue
        candidates.append(post_id)
    return random.choice(candidates) if candidates else None


def _human_delay(low: float = 0.5, high: float = 2.0) -> None:
    time.sleep(random.uniform(low, high))


def _fetch_content() -> str:
    """获取发帖内容：尝试外部 API，失败用内置兜底。"""
    try:
        resp = requests.get("https://tmini.net/api/duanzi", timeout=8)
        resp.raise_for_status()
        quote = (resp.json().get("quote") or "").strip()
        if quote:
            return quote[:200]
    except Exception:
        pass
    return random.choice(_FALLBACK_QUOTES)


# ============================================================
# Token 环境变量解析
# ============================================================
def parse_token_entry(entry: str) -> dict[str, Any]:
    """解析单个 token 条目。

    支持格式：
      bearer xxx
      bearer xxx|refresh_token|expires_at
      {"access_token":"bearer xxx","refresh_token":"...","expires_at":...}
    """
    entry = entry.strip()
    if not entry:
        return {}

    # JSON 格式
    if entry.startswith("{"):
        try:
            obj = json.loads(entry)
            access = obj.get("access_token") or ""
            if access and " " not in access:
                access = f"Bearer {access}"
            return {
                "access_token": access,
                "refresh_token": obj.get("refresh_token") or None,
                "expires_at": obj.get("expires_at") or None,
            }
        except (json.JSONDecodeError, TypeError):
            pass

    # 管道分隔：access|refresh|expires_at
    if "|" in entry:
        parts = entry.split("|")
        access = parts[0].strip()
        refresh = parts[1].strip() if len(parts) > 1 else None
        expires_at = parts[2].strip() if len(parts) > 2 else None
        if access and " " not in access:
            access = f"Bearer {access}"
        return {
            "access_token": access or None,
            "refresh_token": refresh or None,
            "expires_at": expires_at or None,
        }

    # 纯 access_token
    access = entry
    if " " not in access:
        access = f"Bearer {access}"
    return {"access_token": access}


def load_tokens() -> list[dict[str, Any]]:
    """从环境变量加载所有 token 条目。"""
    raw = os.getenv("ZEEHO_TOKEN", "").strip()
    if not raw:
        return []

    # 用 @ 或换行分隔多个账号
    entries = []
    for part in raw.replace("@", "\n").split("\n"):
        part = part.strip()
        if part:
            parsed = parse_token_entry(part)
            if parsed.get("access_token"):
                entries.append(parsed)
    return entries


# ============================================================
# Token 刷新
# ============================================================
def remaining_seconds(expires_at: Any) -> int | None:
    if expires_at is None or expires_at == "":
        return None
    try:
        exp = int(float(expires_at))
    except (TypeError, ValueError):
        return None
    return exp - int(time.time())


def should_refresh(expires_at: Any, refresh_token: str | None) -> bool:
    if not refresh_token:
        return False
    left = remaining_seconds(expires_at)
    if left is None:
        return True
    return left < REFRESH_THRESHOLD_SECONDS


def format_remaining(seconds: int | float | None) -> str:
    if seconds is None:
        return "未知"
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "未知"
    if total <= 0:
        return "已过期"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}天{hours}小时" if hours > 0 else f"{days}天"
    if hours > 0:
        return f"{hours}小时{minutes}分" if minutes > 0 else f"{hours}小时"
    if minutes > 0:
        return f"{minutes}分"
    return "不足1分"


def try_refresh_token(
    cli: ZeehoClient,
    api: ZeehoAPI,
    token_info: dict[str, Any],
) -> dict[str, Any]:
    """尝试刷新 token，返回更新后的 token_info。"""
    refresh = token_info.get("refresh_token")
    expires_at = token_info.get("expires_at")

    if not should_refresh(expires_at, refresh):
        left = remaining_seconds(expires_at)
        status = "skipped_no_refresh" if not refresh else "skipped_fresh"
        return {**token_info, "_refresh_status": status, "_remaining": format_remaining(left)}

    log(f"  token 剩余 {format_remaining(remaining_seconds(expires_at))}，尝试刷新...")
    try:
        auth_rsp = api.refresh_token(refresh)
    except ApiError as exc:
        log(f"  ⚠ token 刷新失败: {exc.msg}")
        return {**token_info, "_refresh_status": "failed", "_error": exc.msg}

    access = auth_rsp.get("access_token") or ""
    if not access:
        return {**token_info, "_refresh_status": "failed", "_error": "刷新响应缺少 access_token"}

    token_type = str(auth_rsp.get("token_type") or "Bearer")
    new_token = f"{token_type} {access}" if " " not in access else access
    new_refresh = auth_rsp.get("refresh_token") or refresh
    new_expires_in = auth_rsp.get("expires_in")
    try:
        new_expires_at = int(time.time()) + int(float(new_expires_in)) if new_expires_in else None
    except (TypeError, ValueError):
        new_expires_at = None

    cli.set_token(new_token)
    log(f"  ✓ token 刷新成功，剩余 {format_remaining(remaining_seconds(new_expires_at))}")
    return {
        "access_token": new_token,
        "refresh_token": new_refresh,
        "expires_at": new_expires_at,
        "_refresh_status": "refreshed",
        "_remaining": format_remaining(remaining_seconds(new_expires_at)),
    }


# ============================================================
# 单账号执行流程
# ============================================================
class AccountRunner:
    """单账号任务执行器。"""

    def __init__(self, cli: ZeehoClient) -> None:
        self.cli = cli
        self.api = ZeehoAPI(cli)
        self.user_id: str | None = None

    # ---------- 获取账号信息 ----------
    def load_account_info(self) -> dict[str, Any]:
        base = self.api.get_me_base_info() or {}
        integral_total = None
        try:
            integral_total = _integral_total(self.api.total_integral())
        except ApiError as e:
            debug(f"积分查询失败: {e.msg}")

        if base.get("id"):
            self.cli.user_info = {**self.cli.user_info, **base}
            self.user_id = str(base["id"])

        phone = base.get("phone") or self.cli.user_info.get("phone") or ""
        nick = base.get("nickName") or base.get("nickname") or ""

        log(f"  账号: {mask_phone(phone)} ({nick})")
        log(f"  积分: {integral_total if integral_total is not None else '查询失败'}")
        return {"baseInfo": base, "integralTotal": integral_total}

    # ---------- 签到 ----------
    def check_in(self) -> dict[str, Any]:
        log("  [签到] 查询签到状态...")
        before_integral = None
        try:
            before_integral = _integral_total(self.api.total_integral())
        except ApiError:
            pass

        try:
            status_data = self.api.h5_signin_status()
        except ApiError as exc:
            log(f"  [签到] 状态查询失败: {exc.msg}")
            return {"status": "error", "error": exc.msg}

        if status_data:
            days = self._query_continuity_days()
            log(f"  [签到] 今日已签到，累计 {days} 天")
            return {"status": "already_signed", "days": days}

        log("  [签到] 未签到，执行签到动作...")
        try:
            signin_rsp = self.api.sign_in_h5() or {}
        except ApiError as exc:
            log(f"  [签到] 签到失败: {exc.msg}")
            return {"status": "failed", "error": exc.msg}

        after_integral = None
        try:
            after_integral = _integral_total(self.api.total_integral())
        except ApiError:
            pass

        delta = None
        if isinstance(before_integral, (int, float)) and isinstance(after_integral, (int, float)):
            delta = after_integral - before_integral

        days = self._query_continuity_days()
        sign_statue = signin_rsp.get("signStatue")
        log(f"  [签到] 完成 signStatue={sign_statue} 积分变化={delta} 累计{days}天")
        return {
            "status": "completed",
            "days": days,
            "delta": delta,
            "beforeIntegral": before_integral,
            "afterIntegral": after_integral,
        }

    def _query_continuity_days(self) -> int | None:
        today = date.today().isoformat()
        try:
            data = self.api.h5_signin_continuity(today) or {}
            days = data.get("continuityDays")
            return int(days) if days is not None else None
        except (ApiError, TypeError, ValueError):
            return None

    # ---------- 任务快照 ----------
    def task_snapshot(self) -> dict[str, Any]:
        if not self.user_id:
            return {"integral": None, "tasks": {}, "taskSummaries": []}
        payload = self.api.task_snapshot(self.user_id) or {}
        tasks = _task_list(payload)
        total = _integral_total(payload.get("integralTotal"))
        if total is None:
            try:
                total = _integral_total(self.api.total_integral())
            except ApiError:
                pass
        task_map = {_task_code(t): t for t in tasks if isinstance(t, dict) and _task_code(t)}
        summaries = [
            {"code": _task_code(t), "name": t.get("name") or _task_code(t),
             "completeFlag": _complete_flag(t)}
            for t in tasks if isinstance(t, dict) and _task_code(t)
        ]
        return {"integral": total, "tasks": task_map, "taskSummaries": summaries}

    # ---------- 积分任务 ----------
    def complete_tasks(self) -> list[dict[str, Any]]:
        if not self.user_id:
            log("  [任务] 缺少 userId，跳过")
            return []

        snapshot = self.task_snapshot()
        results = []

        for task in snapshot["taskSummaries"]:
            code = task["code"]
            done = bool(task["completeFlag"])
            name = task["name"]

            if done:
                results.append({"code": code, "name": name, "status": "已完成"})
                continue

            result = self._dispatch_task(code, name)
            results.append(result)

        return results

    def _dispatch_task(self, code: str, name: str) -> dict[str, Any]:
        """按任务 code 执行对应动作。"""
        result: dict[str, Any] = {"code": code, "name": name}

        # 1005 个人信息完善
        if code == "1005":
            try:
                before = self.api.get_other_info()
                body = _build_other_info_body(before)
                _human_delay()
                self.api.save_other_info(body)
                result["status"] = "已执行"
                log(f"  [任务] {code} {name}: 已提交完善资料")
            except ApiError as exc:
                result["status"] = "失败"
                result["error"] = exc.msg
                log(f"  [任务] {code} {name}: 失败 - {exc.msg}")
            return result

        # 1008 车主生日
        if code == "1008":
            r = self._do_birthday_task()
            result["status"] = r.get("status_text", "未知")
            result["detail"] = r.get("detail", "")
            log(f"  [任务] {code} {name}: {r.get('status_text', '未知')} - {r.get('detail', '')}")
            return result

        # 1023 每日发帖（发帖+删帖）
        if code == "1023":
            r = self._do_publish_and_delete()
            result["status"] = r.get("status", "未知")
            result["detail"] = r.get("detail", "")
            log(f"  [任务] {code} {name}: {r.get('status', '未知')} - {r.get('detail', '')}")
            return result

        # 1024 分享
        if code == "1024":
            r = self._do_share()
            result["status"] = r.get("status", "未知")
            result["detail"] = r.get("detail", "")
            log(f"  [任务] {code} {name}: {r.get('status', '未知')} - {r.get('detail', '')}")
            return result

        # 1026 点赞
        if code == "1026":
            r = self._do_like()
            result["status"] = r.get("status", "未知")
            result["detail"] = r.get("detail", "")
            log(f"  [任务] {code} {name}: {r.get('status', '未知')} - {r.get('detail', '')}")
            return result

        # 1006 绑定车辆、1012 推荐注册：需要交互式输入，青龙跳过
        if code in ("1006", "1012"):
            result["status"] = "跳过"
            result["detail"] = "需要外部输入，青龙环境无法执行"
            log(f"  [任务] {code} {name}: 跳过（需要外部输入）")
            return result

        # 其他未知任务
        result["status"] = "跳过"
        result["detail"] = f"未知任务 code={code}"
        log(f"  [任务] {code} {name}: 未知任务，跳过")
        return result

    def _do_birthday_task(self) -> dict[str, str]:
        """1008 生日任务：已设只复核，未设写明天。"""
        try:
            base = self.api.get_me_base_info() or {}
        except ApiError as exc:
            return {"status_text": "失败", "detail": f"资料查询失败: {exc.msg}"}

        current = _normalize_birthday(
            base.get("birthday") or self.cli.user_info.get("birthday")
        )

        if current:
            return {
                "status_text": "已设(复核)",
                "detail": f"生日={current}，未改写",
            }

        # 未设生日，写明天
        target = (date.today() + timedelta(days=1)).isoformat()
        body = _build_birthday_body(base, target)
        try:
            _human_delay()
            self.api.update_user_info(body)
            return {
                "status_text": "已执行",
                "detail": f"已写入生日={target}",
            }
        except ApiError as exc:
            return {"status_text": "失败", "detail": f"写入失败: {exc.msg}"}

    def _do_like(self) -> dict[str, str]:
        """1026 点赞。"""
        try:
            feed = self.api.feed_list(page=1, page_size=20)
        except ApiError as exc:
            return {"status": "失败", "detail": f"帖子列表获取失败: {exc.msg}"}

        post_id = _pick_post_id(feed, exclude_self=self.user_id)
        if not post_id:
            return {"status": "跳过", "detail": "没有可点赞的帖子"}

        try:
            _human_delay()
            self.api.like_favorite(post_id, kind_flag="0")
            return {"status": "已执行", "detail": f"postId={post_id}"}
        except ApiError as exc:
            return {"status": "失败", "detail": f"点赞失败: {exc.msg}"}

    def _do_publish_and_delete(self) -> dict[str, str]:
        """1023 发帖+删帖。"""
        content = _fetch_content()
        try:
            _human_delay()
            self.api.publish_article(content)
        except ApiError as exc:
            return {"status": "失败", "detail": f"发帖失败: {exc.msg}"}

        # 查找刚发的帖子
        article_id = self._find_my_latest_article()
        if not article_id:
            return {"status": "已执行", "detail": "发帖成功但未找到帖子ID，跳过删帖"}

        try:
            _human_delay()
            self.api.delete_article(article_id, post_type=1)
            return {"status": "已执行", "detail": f"发帖+删帖成功 articleId={article_id}"}
        except ApiError as exc:
            return {"status": "已执行", "detail": f"发帖成功，删帖失败: {exc.msg}"}

    def _find_my_latest_article(self) -> str | None:
        if not self.user_id:
            return None
        try:
            feed = self.api.feed_list(page=1, page_size=20)
        except ApiError:
            return None
        for item in feed:
            if not isinstance(item, dict):
                continue
            if str(item.get("userid") or "") == self.user_id:
                post_id = str(item.get("tuuid") or "")
                if post_id:
                    return post_id
        return None

    def _do_share(self) -> dict[str, str]:
        """1024 分享。"""
        try:
            feed = self.api.feed_list(page=1, page_size=20)
        except ApiError as exc:
            return {"status": "失败", "detail": f"帖子列表获取失败: {exc.msg}"}

        post_id = _pick_post_id(feed)
        if not post_id:
            return {"status": "跳过", "detail": "没有可分享的帖子"}

        try:
            _human_delay()
            self.api.share_article(post_id)
            _human_delay(0.3, 1.0)
            self.api.adjust_by_share()
            return {"status": "已执行", "detail": f"postId={post_id}"}
        except ApiError as exc:
            return {"status": "失败", "detail": f"分享失败: {exc.msg}"}


# ============================================================
# 主入口
# ============================================================
def run_account(index: int, token_info: dict[str, Any]) -> dict[str, Any]:
    """执行单个账号的全流程。"""
    access_token = token_info.get("access_token") or ""
    log(f"\n{'─'*50}")
    log(f"账号 #{index + 1}  token={mask_token(access_token)}")

    cli = ZeehoClient()
    cli.set_token(access_token)

    api = ZeehoAPI(cli)
    runner = AccountRunner(cli)

    report: dict[str, Any] = {
        "index": index + 1,
        "token": mask_token(access_token),
        "errors": [],
    }

    # token 刷新
    try:
        updated = try_refresh_token(cli, api, token_info)
        refresh_status = updated.get("_refresh_status", "unknown")
        if refresh_status == "refreshed":
            log(f"  token 已自动刷新，剩余 {updated.get('_remaining', '未知')}")
        elif refresh_status == "failed":
            report["errors"].append(f"token刷新失败: {updated.get('_error', '')}")
        report["tokenRefresh"] = refresh_status
    except Exception as exc:
        report["errors"].append(f"token刷新异常: {exc}")
        report["tokenRefresh"] = "error"

    # 获取账号信息
    try:
        report["accountInfo"] = runner.load_account_info()
    except ApiError as exc:
        msg = f"账号信息获取失败: {exc.msg}"
        report["errors"].append(msg)
        log(f"  ⚠ {msg}")
        if exc.is_auth_expired:
            report["status"] = "token失效"
            return report
    except Exception as exc:
        report["errors"].append(f"账号信息异常: {exc}")
        log(f"  ⚠ 账号信息异常: {exc}")
        report["status"] = "异常"
        return report

    # 签到
    try:
        report["checkin"] = runner.check_in()
    except Exception as exc:
        report["errors"].append(f"签到异常: {exc}")
        log(f"  ⚠ 签到异常: {exc}")

    # 积分任务
    try:
        report["tasks"] = runner.complete_tasks()
    except Exception as exc:
        report["errors"].append(f"任务异常: {exc}")
        log(f"  ⚠ 任务异常: {exc}")

    # 任务后积分快照
    try:
        after_integral = _integral_total(api.total_integral())
        report["afterIntegral"] = after_integral
        log(f"  最终积分: {after_integral if after_integral is not None else '查询失败'}")
    except ApiError:
        pass

    report["status"] = "完成" if not report["errors"] else "部分失败"
    return report


def main() -> None:
    log("=" * 60)
    log("ZEEHO 电智行 青龙面板自动化")
    log("=" * 60)

    tokens = load_tokens()
    if not tokens:
        log("\n⚠ 未检测到 ZEEHO_TOKEN 环境变量！")
        log("请在青龙面板 → 环境变量 中添加：")
        log("  名称: ZEEHO_TOKEN")
        log("  值:   bearer xxx  （多个账号用 & 或换行分隔）")
        notify("ZEEHO 电智行", "未配置 ZEEHO_TOKEN 环境变量，脚本退出")
        return

    log(f"\n检测到 {len(tokens)} 个账号")

    all_reports = []
    for i, token_info in enumerate(tokens):
        try:
            report = run_account(i, token_info)
            all_reports.append(report)
        except Exception as exc:
            log(f"\n⚠ 账号 #{i + 1} 执行异常: {exc}")
            all_reports.append({
                "index": i + 1,
                "token": mask_token(token_info.get("access_token", "")),
                "status": "异常",
                "errors": [str(exc)],
            })

    # 汇总通知
    summary_lines = []
    for r in all_reports:
        line = f"账号#{r['index']} token={r.get('token', '?')}"
        if r.get("accountInfo"):
            base = r["accountInfo"].get("baseInfo", {})
            phone = base.get("phone", "")
            if phone:
                line += f" ({mask_phone(phone)})"
        line += f" [{r.get('status', '未知')}]"

        checkin = r.get("checkin", {})
        if checkin:
            cs = checkin.get("status", "")
            if cs == "completed":
                line += f" 签到:✓(+{checkin.get('delta', '?')})"
            elif cs == "already_signed":
                line += f" 签到:已签({checkin.get('days', '?')}天)"
            else:
                line += f" 签到:{cs}"

        tasks = r.get("tasks", [])
        if tasks:
            done = sum(1 for t in tasks if t.get("status") in ("已完成", "已执行"))
            total = len(tasks)
            line += f" 任务:{done}/{total}"

        final = r.get("afterIntegral")
        if final is not None:
            line += f" 积分:{final}"

        summary_lines.append(line)

    summary = "\n".join(summary_lines)
    sep = "=" * 60
    log(f"\n{sep}")
    log("执行汇总：")
    log(summary)
    log(sep)

    notify("ZEEHO 电智行", summary)


if __name__ == "__main__":
    main()
