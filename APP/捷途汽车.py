"""
捷途汽车
cron: 30 8 * * *
new Env('捷途汽车');

依赖：requests、pycryptodome
JT_TOKEN          必填，accessToken
JT_DRYRUN=1       只查询，不执行业务写操作
JT_PROFILE=0      禁用新手资料流程；默认保留真实资料并尝试一次
JT_PROFILE_FILE   可选，用户提供的真实资料 JSON
JT_POST_FILE      图文发布队列；默认同目录 daily_posts.json
JT_SHARE_FILE     当天实际分享记录；默认同目录 share_receipts.json

自动签到、真实阅读；按配置发布图文、处理实际分享、保存真实资料。
审核、车主认证、购物等奖励按真实业务条件展示。

@auth: xiaohai
@data: 2026.11.26
"""

import base64
import hashlib
import json
import mimetypes
import os
import random
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote

try:
    import requests
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError:
    print("缺少依赖：pip install requests pycryptodome")
    sys.exit(1)

VERSION = "2026.11.26"
TOKEN = os.environ.get("JT_TOKEN", "").strip()
DRYRUN = os.environ.get("JT_DRYRUN", "0").strip() == "1"
BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / "jetour_ql_cache.json"
RESULT_FILE = BASE_DIR / "jetour_ql_result.json"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / f"jetour_{time.strftime('%Y%m%d_%H%M%S')}.log"
BUSINESS_TZ = timezone(timedelta(hours=8))
BFF = "https://mobile-consumer.jetour.com.cn"
KEY = base64.b64decode("vVfnp9ozfDQyonMKuqgZUWjtdV+7PtBqtMCwJqz2HKQ=")
HEADERS = {"User-Agent": "Dart/3.10 (dart:io)",
           "content-type": "application/json; charset=UTF-8",
           "agent": "android", "encryptflag": "true"}


def today():
    return datetime.now(BUSINESS_TZ).date().isoformat()


def log(msg, only_file=False):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    if not only_file:
        print(line, flush=True)


def safe_error(error):
    message = str(error)
    if TOKEN:
        message = message.replace(TOKEN, "[TOKEN]")
    return re.sub(r"https?://\S+", "[URL]", message)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def load_cache():
    path = Path(CACHE_FILE)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("缓存根节点必须是对象")
        return value
    except (OSError, ValueError) as error:
        raise RuntimeError("状态缓存无法读取，停止执行以免重复发布；请先检查缓存") from error


def save_cache(value):
    atomic_json(CACHE_FILE, value)


def load_state(c):
    return load_cache().get("accounts", {}).get(c.account_key, {})


def save_state(c, state):
    cache = load_cache()
    cache.setdefault("accounts", {})[c.account_key] = state
    save_cache(cache)


