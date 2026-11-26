# -*- coding: utf-8 -*-
"""
new Env('ARCFOX 极狐')

# 🔥 ARCFOX 极狐 App 签到助手🔥

支持多账号：环境变量名相同配置多个值（自动遍历），或用 & / @ 分隔多个 token。

【环境变量】
  变量名: ARCFOX
  值:   App 登录后抓包获取的 token（query 参数里的 token 值，纯协议直接复用）
  多账号示例（三选一）:
    方式1（青龙 2.13+ 原生）: 添加变量 ARCFOX 值 123，再添加一条 ARCFOX 值 456
    方式2: ARCFOX = "123@456"
    方式3: ARCFOX = "123&456"
  说明: aid 请求头可省略（实测网关仅凭 query token 识别用户）；
        如遇风控可配置完整格式 "token#aid"，aid 为登录响应 accountVo.aid。
  建议配合抓包工具（如 Reqable/HttpCanary 过滤 mg.arcfox.cn）提取 token。

🦊 每日任务: 签到（车主账号）+ 查询积分 + 领取待领积分红包

定时建议（QL Cron）: 30 8 * * *

@Author: xiaohai
@Date: 2026.11.26
"""

import hashlib
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping

# ══════════════════════════════════════════════════════════════
# ▶ 通知模块（青龙面板自带）
# ══════════════════════════════════════════════════════════════
try:
    from notify import send
except ImportError:
    send = None

BASE_URL = "https://mg.arcfox.cn/"
APP_VERSION = "3.4.36"
APP_CODE = "333"
# 设备指纹（协议侧不校验具体值，保持稳定即可）
DEVICE_ID = "qlpanel8678d1f0"
DEVICE_MODEL = "MI10Pro"
# 青龙抓取同名变量多个值的上限（防护）
MAX_ACCOUNTS = 20

ENV_NAME = "ARCFOX"


def banner(title: str) -> None:
    print(f"\n📊 {title}\n{'═' * 50}")


# ══════════════════════════════════════════════════════════════
# ▶ 签名模块
# ══════════════════════════════════════════════════════════════
def jni_modified_utf8(value: str) -> bytes:
    """Modified UTF-8：NUL→C0 80，代理项按独立码元编码。"""
    units = value.encode("utf-16-be", "surrogatepass")
    out = bytearray()
    for off in range(0, len(units), 2):
        unit = int.from_bytes(units[off:off + 2], "big")
        if unit == 0:
            out += b"\xc0\x80"
        elif unit < 0x80:
            out.append(unit)
        elif unit < 0x800:
            out += bytes((0xc0 | unit >> 6, 0x80 | unit & 63))
        else:
            out += bytes((0xe0 | unit >> 12, 0x80 | unit >> 6 & 63, 0x80 | unit & 63))
    return bytes(out)


def native_signature(path: str, params: Mapping[str, str], secret: str) -> str:
    """键按 Modified UTF-8 字节序排序，拼接后百分号编码，MD5 小写 hex。"""
    pairs = sorted((jni_modified_utf8(k), jni_modified_utf8(v)) for k, v in params.items())
    material = (jni_modified_utf8(path) + b"_" + b"".join(k + b"=" + v + b"_" for k, v in pairs)
                + jni_modified_utf8(secret))
    encoded = urllib.parse.quote_from_bytes(material, safe="-._~")
    return hashlib.md5(encoded.encode("ascii")).hexdigest()


def build_signed_query(path: str, params: Mapping[str, str], *, app_key: str, secret: str) -> dict:
    """构造带公共参数 appkey/signt/nonce/sign 的最终 query（公共键覆盖同名业务键）。"""
    if len(jni_modified_utf8(path)) > 2048 or len(params) > 256:
        raise ValueError("签名输入超限")
    if any(len(jni_modified_utf8(k)) > 128 or len(jni_modified_utf8(v)) > 4096
           for k, v in params.items()):
        raise ValueError("签名键值超限")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in params.items()):
        raise TypeError("签名键值必须是字符串")
    query = dict(params)
    query.update(appkey=app_key, signt=str(int(time.time() * 1000)),
                 nonce=str(uuid.uuid4()))
    query["sign"] = native_signature(path, query, secret)
    return query


