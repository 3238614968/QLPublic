#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cron: 37 8 * * *
new Env('起亚Kia')

🚗 起亚Kia App 自动签到 + 积分任务

🔧 环境变量（青龙面板 → 环境变量）
  KIA_TOKEN    登录 token（必填）。取值：App 登录后任意请求头里 Authorization 的值
               多账号用 @ 隔开：      KIA_TOKEN = token1@token2
               或同名变量分多条添加：  KIA_TOKEN = token1
                                     KIA_TOKEN = token2
  KIA_POST     是否自动评论 / 发文章（选填，默认开启；两者审核通过后才计分），填 false 关闭
  KIA_DELAY    多账号间隔秒数（选填，默认 3）

@auth: xiaohai
@data: 2026.11.26
"""
import base64
import hashlib
import json
import os
import random
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

# ---------- 协议常量 ----------
BASE_URL = "https://app-api.kia.cn"
CLIENT_ID = "0c465c6c45a48c48dcf81c8e30c701d2beee"
CLIENT_SECRET = "25ba38102d8f724b1e090ddb9970f5d03d2f"
APP_VERSION = "1.2.43"
CHANNEL = "80341001"
PLATFORM = "android"
USER_AGENT = "okhttp/4.12.0"

ENV_TOKEN = "KIA_TOKEN"
ENV_POST = "KIA_POST"
ENV_DELAY = "KIA_DELAY"

SIGNED = "10041001"             # 签到状态：已签到
RELATION_ON = "10011001"        # 点赞 / 关注：生效
TASK_TYPES = ("1", "2", "3")    # 任务类型：成长 / 日常 / 车主
TOKEN_EXPIRED = {"40106"}       # 登录态失效的业务码

COMMENT_TEXT = "支持起亚！期待更多精彩活动。"
JOIN_REASON = "喜欢起亚，想和车友一起交流。"
ARTICLE_TITLE = "我的起亚用车感受"
ARTICLE_TEXT = (
    "提车已经有一段时间了，日常通勤主要在市区，起步轻快，油耗表现也比预期更好。"
    "周末带家人出去转转，后排空间和后备箱都很够用。车机连接手机很方便，导航和音乐都很流畅。"
    "整体来说这台车满足了我对家用车的期待，后续有新的用车体会再和大家分享。"
)


def today():
    """北京时间日期（服务端按北京时间判断"今天"）。"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def now_text():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def log(text):
    print(text, flush=True)


def mask(text):
    """长标识只留首尾，用于日志展示。"""
    text = str(text or "")
    return text if len(text) < 11 else f"{text[:3]}{'*' * (len(text) - 7)}{text[-4:]}"


def brief(title, limit=15):
    """标题过长时截断并加省略号。"""
    title = str(title or "")
    return title if len(title) <= limit else title[:limit].rstrip() + "…"


def as_dict(data):
    return data if isinstance(data, dict) else {}


# ---------- 签名与加密 ----------
def _build_sbox():
    """生成 AES S-box：GF(2^8) 求逆后做仿射变换。"""
    exp, log_table = [0] * 255, [0] * 256
    x = 1
    for i in range(255):
        exp[i], log_table[x] = x, i
        x = (x ^ (x << 1) ^ (0x1B if x & 0x80 else 0)) & 0xFF   # 乘 3
    sbox = [0x63] * 256
    for a in range(1, 256):
        y = exp[(255 - log_table[a]) % 255]
        s = y
        for _ in range(4):
            y = ((y << 1) | (y >> 7)) & 0xFF
            s ^= y
        sbox[a] = s ^ 0x63
    return sbox