@contextmanager
def run_lock():
    """使用操作系统锁；进程结束自动释放，锁文件保留以免 inode 竞争。"""
    path = Path(str(CACHE_FILE) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("另一个捷途任务正在运行，请等待其结束") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def enc(plaintext):
    if isinstance(plaintext, dict):
        plaintext = json.dumps(plaintext, ensure_ascii=False, separators=(",", ":"))
    raw = plaintext.encode() if isinstance(plaintext, str) else plaintext
    iv = os.urandom(16)
    ciphertext = AES.new(KEY, AES.MODE_CBC, iv).encrypt(pad(raw, 16))
    return base64.b64encode(iv + ciphertext).decode().replace("+", "-")


def qs(params, token):
    parts = [f"{key}={'' if value is None else value}" for key, value in params.items()
             if key not in ("access_token", "terminal")]
    parts += [f"access_token={token}", f"terminal={params.get('terminal', 3)}"]
    return "&".join(parts)


class Client:
    def __init__(self, token):
        self.token = token
        self.account_key = ""
        self.http = requests.Session()
        self.http.headers.update(HEADERS)
        self._ts = 0.0

    def _wait(self):
        gap = 1.2 + random.uniform(0, 0.6)
        now = time.monotonic()
        if now - self._ts < gap:
            time.sleep(gap - (now - self._ts))
        self._ts = time.monotonic()

    def _retry(self, fn, tries=3):
        for index in range(tries):
            try:
                return fn()
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                if index == tries - 1:
                    raise
                delay = 5 * (index + 1)
                log(f"网络异常，{delay}s 后重试（{index + 1}/{tries}）")
                time.sleep(delay)

    def _parse(self, response):
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        data = response.json()
        if isinstance(data, dict) and data.get("status") == 200:
            return data
        if not isinstance(data, dict):
            raise RuntimeError("接口返回了未知格式")
        raise RuntimeError(f"[{data.get('error')}] {data.get('message')}")

    def _url(self, path, params=None):
        return f"{BFF}{path}?encryptParam={quote(enc(qs(params or {}, self.token)), safe='')}"

    def get(self, path, params=None):
        self._wait()
        return self._retry(lambda: self._parse(self.http.get(self._url(path, params), timeout=20)))

    def post(self, path, body, token_in_query=True, retry=True):
        self._wait()
        url = self._url(path) if token_in_query else BFF + path
        payload = dict(body)
        payload.setdefault("access_token", self.token)
        payload.setdefault("terminal", 3)
        return self._retry(lambda: self._parse(self.http.post(
            url, data=enc(payload).encode(), timeout=30)), tries=3 if retry else 1)

    def upload_image(self, path):
        self._wait()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as stream:
            response = self.http.post(self._url("/web/community/common/files/images"),
                                      files={"file": (path.name, stream, mime)},
                                      headers={"Content-Type": None}, timeout=60)
        url = image_url(self._parse(response).get("data"))
        if not url:
            raise RuntimeError("图片上传响应缺少可识别的 URL，尚未发布")
        return url


def page_rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    return []


def task_key(task):
    return str(task.get("taskId") or task.get("taskCode") or task.get("taskName"))


def get_tasks(c):
    tasks = []
    seen = set()
    for group in (1, 2):
        for page in range(20):
            rows = page_rows(c.get("/web/taskCenter/task/userTaskDetails",
                                   {"type": group, "page": page, "size": 50, "terminal": 4}).get("data"))
            fresh = [dict(task, _group="新手" if group == 1 else "每日")
                     for task in rows if task_key(task) not in seen]
            tasks.extend(fresh)
            seen.update(task_key(task) for task in fresh)
            if len(rows) < 50 or not fresh:
                break
    log(f"任务清单 {len(tasks)} 项")
    return tasks


def snapshot_status(c, tasks):
    return {task_key(task): task.get("status") for task in tasks}


def pending_task(tasks, code=None, event=None):
    return next((task for task in tasks if task.get("status") == 0
                 and (code is None or task.get("taskCode") == code)
                 and (event is None or any(rule.get("eventCode") == event
                                          for rule in task.get("taskEventRules") or []))), None)


def show_user(c):
    user = c.get("/web/user/current").get("data") or {}
    account_id = user.get("accountId")
    if not account_id:
        raise RuntimeError("当前账号响应缺少 accountId，无法隔离任务状态")
    c.account_key = hashlib.sha256(str(account_id).encode()).hexdigest()[:24]
    balance = c.get("/web/point/user/balance/detail").get("data") or {}
    member = c.get("/web/member/consumer/detail").get("data") or {}
    card = (member.get("cardAccountList") or [{}])[0]
    log(f"账号 {user.get('displayName')} | 捷途币 {balance.get('balance')} | "
        f"会员 {card.get('levelName')} | 旅行值 {member.get('growth')}")
    return {"balance": balance.get("balance"), "user": user, "member": member}


def sign_in(c):
    if DRYRUN:
        log("DRYRUN：跳过签到")
        return
    scene = c.get("/web/task/tasks/load-one", {"sceneCode": "signInScene", "terminal": 4}).get("data") or {}
    task_id = (scene.get("taskInfo") or {}).get("id")
    if task_id:
        day = today()
        record = c.get("/web/task/sign/sign-record", {
            "taskId": task_id, "monthInYear": int(day[:7].replace("-", ""))}).get("data") or {}
        bitmap = record.get("signRecord") or ""
        index = int(day[-2:]) - 1
        if isinstance(bitmap, str) and len(bitmap) > index and bitmap[index] == "1":
            log("今日已签到")
            return
    c.post("/web/task/tasks/event-start", {"eventCode": "SJ50001"})
    log("签到事件已受理，奖励以下方回查为准")


def browse_content(c, tasks):
    candidates = [(task, rule) for task in tasks if task.get("status") == 0
                  for rule in task.get("taskEventRules") or [] if rule.get("eventCode") == "SJ50006"]
    if not candidates:
        log("浏览任务已完成或未开放")
        return
    if DRYRUN:
        log("DRYRUN：跳过内容浏览")
        return
    _, rule = candidates[0]
    required = max([15] + [int(prop.get("value") or 15) for prop in rule.get("properties") or []
                          if prop.get("code") == "content_duration"])
    if required > 300:
        raise RuntimeError(f"浏览条件已变化（{required}s），请核对规则")
    feed = c.get("/web/community/square/contents", {"pageSize": 10}).get("data")
    for post in page_rows(feed):
        content_id = str(post.get("contentId") or post.get("id") or "")
        if not content_id:
            continue
        detail = c.get(f"/web/community/contents/{quote(content_id, safe='')}",
                       {"pvFlag": "true", "outApp": 1}).get("data") or {}
        if not detail.get("detail"):
            continue
        log(f"阅读《{detail.get('title') or post.get('title') or content_id}》，至少 {required}s")
        started = time.monotonic()
        time.sleep(required + 1)
        elapsed = int(time.monotonic() - started)
        if elapsed < required:
            raise RuntimeError("实际阅读时长不足，未提交事件")
        c.post("/web/event/event-instances", {"eventCode": "SJ50006", "properties": {
            "content_duration": elapsed, "content_id": content_id}})
        log(f"真实阅读记录已受理（{elapsed}s），奖励以下方回查为准")
        return
    log("暂无可读取的有效内容，未提交浏览事件")


def option_file(variable, default=None):
    configured = os.environ.get(variable, "").strip()
    if not configured and not default:
        return None
    path = Path(configured or default)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists() and not configured:
        return None
    return path


def read_entries(path, key):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = value.get(key) if isinstance(value, dict) else value
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise ValueError(f"{path.name} 必须包含 {key} 数组")
    return entries


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def complete_profile(c, tasks, state, user):
    task = pending_task(tasks, code="xsrw", event="SJ50003")
    if not task or os.environ.get("JT_PROFILE", "1").strip() == "0":
        log("新手资料流程：已完成、未开放或已禁用")
        return
    if DRYRUN:
        log("DRYRUN：跳过资料保存与完善事件")
        return
    details = c.get("/web/user/current/details").get("data") or {}
    fields = {"avatarUrl", "birthday", "displayName", "gender", "provinceCode", "cityCode"}
    current = dict(user, **details)
    body = {key: current[key] for key in fields if key in current}
    path = option_file("JT_PROFILE_FILE")
    if path:
        configured = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(configured, dict) or set(configured) - fields:
            raise ValueError("资料配置只允许 avatarUrl/birthday/displayName/gender/provinceCode/cityCode")
        body.update(configured)
    if not body.get("displayName") or not body.get("avatarUrl"):
        log("缺少真实昵称或头像，请提供真实资料后再执行新手流程")
        return
    key = fingerprint({"task": task_key(task), "profile": body})
    attempts = state.setdefault("profile_attempts", {})
    if key in attempts:
        log("本任务与这份资料已尝试保存和上报；新手奖励仍以资格及旅行值回查为准")
        return
    c.post("/web/user/update", body)
    saved = c.get("/web/user/current").get("data") or {}
    if str(saved.get("accountId")) != str(user.get("accountId")):
        raise RuntimeError("资料回查的账号不一致，未提交完善事件")
    if saved.get("displayName") != body["displayName"] or saved.get("avatarUrl") != body["avatarUrl"]:
        raise RuntimeError("资料回查未匹配保存的昵称与头像，未提交完善事件")
    verified = c.get("/web/user/current/details").get("data") or {}
    values = dict(saved, **verified)
    area = None
    if values.get("provinceName") is not None or values.get("cityName") is not None:
        area = str(values.get("provinceName") or "") + str(values.get("cityName") or "")
    degree = int((2 + sum(value is not None for value in (
        values.get("avatarUrl"), values.get("birthday"), values.get("displayName"), area))) / 6 * 100)
    attempts[key] = {"date": today(), "status": "reporting"}
    save_state(c, state)
    c.post("/web/event/event-instances", {"eventCode": "SJ50003", "properties": {
        "infoperfect_userid": saved["accountId"], "infoperfect_username": saved["displayName"],
        "infoperfect_time": int(time.time() * 1000), "infoperfect_degree": f"{degree}%"}}, retry=False)
    attempts[key]["status"] = "accepted"
    save_state(c, state)
    log(f"真实资料已保存，完善事件已受理（客户端完整度 {degree}%）；未补报注册，奖励待回查")


def share_content(c, tasks, state):
    if not pending_task(tasks, event="SJ50005"):
        log("分享任务已完成或未开放")
        return
    if DRYRUN:
        log("DRYRUN：跳过分享记录处理")
        return
    path = option_file("JT_SHARE_FILE", "share_receipts.json")
    entries = read_entries(path, "shares") if path else []
    active = [item for item in entries if item.get("date") == today() and item.get("confirmed") is True]
    if not active:
        log("分享任务：需要当天已实际完成的微信/朋友圈分享记录，当前未配置")
        return
    receipt = active[0]
    content_id = str(receipt.get("content_id") or "").strip()
    if not content_id or receipt.get("channel") not in ("wechat", "moments"):
        raise ValueError("分享记录必须包含真实 content_id，channel 为 wechat 或 moments")
    reports = state.setdefault("shares", {})
    if today() in reports:
        log("今天的分享记录已提交或结果待核对，不重复上报")
        return
    detail = c.get(f"/web/community/contents/{quote(content_id, safe='')}").get("data") or {}
    if not detail.get("detail"):
        raise RuntimeError("分享内容无法读取，未提交事件")
    day = today()
    if receipt["date"] != day:
        raise RuntimeError("执行期间已跨日，请提供新日期的真实分享记录")
    reports[day] = {"content_id": content_id, "status": "reporting"}
    save_state(c, state)
    c.post("/web/event/event-instances", {"eventCode": "SJ50005", "properties": {
        "content_id": content_id}}, retry=False)
    reports[day]["status"] = "accepted"
    save_state(c, state)
    log("当天实际分享的事件已受理，是否完成以下方任务与流水为准")


def image_url(data):
    if isinstance(data, str) and data.startswith(("https://", "http://")):
        return data
    if isinstance(data, list):
        return next((value for item in data if (value := image_url(item))), None)
    if isinstance(data, dict):
        for key in ("url", "fileUrl", "imageUrl", "accessUrl", "downloadUrl", "filePath", "src", "path", "files", "data"):
            value = image_url(data.get(key))
            if value:
                return value
    return None


def validate_post(entry, directory, task):
    title = entry.get("title")
    detail = entry.get("detail")
    images = entry.get("images")
    if not isinstance(title, str) or not title.strip() or not isinstance(detail, str):
        raise ValueError("发帖队列必须填写标题 title 和正文 detail")
    rule = next((rule for rule in task.get("taskEventRules") or []
                 if rule.get("eventCode") == "SJ20008"), None)
    if not rule:
        raise ValueError("当前首帖任务未提供已验证的图文规则，停止发布")
    minimum = {"content_num": 30, "pic_num": 1}
    for prop in rule.get("properties") or []:
        code = prop.get("code")
        if code not in minimum or prop.get("symbol") != ">=":
            raise ValueError("当前首帖图文条件已变化，请核对服务端规则")
        minimum[code] = max(minimum[code], int(prop["value"]))
    text = re.sub(r"\s+", "", detail)
    if len(text) < minimum["content_num"]:
        raise ValueError(f"首帖正文不足 {minimum['content_num']} 字")
    if not isinstance(images, list) or len(images) < minimum["pic_num"]:
        raise ValueError(f"首帖至少需要 {minimum['pic_num']} 张真实配图")
    sources, digests = [], []
    for image in images:
        if not isinstance(image, str) or not image.strip():
            raise ValueError("图片必须是本地文件路径或已上传图片的 HTTPS URL")
        if image.startswith("https://"):
            sources.append(image)
            digests.append(image)
            continue
        if "://" in image:
            raise ValueError("远程图片只支持 HTTPS URL")
        path = Path(image)
        if not path.is_absolute():
            path = directory / path
        if not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            raise ValueError(f"配图文件不存在或格式不支持：{path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        sources.append(path)
        digests.append(digest.hexdigest())
    digest = fingerprint({"title": title, "detail": detail, "images": digests})
    return {"title": title, "detail": detail, "contentType": 12}, sources, digest


def check_post(c, record):
    content_id = record["content_id"]
    detail = c.get(f"/web/community/contents/{quote(str(content_id), safe='')}").get("data") or {}
    log(f"已发布帖子 {content_id} | approvalStatus={detail.get('approvalStatus')} "
        f"approvalLevel={detail.get('approvalLevel')}；首帖及评级奖励以任务和流水为准")


def publish_daily_post(c, tasks, state):
    day = today()
    posts = state.setdefault("posts", {})
    record = posts.get(day)
    if record and record.get("content_id"):
        check_post(c, record)
        return
    if record and record.get("status") in ("publishing", "uncertain"):
        raise RuntimeError("今天的发帖请求结果未知，请先在 App 核对并修正发布状态，禁止自动重发")
    task = pending_task(tasks, code="meirishoutieutxo")
    if not task:
        log("每日首帖已完成、审核中或未开放")
        return
    if DRYRUN:
        log("DRYRUN：跳过图片上传和公开发帖")
        return
    path = option_file("JT_POST_FILE", "daily_posts.json")
    entries = read_entries(path, "posts") if path else []
    active = [item for item in entries if item.get("date") == day and item.get("approved") is True]
    if not active:
        log("每日首帖：未配置今天已确认正文与配图的发布条目")
        return
    if len(active) != 1:
        raise ValueError("同一天只能配置一条已确认的首帖，避免重复发布")
    body, sources, digest = validate_post(active[0], path.parent, task)
    if any(item.get("fingerprint") == digest and (item.get("content_id") or item.get("status") in ("publishing", "uncertain"))
           for item in posts.values()):
        raise RuntimeError("这份正文与配图已发布或发布结果未知，请使用新的真实内容")
    if not record or record.get("fingerprint") != digest:
        record = {"status": "prepared", "fingerprint": digest, "pictures": []}
        posts[day] = record
        save_state(c, state)
    pictures = record.setdefault("pictures", [])
    for source in sources[len(pictures):]:
        pictures.append(source if isinstance(source, str) else c.upload_image(source))
        save_state(c, state)
    if today() != day:
        raise RuntimeError("上传期间已跨日，未发布；请确认新日期的队列")
    body["pictureUrls"] = list(pictures)
    record["status"] = "publishing"
    save_state(c, state)
    try:
        response = c.post("/web/community/contents", body, retry=False).get("data") or {}
        content_id = response.get("contentId") if isinstance(response, dict) else None
        if not content_id:
            raise RuntimeError("发布响应缺少 contentId")
    except Exception:
        record["status"] = "uncertain"
        save_state(c, state)
        raise
    record.update({"status": "published", "content_id": str(content_id),
                   "published_at": datetime.now(BUSINESS_TZ).isoformat()})
    save_state(c, state)
    log(f"图文已发布：{content_id}，等待审核及首帖奖励回查")
    check_post(c, record)


def flow_key(row):
    business = row.get("businessInfo") or {}
    return str(row.get("id") or business.get("businessNo") or fingerprint(row))


def point_flow(c):
    return page_rows(c.get("/web/point/flow", {"page": 0, "size": 100}).get("data"))


def growth_flow(c, member):
    system_id = member.get("systemId")
    if system_id is None:
        raise RuntimeError("会员响应缺少 systemId，未猜测旅行值系统")
    data = c.get("/web/member/accountFlow", {"systemId": system_id, "page": 0, "size": 100}).get("data") or {}
    return page_rows(data.get("growthFlowPageDTO"))


def show_pending_tasks(tasks):
    requirements = {
        "xsrw": "注册及真实资料完善，并符合当前新手资格；500旅行值尚未验证到账",
        "renzheng2": "需真实车主认证", "quanyip": "需真实首购权益包",
        "meirishoutieutxo": "需当天已确认的图文队列并通过审核；至少30字+1图",
        "neirong2": "需当天实际完成微信/朋友圈分享，再提供分享记录",
        "liulan2o": "需按当前规则读取真实内容并停留",
        "youzhi1": "需运营评为优质帖", "jingping1": "需运营评为精品帖",
        "jinghua1": "需运营评为精华帖", "PLBZD2": "需评论被官方精选",
        "nrbtjli": "需内容被官方推荐", "xianjinpxiaofei": "需真实商城消费",
    }
    for task in tasks:
        name, status = task.get("taskName"), task.get("status")
        if status == 3:
            log(f"已完成：{name}")
        elif status == 4:
            log(f"审核中：{name}")
        elif status == 0:
            log(f"待完成：{name}；{requirements.get(task.get('taskCode'), '需满足当前服务端实际业务条件')}")
        else:
            log(f"任务：{name}，服务端状态={status}")


def verify_result(c, before_bal, before_status, before_flow_keys,
                  before_member=None, before_growth_keys=None):
    if not DRYRUN:
        time.sleep(4)
    balance = (c.get("/web/point/user/balance/detail").get("data") or {}).get("balance")
    tasks = get_tasks(c)
    flipped = [task.get("taskName") for task in tasks if task.get("status") == 3
               and before_status.get(task_key(task)) != 3]
    new_rows = [row for row in point_flow(c) if flow_key(row) not in before_flow_keys]
    new_flow = [{"name": (row.get("businessInfo") or {}).get("businessName"),
                 "point": row.get("streamPoint"), "remark": (row.get("businessInfo") or {}).get("remark")}
                for row in new_rows]
    log(f"结果：捷途币 {before_bal} → {balance} | 新完成 {len(flipped)} 项 {flipped or ''}")
    for row in new_flow:
        log(f"新增积分流水：{row['name']}，变动 {row['point']}，{row['remark'] or ''}")
    result = {"balance": balance, "flipped": flipped, "new_flow": new_flow, "tasks": tasks}
    if before_member is not None:
        member = c.get("/web/member/consumer/detail").get("data") or {}
        result.update({"growth_before": before_member.get("growth"), "growth": member.get("growth")})
        log(f"旅行值 {before_member.get('growth')} → {member.get('growth')}")
        try:
            after_growth = growth_flow(c, member)
            result["new_growth_flow"] = ([row for row in after_growth if flow_key(row) not in before_growth_keys]
                                         if before_growth_keys is not None else None)
            if before_growth_keys is not None:
                log(f"新增旅行值流水 {len(result['new_growth_flow'])} 条")
            else:
                log("旅行值基线未取得，未将历史流水计为本次到账")
        except Exception as error:
            result["growth_query_error"] = safe_error(error)
            log(f"旅行值流水回查失败：{safe_error(error)}")
    return result


def main():
    log(f"捷途汽车 · 青龙完整版 {VERSION} | {'DRYRUN（只读）' if DRYRUN else '执行任务'} | 北京日期 {today()}")
    if not TOKEN:
        log("未配置 JT_TOKEN，请将登录会话的 access_token 配置到青龙环境变量")
        return 1
    with run_lock():
        c = Client(TOKEN)
        try:
            info = show_user(c)
            tasks = get_tasks(c)
            state = load_state(c)
            before_status = snapshot_status(c, tasks)
            before_flow = {flow_key(row) for row in point_flow(c)}
            before_growth = None
            try:
                before_growth = {flow_key(row) for row in growth_flow(c, info["member"])}
            except Exception as error:
                log(f"旅行值基线查询失败：{safe_error(error)}")
            errors = []
            for name, action in (
                ("签到", lambda: sign_in(c)),
                ("浏览", lambda: browse_content(c, tasks)),
                ("新手资料", lambda: complete_profile(c, tasks, state, info["user"])),
                ("实际分享", lambda: share_content(c, tasks, state)),
                ("图文首帖", lambda: publish_daily_post(c, tasks, state)),
            ):
                try:
                    action()
                except Exception as error:
                    message = f"{name}：{safe_error(error)}"
                    errors.append(message)
                    log(f"执行失败：{message}")
            result = verify_result(c, info["balance"], before_status, before_flow, info["member"], before_growth)
            show_pending_tasks(result["tasks"])
            result["tasks"] = [{key: task.get(key) for key in ("taskId", "taskCode", "taskName", "status")}
                               for task in result["tasks"]]
            result.update({"version": VERSION, "date": today(), "dry_run": DRYRUN,
                           "balance_before": info["balance"], "errors": errors})
            atomic_json(RESULT_FILE, result)
            log(f"本次新增积分流水 {len(result['new_flow'])} 条，新完成任务 {len(result['flipped'])} 项，执行错误 {len(errors)} 项")
            log(f"结果：{RESULT_FILE.name} | 日志：{LOG_FILE.name}")
            return 1 if errors or result.get("growth_query_error") else 0
        finally:
            c.http.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断；若在发布中中断，请先核对已发布帖子和本地状态")
        sys.exit(130)
    except Exception as error:
        log(f"执行终止：{safe_error(error)}")
        sys.exit(1)