# ══════════════════════════════════════════════════════════════
# ▶ 业务客户端
# ══════════════════════════════════════════════════════════════
class ApiError(Exception):
    def __init__(self, code: str, message: str, payload: dict | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.payload = payload


class SessionInvalidError(ApiError):
    """token 失效（需重新抓包更新环境变量）。"""


class ArcfoxClient:
    def __init__(self, *, token: str, aid: str = "", timeout: int = 30):
        self.token = token
        self.aid = aid
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {
            "aid": self.aid,
            "vin": "",
            "platform": "Android",
            "appversion": APP_VERSION,
            "appcode": APP_CODE,
            "deviceId": DEVICE_ID,
            "deviceModel": DEVICE_MODEL,
        }
        return h

    def _request(self, method: str, path: str, *, params: dict[str, str] | None = None,
                 body: dict | None = None) -> dict:
        query = dict(params or {})
        query.setdefault("token", self.token)
        signed = build_signed_query(path, query, app_key=APP_KEY, secret=SECRET)
        url = BASE_URL + path + "?" + urllib.parse.urlencode(signed)
        data = None
        headers = self._headers()
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                http_status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            http_status, raw = e.code, e.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError("TRANSPORT", f"非 JSON 响应（HTTP {http_status}）")
        if not isinstance(payload, dict) or not (200 <= http_status < 300):
            raise ApiError("HTTP", f"HTTP {http_status}", payload if isinstance(payload, dict) else None)
        return self._check_business(payload)

    def _check_business(self, payload: dict) -> dict:
        if payload.get("status") == "SUCCEED" or str(payload.get("statusCode", "")) == "0":
            return payload
        code = str(payload.get("errorCode", ""))
        if code == "API-GATEWAY.4402":
            raise SessionInvalidError(code, str(payload.get("errorMessage", "会话失效")), payload)
        raise ApiError(code or "UNKNOWN",
                       str(payload.get("errorMessage") or payload.get("message") or "业务失败"), payload)

    # ---- 接口 ----
    def get_user_info(self) -> dict:
        return self._request("GET", "user/public/account/getUserInfo")

    def get_user_integral(self) -> dict:
        return self._request("GET", "mall-integral/public/integral/getUserIntegral")

    def sync_integral_signin(self) -> dict:
        return self._request("GET", "mall-integral/public/integral/syncIntegral",
                             params={"syncType": "5"})

    def get_user_envelopes(self) -> dict:
        return self._request("GET", "mall-integral/public/envelope/getUserEnvelopes")

    def receive_envelope(self, envelope_id: str) -> dict:
        return self._request("GET", "mall-integral/public/envelope/receive",
                             params={"envelopeId": envelope_id})


# ══════════════════════════════════════════════════════════════
# ▶ 单账号任务流程
# ══════════════════════════════════════════════════════════════
def run_account(index: int, token: str, aid: str) -> str:
    """执行一个账号的完整任务，返回通知文本。"""
    lines = []
    client = ArcfoxClient(token=token, aid=aid)

    # 1️⃣ 用户信息
    nickname = "未知用户"
    try:
        info = client.get_user_info().get("data") or {}
        nickname = info.get("nickname") or f"用户{index}"
        owner = info.get("vehicleOwnerFlag") == "OWNER"
        print(f"👤 账号[{index}] {nickname} {'🚗 车主' if owner else '🚶 非车主'}")
    except SessionInvalidError:
        msg = f"❌ 账号[{index}] token 已失效，请重新抓包更新 ARCFOX"
        print(msg)
        return msg
    except ApiError as e:
        print(f"⚠️ 账号[{index}] 获取用户信息失败: {e.message}")

    # 2️⃣ 积分与签到状态
    try:
        detail = client.get_user_integral().get("data") or {}
        integral = detail.get("integral") or "0"
        days = detail.get("checkindays") or 0
        print(f"💰 当前积分: {integral} | 连签: {days} 天")

        if detail.get("isCheck"):
            print("📅 今日已签到，跳过 🎉")
            lines.append(f"👤 {nickname}\n💰 积分: {integral}（连签 {days} 天）\n📅 今日已签到 ✅")
        else:
            try:
                r = client.sync_integral_signin().get("data") or {}
                gained = r.get("checkinIntegral") or 0
                extra = r.get("extraIntegral") or 0
                toast = r.get("checkToast") or ""
                new_integral = r.get("integral") or integral
                new_days = r.get("checkindays") or days + 1
                total_gain = gained + extra
                print(f"✅ 签到成功 +{total_gain} 分{f'（含额外奖励 +{extra}）' if extra else ''} 🎁")
                if toast:
                    print(f"💌 {toast}")
                print(f"💰 最新积分: {new_integral} | 连签: {new_days} 天")
                lines.append(f"👤 {nickname}\n💰 积分: {new_integral}（连签 {new_days} 天）\n"
                             f"📅 签到成功 +{total_gain} 分 ✅{' 🎁' if extra else ''}")
            except ApiError as e:
                # 非车主等场景签到被拒
                print(f"📅 签到未成功: {e.message}")
                lines.append(f"👤 {nickname}\n💰 积分: {integral}（连签 {days} 天）\n"
                             f"📅 签到: {e.message}")
    except SessionInvalidError:
        msg = f"❌ 账号[{index}] {nickname} token 已失效，请重新抓包更新"
        print(msg)
        return "\n".join(lines + [msg])
    except ApiError as e:
        print(f"⚠️ 账号[{index}] 获取积分失败: {e.message}")
        lines.append(f"👤 {nickname}\n⚠️ 积分查询失败: {e.message}")

    # 3️⃣ 积分红包
    try:
        envelopes = client.get_user_envelopes().get("data") or []
        pending = [e for e in envelopes
                   if str(e.get("receiveStatus")) != "1" and str(e.get("payStatus")) == "1"]
        if pending:
            got = 0
            for env in pending:
                try:
                    client.receive_envelope(str(env.get("envelopeId")))
                    got += int(env.get("integral") or 0)
                    print(f"🧧 领取红包 +{env.get('integral')} 分 🎊")
                except ApiError as e:
                    print(f"⚠️ 红包 {env.get('envelopeId')} 领取失败: {e.message}")
            if got:
                lines[-1] += f"\n🧧 红包入账 +{got} 分 🎊"
        elif envelopes:
            print("🧧 暂无可领取的红包")
        else:
            print("🧧 暂无红包记录")
    except ApiError as e:
        print(f"⚠️ 账号[{index}] 红包查询失败: {e.message}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# ▶ 多账号环境变量解析
# ══════════════════════════════════════════════════════════════
def load_accounts() -> list[tuple[str, str]]:
    """解析 ARCFOX 环境变量（青龙多值 + @ / & 分隔），返回 [(token, aid), ...]。"""
    raw_values: list[str] = []

    # 青龙 2.13+：同名变量多个值会体现在 os.environ 中
    env_value = os.environ.get(ENV_NAME, "")
    if env_value:
        raw_values.append(env_value)

    # 青龙数据目录方式（容器内 /ql/data/config/env.sh 或 db 注入的变量序列）
    for i in range(2, MAX_ACCOUNTS + 1):
        v = os.environ.get(f"{ENV_NAME}{i}", "")
        if v:
            raw_values.append(v)
        else:
            break

    # 兜底：青龙将多值用 & 拼接注入的情况
    merged: list[str] = []
    for v in raw_values:
        for part in v.split("&"):
            part = part.strip()
            if part:
                merged.append(part)

    # @ 分隔多账号
    values: list[str] = []
    for v in merged:
        for part in v.split("@"):
            part = part.strip()
            if part:
                values.append(part)

    accounts: list[tuple[str, str]] = []
    seen: set[str] = set()
    for v in values:
        token, _, aid = v.partition("#")
        token, aid = token.strip(), aid.strip()
        if token and token not in seen:
            seen.add(token)
            accounts.append((token, aid))
    return accounts[:MAX_ACCOUNTS]


def main() -> int:
    banner("ARCFOX 极狐 签到助手 🦊")

    accounts = load_accounts()
    if not accounts:
        print(f"❗ 未配置环境变量 {ENV_NAME}，请先在面板【环境变量】中添加")
        print(f"   格式: 名称 {ENV_NAME}，值 = 抓包获取的 token（多账号可用 @ 或 & 分隔，或添加多条同名变量）")
        return 1
    print(f"🔎 共解析到 {len(accounts)} 个账号 {'🦊' * min(len(accounts), 10)}\n")

    results: list[str] = []
    for i, (token, aid) in enumerate(accounts, 1):
        if i > 1:
            time.sleep(3)  # 账号间随机错峰由调度保证，这里固定 3s 降低风控
        print(f"────── 账号 {i}/{len(accounts)} ──────")
        try:
            results.append(run_account(i, token, aid))
        except Exception:
            err = traceback.format_exc(limit=3)
            print(f"💥 账号[{i}] 异常:\n{err}")
            results.append(f"❌ 账号[{i}] 执行异常，详见日志")

    # 📢 汇总通知
    if send and results:
        try:
            send("🦊 ARCFOX 极狐签到", "\n\n".join(results))
            print("\n📢 通知已发送")
        except Exception as e:
            print(f"\n⚠️ 通知发送失败: {e}")

    print(f"\n🏁 全部账号处理完毕，Bye~ 👋")
    return 0


if __name__ == "__main__":
    sys.exit(main())