_SBOX = _build_sbox()
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _expand_key(key):
    """密钥扩展，返回 (轮数 + 1) * 4 个 4 字节字。"""
    nk = len(key) // 4
    words = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nk + 7)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = [_SBOX[b] for b in temp[1:] + temp[:1]]
            temp[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return words


def _encrypt_block(words, block):
    """AES 单块加密；状态按列存放，state[列][行]。"""
    state = [list(block[4 * c:4 * c + 4]) for c in range(4)]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                state[c][r] ^= words[rnd * 4 + c][r]

    def sub_shift():
        state[:] = [[_SBOX[state[(c + r) % 4][r]] for r in range(4)] for c in range(4)]

    def mix_columns():
        for c in range(4):
            a = state[c]
            state[c] = [
                _xtime(a[0]) ^ _xtime(a[1]) ^ a[1] ^ a[2] ^ a[3],
                a[0] ^ _xtime(a[1]) ^ _xtime(a[2]) ^ a[2] ^ a[3],
                a[0] ^ a[1] ^ _xtime(a[2]) ^ _xtime(a[3]) ^ a[3],
                _xtime(a[0]) ^ a[0] ^ a[1] ^ a[2] ^ _xtime(a[3]),
            ]

    rounds = len(words) // 4 - 1
    add_round_key(0)
    for rnd in range(1, rounds):
        sub_shift()
        mix_columns()
        add_round_key(rnd)
    sub_shift()
    add_round_key(rounds)
    return bytes(state[c][r] for c in range(4) for r in range(4))


def _aes256_ecb(plain, words):
    pad_len = 16 - len(plain) % 16
    plain += bytes([pad_len]) * pad_len
    return b"".join(_encrypt_block(words, plain[i:i + 16]) for i in range(0, len(plain), 16))


_ROUND_KEYS = _expand_key(hashlib.sha256(CLIENT_SECRET.encode("utf-8")).hexdigest()[:32].encode("utf-8"))


def make_sign(path, params, timestamp, nonce):
    """sign = sha256(排序拼接[secret, timestamp, nonce, base64(AES-256-ECB(path + 排序query))])。"""
    query = "&".join(sorted(f"{k}={v}" for k, v in params.items())) if params else ""
    aes_text = base64.b64encode(_aes256_ecb((path + query).encode("utf-8"), _ROUND_KEYS)).decode()
    joined = "".join(sorted([CLIENT_SECRET, timestamp, nonce, aes_text]))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ---------- 账号解析 ----------
JWT_RE = re.compile(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


class Account:
    def __init__(self, token=""):
        self.token = token


def parse_tokens(raw):
    """提取 token：@、换行、&、逗号、空格等分隔方式均可，也兼容整段粘贴。"""
    tokens = JWT_RE.findall(raw or "")
    if not tokens and (raw or "").strip():
        tokens = [part for part in re.split(r"[@&\n\r,;\s]+", raw) if part]
    return list(dict.fromkeys(token.strip().strip("\"'") for token in tokens))


def load_accounts():
    return [Account(token=token) for token in parse_tokens(os.environ.get(ENV_TOKEN))]


# ---------- 当天提交记录 ----------
def pick_state_path():
    """状态文件优先放脚本同目录，不可写时退回系统临时目录。"""
    for folder in (os.path.dirname(os.path.abspath(__file__)), tempfile.gettempdir()):
        if os.access(folder, os.W_OK):
            return os.path.join(folder, "kia_state.json")
    return ""


STATE_FILE = pick_state_path()


class State:
    """记录各账号当天已提交的评论 / 文章（审核期间任务不会变成已完成），跨天自动清空。"""

    def __init__(self, path):
        self.path = path
        self.day = today()
        self.data = {}
        if path:
            try:
                with open(path, encoding="utf-8") as file:
                    saved = json.load(file)
            except (OSError, ValueError):
                saved = {}
            self.data = {uid: marks for uid, marks in as_dict(saved).items()
                         if as_dict(marks).get("date") == self.day}

    def done(self, uid, key):
        return bool(as_dict(self.data.get(uid)).get(key))

    def mark(self, uid, key):
        self.data.setdefault(uid, {"date": self.day})[key] = True
        if not self.path:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump(self.data, file, ensure_ascii=False)
        except OSError:
            self.path = ""


# ---------- 接口 ----------
class ApiError(Exception):
    def __init__(self, path, message, code=None):
        super().__init__(message)
        self.path = path
        self.code = code


class Kia:
    def __init__(self, token=""):
        self.token = token
        self._feed = {}

    def token_hash(self):
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:16]

    def _headers(self, path, params, auth):
        nonce = str(uuid.uuid4())
        timestamp = str(int(time.time() * 1000))
        headers = {
            "clientId": CLIENT_ID,
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": make_sign(path, params, timestamp, nonce),
            "version": APP_VERSION,
            "channel": CHANNEL,
            "platform": PLATFORM,
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": USER_AGENT,
        }
        if auth and self.token:
            headers["Authorization"] = self.token
        return headers

    def request(self, method, path, params=None, body=None, auth=True):
        """GET 的 params 用原值签名、编码后拼 URL；POST 的 body 不参与签名。"""
        url = BASE_URL + path
        sign_params = None
        if params:
            sign_params = {k: str(v) for k, v in params.items()}
            url += "?" + "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in sign_params.items())
        data = json.dumps(body if body is not None else {}).encode("utf-8") if method == "POST" else None
        for attempt in (1, 2):
            time.sleep(random.uniform(0.4, 1.0))
            req = urllib.request.Request(url, data=data, headers=self._headers(path, sign_params, auth), method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return self._unwrap(path, resp.status, resp.read())
            except urllib.error.HTTPError as exc:
                return self._unwrap(path, exc.code, exc.read())
            except OSError as exc:          # 含 URLError / 超时 / 连接重置
                if method != "GET" or attempt == 2:
                    raise ApiError(path, f"网络异常：{getattr(exc, 'reason', None) or exc}") from None
                time.sleep(3)

    @staticmethod
    def _unwrap(path, status, raw):
        text = raw.decode("utf-8", "replace")
        try:
            data = json.loads(text)
        except ValueError:
            snippet = re.sub(r"\s+", " ", text)[:120] or "（空）"
            raise ApiError(path, f"HTTP {status} 非 JSON 响应：{snippet}") from None
        if not isinstance(data, dict):
            return {"data": data}
        code = data.get("resultCode")
        if code is not None and str(code) != "200":
            message = data.get("message") or data.get("errMsg") or ""
            raise ApiError(path, f"resultCode {code}：{message}", code=str(code))
        code = data.get("code")
        if code is not None and str(code) not in ("200", "0", "2000"):
            message = data.get("msg") or data.get("message") or ""
            raise ApiError(path, f"业务码 {code}：{message}", code=str(code))
        return data

    # --- 用户 ---
    def user_info(self):
        return as_dict(self.request("GET", "/api/customer/userInfo/getCurrentUser").get("data"))

    def user_point(self):
        return as_dict(self.request("GET", "/api/customer/task/getUserPoint").get("data"))

    # --- 签到 ---
    def signed_on(self, day):
        calendar = self.request("GET", "/api/customer/signIn/querySignInDetail",
                                {"viewTime": now_text()}).get("data")
        for item in calendar if isinstance(calendar, list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("createdAt") or "").startswith(day):
                return str(item.get("isSignIn")) == SIGNED
        return False

    def sign_in(self, day):
        return as_dict(self.request("GET", "/api/customer/signIn/signIn", {"signDate": day}).get("data"))

    # --- 任务 ---
    def task_summary(self):
        rows = []
        for task_type in TASK_TYPES:
            data = self.request("POST", "/api/customer/task/getGrowthPointTask",
                                body={"taskType": task_type, "page": 1, "size": 50}).get("data")
            rows.extend(item for item in (data if isinstance(data, list) else []) if isinstance(item, dict))
        done = sum(1 for row in rows if row.get("isComplete"))
        undone = {row.get("taskName") for row in rows if not row.get("isComplete")}
        return {"total": len(rows), "done": done, "undone": undone}

    # --- 社区 ---
    def feed_articles(self, page=1):
        if page not in self._feed:
            data = as_dict(self.request(
                "GET", "/api/social/Circle/queryFeedToHome",
                {"currentPage": page, "pageSize": 10, "time": now_text()}).get("data"))
            articles = []
            for record in as_dict(data.get("standardPropertyDTOIPage")).get("records") or []:
                for art in (record.get("articleList") if isinstance(record, dict) else None) or []:
                    count = (art.get("allCountList") or [{}])[0]
                    articles.append({
                        "id": art.get("id"),
                        "type": count.get("objectType") or art.get("objType"),
                        "author": art.get("createdBy"),
                        "liked": bool(art.get("isLike")),
                        "focused": bool(art.get("isFocus")),
                        "title": art.get("title") or "",
                    })
            self._feed[page] = [art for art in articles if art["id"]]
        return self._feed[page]

    def pick_unliked(self):
        """依次翻最多 3 页，找第一条还没赞过的内容。"""
        for page in (1, 2, 3):
            articles = self.feed_articles(page)
            for art in articles:
                if not art["liked"]:
                    return art
            if not articles:
                break
        return None

    def like(self, obj_id, obj_type):
        return self.request("POST", "/api/social/CommonQuery/saveLike",
                            body={"objId": obj_id, "likeType": obj_type, "isValid": RELATION_ON})

    def follow(self, user_id, target_id):
        return self.request("POST", "/api/social/CommonQuery/saveFocus",
                            body={"userId": user_id, "focusUserId": target_id, "isValid": RELATION_ON})

    def forward_reward(self):
        return self.request("POST", "/api/social/CommonQuery/sendForwardRewardToMember", body={})

    def comment(self, obj_id, obj_type, content):
        return self.request("POST", "/api/social/CommonQuery/saveComment", body={
            "commentLevel": 80681001,
            "objId": str(obj_id),
            "commentType": str(obj_type),
            "prentUserId": None,
            "prentUserName": None,
            "content": content,
        })

    def first_info_id(self):
        """取一条官方资讯 id：分类 → 二级分类 → 列表首条。"""
        types = self.request("GET", "/api/social/InformationType/queryInformationType").get("data")
        for cat in types if isinstance(types, list) else []:
            for sub in (cat.get("secondTypeList") if isinstance(cat, dict) else None) or []:
                data = as_dict(self.request(
                    "GET", "/api/social/InformationType/queryInformationDetail",
                    {"currentPage": 1, "pageSize": 5, "classifi2Id": sub.get("id") if isinstance(sub, dict) else sub}).get("data"))
                records = data.get("records") or []
                if records and isinstance(records[0], dict) and records[0].get("id") is not None:
                    return str(records[0]["id"])
        return None

    def read_info(self, info_id):
        return self.request("GET", "/api/social/InformationType/queryInformation", {"id": info_id})

    def circle_list(self):
        data = as_dict(self.request("GET", "/api/social/Circle/queryCircle",
                                    {"currentPage": 1, "pageSize": 5}).get("data"))
        return [c for c in data.get("records") or [] if isinstance(c, dict)]

    def circle_status(self, circle_id):
        data = self.request("GET", "/api/social/Circle/queryCircleDetail", {
            "currentPage": 1, "pageSize": 10, "circleId": circle_id, "time": now_text()}).get("data")
        return as_dict(data[0] if isinstance(data, list) and data else {}).get("joinStatus")

    def join_circle(self, circle_id):
        return self.request("POST", "/api/social/Circle/applyInCircle",
                            body={"circleId": circle_id, "memberStatus": 80311001, "joinReason": JOIN_REASON})

    def write_article(self, circle_id, circle_name, title, text):
        return self.request("POST", "/api/social/Circle/writeArticle", body={
            "id": None,
            "attachmentList": [],
            "circleId": circle_id,
            "circleName": circle_name,
            "content": f"<p>{text}</p>",
            "jsonContent": [{"type": "text", "content": text}],
            "dynamicsAtopicDTOList": [],
            "title": title,
            "publishStatus": 80211003,
            "city": None,
            "activityId": None,
        })


# ---------- 每日流程 ----------
class Reporter:
    """一行日志：实时打印，同时留档用于汇总通知。"""

    def __init__(self):
        self.lines = []

    def __call__(self, icon, text):
        line = f"{icon} {text}"
        log(line)
        self.lines.append(line)


def do_tasks(kia, me, todo, state, post_on, say):
    """按未完成任务逐项处理；每一步互不影响，失败只记录不中断。"""
    uid = str(me.get("userId") or kia.token_hash())
    need_feed = bool(todo & {"点赞", "首次点赞", "首次关注好友", "首次评论", "评论"})
    first, feed_error = None, None
    if need_feed:
        try:
            first = (kia.feed_articles(1) or [None])[0]
        except ApiError as exc:
            feed_error = str(exc)
    if feed_error:
        say("⚠️", f"获取社区动态失败，依赖它的任务（点赞 / 关注 / 评论）跳过：{feed_error}")
    elif need_feed and first is None:
        say("⚠️", "未取到社区内容，依赖它的任务（点赞 / 关注 / 评论）跳过")
    no_feed = feed_error is not None or (need_feed and first is None)

    if ("点赞" in todo or "首次点赞" in todo) and not no_feed:
        try:
            target = kia.pick_unliked()
            if not target:
                say("👍", "推荐的内容都已赞过，跳过")
            else:
                kia.like(target["id"], target["type"])
                say("👍", f"点赞成功：《{brief(target['title'])}》")
        except ApiError as exc:
            say("⚠️", f"点赞失败：{exc}")

    if "首次关注好友" in todo and first:
        if first["focused"]:
            say("🤝", "关注：已经关注过作者，跳过")
        elif first["author"]:
            try:
                kia.follow(me.get("userId"), first["author"])
                say("🤝", f"关注成功：用户 {mask(first['author'])}")
            except ApiError as exc:
                say("⚠️", f"关注失败：{exc}")

    if "首次转发" in todo:
        try:
            kia.forward_reward()
            say("🔁", "转发任务完成")
        except ApiError as exc:
            say("⚠️", f"转发任务失败：{exc}")

    if "首次评论" in todo or "评论" in todo:
        if not post_on:
            say("💬", f"评论：已关闭（{ENV_POST}=false）")
        elif first:
            if state.done(uid, "comment"):
                say("💬", "评论：今天已提交，等待审核")
            else:
                try:
                    kia.comment(first["id"], first["type"], COMMENT_TEXT)
                    state.mark(uid, "comment")
                    say("💬", "评论已提交，审核通过后计分")
                except ApiError as exc:
                    say("⚠️", f"评论失败：{exc}")

    if "阅读官方资讯/文章" in todo:
        try:
            info_id = kia.first_info_id()
            if info_id:
                kia.read_info(info_id)
                say("📖", f"阅读资讯完成（id={info_id}）")
            else:
                say("📖", "没有取到资讯内容")
        except ApiError as exc:
            say("⚠️", f"阅读资讯失败：{exc}")

    if "首次加入圈子" in todo:
        try:
            circles = kia.circle_list()
            target = next((c for c in circles if not c.get("isIn")), circles[0] if circles else None)
            if not target:
                say("🎪", "加入圈子：没有可加入的圈子")
            else:
                circle_id = target.get("id")
                name = target.get("circleName") or target.get("name") or circle_id
                status = kia.circle_status(circle_id)
                if status and status != "加入":
                    say("🎪", f"加入圈子：{name}（{status}），跳过")
                else:
                    kia.join_circle(circle_id)
                    say("🎪", f"已申请加入圈子：{name}")
        except Exception as exc:  # noqa: BLE001  任何错误（含返回结构异常）只记录，不中断后续任务
            say("⚠️", f"加入圈子失败：{exc}")

    if "首次发布文章" in todo or "发布文章" in todo:
        if not post_on:
            say("✍️", f"发文章：已关闭（{ENV_POST}=false）")
        elif state.done(uid, "article"):
            say("✍️", "发文章：今天已提交，等待审核")
        else:
            try:
                circles = kia.circle_list()
                if not circles:
                    say("✍️", "发文章：没有可用的圈子")
                else:
                    kia.write_article(circles[0].get("id"), circles[0].get("circleName") or circles[0].get("name"),
                                      ARTICLE_TITLE, ARTICLE_TEXT)
                    state.mark(uid, "article")
                    say("✍️", "文章已提交，审核通过后计分")
            except ApiError as exc:
                say("⚠️", f"发文章失败：{exc}")


def run_account(account, state, post_on, say):
    kia = Kia(account.token)
    me = kia.user_info()         # token 失效时这里抛出 40106
    say("🔑", f"登录有效：{me.get('nickname') or '未知昵称'}")

    day = today()
    if str(me.get("isSign")) == SIGNED or (me.get("isSign") is None and kia.signed_on(day)):
        say("📅", "签到：今天已经签过啦")
    else:
        try:
            result = kia.sign_in(day)
            text = "🎉 签到成功"
            if result.get("points") is not None:
                text += f"，+{result['points']} 积分"
            if result.get("signDays") is not None:
                text += f"，已连续签到 {result['signDays']} 天"
            say("📅", text)
        except ApiError as exc:
            say("📅", f"签到失败：{exc}")

    before = kia.task_summary()
    say("📋", f"任务进度：{before['done']}/{before['total']}")
    do_tasks(kia, me, before["undone"], state, post_on, say)
    after = kia.task_summary()
    say("🏁", f"任务进度：{before['done']} → {after['done']}（共 {after['total']} 项）")

    try:
        point = kia.user_point()
        say("💰", f"当前积分：{point.get('pointValue')}（即将过期 {point.get('expiringPointSum', 0)}）")
    except ApiError as exc:
        say("⚠️", f"积分查询失败：{exc}")


def send_notify(title, content):
    """调用青龙自带 notify.py；找不到时静默跳过。"""
    for folder in ("/ql/data/scripts", "/ql/scripts", os.path.dirname(os.path.abspath(__file__))):
        if not os.path.isfile(os.path.join(folder, "notify.py")):
            continue
        try:
            if folder not in sys.path:
                sys.path.insert(0, folder)
            from notify import send
            send(title, content)
        except Exception as exc:  # noqa: BLE001
            log(f"⚠️ 通知发送失败：{exc}")
        return


def main():
    accounts = load_accounts()
    if not accounts:
        log(f"❌ 未配置账号：请在环境变量中添加 {ENV_TOKEN}")
        return 1
    post_on = os.environ.get(ENV_POST, "").strip().lower() not in ("0", "false", "off", "no")
    try:
        delay = max(0, min(int(os.environ.get(ENV_DELAY, "3")), 120))
    except ValueError:
        delay = 3
    state = State(STATE_FILE)

    log(f"🚗 起亚Kia 开始执行，共 {len(accounts)} 个账号")
    reports, ok = [], True
    for index, account in enumerate(accounts, 1):
        log(f"\n{'━' * 12} 🚗 账号 {index} {'━' * 12}")
        say = Reporter()
        try:
            run_account(account, state, post_on, say)
        except ApiError as exc:
            ok = False
            if exc.code in TOKEN_EXPIRED:
                say("🔑", f"登录已失效，请重新获取 token（{mask(account.token)}）")
            else:
                say("❌", f"执行中断：{exc}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            say("❌", f"执行异常：{exc}")
        ok = ok and not any(line.startswith(("❌", "⚠️")) for line in say.lines)
        reports.append(f"🚗 账号 {index}\n" + "\n".join(say.lines))
        if index < len(accounts):
            time.sleep(delay)

    summary = f"\n\n{'━' * 20}\n🎉 全部账号执行完毕"
    log(summary)
    send_notify(f"起亚Kia 签到 {'✅ 全部成功' if ok else '⚠️ 有异常'}", "\n\n".join(reports) + summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
