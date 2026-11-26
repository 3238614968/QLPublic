"""
中国移动

调用链：
    App 票据(引导) -> /qwhdsso 登录 -> 账号级 JWT(缓存 30 天)
                                    -> 各活动页换 30min 会话 -> 业务接口
    ext(现签) -> coc User-Token ；coc3 用 ext + userLoginV2 换票

必填参数（写在下方 DEFAULTS，或用同名环境变量覆盖）：
    CMCC_PHONE           手机号（11 位）
    CMCC_APP_TOKEN       App 原生票据串 "JSESSIONID=..; UID=..; ticketID=.."
    CMCC_PROVINCE_CODE   省代码（371=河南）
    CMCC_CITY_CODE       会话真实属地市代码（0371=郑州）；须与 60012 续期校验一致

常用可选参数：
    CMCC_TASKS           任务白名单，all 或逗号分隔
    CMCC_DRY_RUN         1=只读，不提交任何写操作
    CMCC_DELAY           启动前随机延迟秒数（防风控）
    CMCC_GAP             基础请求间隔秒
    CMCC_AUTO_RENEW      1=启用 60012 免密续期（会轮换票据，仅单副本开启）
    CMCC_SECKILL         1=启用秒杀（单账号限次）
    CMCC_COC3_TOKEN / CMCC_COC3_REFRESH   coc3 权益市场回退票（可空）
    CMCC_EXT             YDRZ ext（留空则用 QWHD 会话现签）
    CMCC_CMIC_COOKIE     全球通 Cookie（留空则现签）
    CMCC_RISK_TOKEN / CMCC_WASM_PARAMS / CMCC_BSFIT_DFP / CMCC_SM_DEVICE_ID
                         coc3 与 139 云朵的风控指纹（缺失会触发风控拒绝）
    CMCC_SMS_CODE        139 领奖用一次性短信码（仅服务端开关=1 时需要）

任务清单（CMCC_TASKS 取值）：
    mark31      月月签到（签到 + 连签领奖）
    saturday    周六游戏中心打卡 + 游戏币抽奖
    video       追剧领福利签到
    aitasks     签到页 AI 豆任务（完成 + 领奖）
    coins       代币任务（周六/追剧/玩游戏）
    lottery     抽奖（窗口制：周六 12 点 / 周日）
    assess      评价有礼
    seckill     秒杀（签到门槛）
    hlw         福利营销（周三充值日，只读）
    coc3        coc3 权益市场任务浏览
    coc3draw    coc3 抽奖（0 元单）
    coc7        coc web6 七日打卡
    henan       河南本省签到抽奖 / 周二充值日
    cmic        全球通星动日
    gold        AI 豆顶格兑话费券
    seed        AI 豆苗养成记
    wed         周三充值日拼图闭环
    videoday    追剧每日视频任务
    fee         话费余额（原生只读）
    sweep       全量只读扫描

@auth: xiaohai
@data: 2026.10.1
"""
import base64
import json
import os
import random
import re
import secrets
import ssl
import sys
import time

# ===========================================================================
# 参数配置区（只改这里即可；同名环境变量优先）
# ===========================================================================
DEFAULTS = {
    # ---- 账号（必填）----
    # 手机号；CMCC_APP_TOKEN 为 App 原生票据，仅作 JWT 的引导/兜底
    'CMCC_PHONE': '',
    'CMCC_APP_TOKEN': (
        'JSESSIONID=; UID=; Comment=SessionServer-unity; Path=/;HTTPOnly; ticketID=NingBo'),

    # ---- coc3 权益市场（可留空：脚本用 ext + userLoginV2 自动换票）----
    'CMCC_COC3_TOKEN': '',
    'CMCC_COC3_REFRESH': '',

    # ---- YDRZ ext（留空则由 QWHD 会话现签）----
    'CMCC_EXT': '',

    # ---- 其它平台 Cookie（可留空）----
    'CMCC_CMIC_COOKIE': ('access_token=; '
                         'JSESSIONID='),
    'CMCC_COC_USER_TOKEN': '',

    # ---- 归属地 / 版本 ----
    'CMCC_PROVINCE_CODE': '371',      # 河南=371
    'CMCC_CITY_CODE': '0371',         # 郑州=0371（须与会话真实属地一致）
    'CMCC_CARRIER_OPERATOR': '002',
    'CMCC_APP_VERSION': '12.5.4',

    # ---- 行为开关 ----
    'CMCC_TASKS': 'all',              # all 或逗号分隔，取值见文件头任务清单
    'CMCC_WISH_INDEX': '0',           # 许愿活动选第 N 个奖品（0 起）
    'CMCC_FRIEND_INVITE': '',         # 好友邀请码
    'CMCC_ASSESS_EXCHANGE': '0',      # 评价币兑换：0=只攒 / auto=自动兑 / <prizeId>=指定档
    'CMCC_GOLD_EXCHANGE': '1',        # AI 豆兑话费券：1=顶格自动兑 / 0=只查余额
    'CMCC_LNLOTTERY': '0',            # 辽宁充值先领券抽奖（仅辽宁号）
    'CMCC_AUTO_RENEW': '1',           # 60012 免密续期（轮换票据，仅单副本开启）

    # ---- 秒杀 ----
    'CMCC_SECKILL': '1',              # 1=启用
    'CMCC_SECKILL_WAIT_MIN': '0',     # 下一场 N 分钟内开抢则等待
    'CMCC_SECKILL_MAX_TRIES': '3',    # 单场最大尝试次数

    'CMCC_DRY_RUN': '0',              # 1=只查询，不提交写操作
    'CMCC_DELAY': '0',                # 启动前随机延迟 0~N 秒
    'CMCC_NOTIFY': '1',
    'CMCC_GAP': '1.2',                # 基础请求间隔秒

    # ---- 风控指纹（缺失时相关提交会被风控拒绝）----
    'CMCC_RISK_TOKEN': '',            # coc3 抽奖头 gr-risk-p3
    'CMCC_WASM_PARAMS': '',           # coc3 抽奖头 wasmParams
    'CMCC_BSFIT_DFP': '',             # 数美设备 ID（lottery 体 operDfp）
    'CMCC_SM_DEVICE_ID': '',          # 数美 SMSdk 设备 ID（139 云朵领取/兑换必需）
    'CMCC_SMS_CODE': '',              # 139 领奖一次性短信码
    'CMCC_MCLOUD_NO_CACHE': '',       # 1=139 JWT 每次现签（默认 50 分钟缓存）
}

BASE = 'https://wx.10086.cn'
#: 各活动入口页（SSO 换票按页路由；jwt 账号级通用，换页不消耗 App 票据）
ACTS = {
    'mark31': {
        'name': '月月签到',
        'entry': BASE + '/qwhdhub/qwhdmark/1021122301?channelId=P00000109876',
    },
    'saturday': {
        'name': '周六游戏中心',
        'entry': BASE + '/qwhdhub/diy-client/1126086939?A_C_CODE=pXMkQhEmZ7&channelId=P00000119581',
        'sign_query': ('POST', '/qwhdhub/api/diySaturdayGame/getClockInStatus'),
        'sign_do': ('POST', '/qwhdhub/api/diySaturdayGame/clockIn'),
        'signed': lambda d: bool(d.get('signed')) or '已签到' in (d.get('message') or ''),
        'task_component': '50HQAg0-9YaSW4St7MDT',
        # 游戏币 10 币/次；周六 12:00 起奖池拓展；余额 31 天清零 ⇒ 攒到周六抽，未中即停
        'lottery': {'component': '50HQAg0-9YaSW4St7MDT', 'wallet_key': 'remainGameVal',
                    'cost': 10, 'count_key': 'remain', 'weekday': 6, 'start_hour': 12,
                    'label': '游戏币', 'stop_on_miss': True},
    },
    'video': {
        'name': '追剧领福利',
        'entry': BASE + '/qwhdhub/diy-client/1126082530?A_C_CODE=10hep1ZhkL&channelId=P00000119581',
        'sign_query': ('GET', '/qwhdhub/api/diyVideoDayRedesign/sign/querySignStatus'),
        'sign_do': ('GET', '/qwhdhub/api/diyVideoDayRedesign/sign/doSign'),
        'signed': lambda d: str(d.get('todaySignFlag')) == '1',
        'task_component': '4EC96l0-9btuobajC9Yu',
        # 次数 1 次/抽；仅周日开放且过期作废 ⇒ 未中也抽满
        'lottery': {'component': '4EC96l0-9btuobajC9Yu', 'wallet_key': 'remain',
                    'cost': 1, 'count_key': None, 'weekday': 7,
                    'label': '次数', 'stop_on_miss': False},
    },
    'game666': {
        'name': '玩游戏抽话费（周任务）',
        'entry': BASE + '/qwhdhub/diy-client/1126090514?A_C_CODE=aEvxYYAV3C&channelId=P00000119581',
        'task_component': '7M6vIq0-TFMj5T-ZGjks',
    },
    'hlw': {
        'name': '福利营销（周三充值日）',
        'entry': BASE + '/hlwyxhdhub/act-wedrecharge/index.html?pageId=1849008675699650560',
    },
}
#: 评价有礼入口（同一 SSO 通道）
ASSESS_ENTRY = (BASE + '/qwhdhub/assess/1024071116'
                '?A_C_CODE=mbQ0p2z44K&channelId=P00000054182&yx=1182211444')
#: coc web6「7 天打卡分百万」常量
COC_7DAY_ACTIVITY_ID = 2095709942208745472
COC_7DAY_TOUCH_CODE = 'P00000001090'
#: coc3 任务上报渠道码
COC3_CHANNEL_CODE = 'P00000008211'

#: 签到专属 AI 豆兑话费券：面额从高到低顶格兑（0.5 元档与加赠券不兑）
GOLD_TIERS = ('5元话费券', '2元话费券', '1.5元话费券', '1元话费券')
GOLD_MIN_STOCK = 100
GOLD_MAX_ROUNDS = 8
#: AI 豆苗养成记（activityId 17692；任务列表 55，签到任务 3115）
SEED_ACTIVITY_ID = 17692
SEED_TASK_LIST_ID = 55
SEED_SIGN_TASK_ID = 3115
SEED_CHANNEL = 'P00000011939'
SEED_WTAC = 'P00000008211_'
SEED_PAGE_KEY = '2026dmycj'
#: 周三充值日拼图闭环入口（带 channelId，SSO 按页换票）
WED_ENTRY = (BASE + '/hlwyxhdhub/act-wedrecharge/1024101716'
             '?pageId=1849008675699650560&channelId=P00000093619')
#: 拼图档位 → 集齐领取 round
WED_TIERS = {'dp1': '95折话费券', 'dp2': '9折话费券', 'dp3': '8折话费券'}
WED_PUZZLE_RECEIVE = {1: 'r6', 2: 'r5', 3: 'r4'}
WED_RECEIVE_LABELS = {'r6': '95折话费券', 'r5': '9折话费券', 'r4': '8折话费券'}
#: 任务黑名单制：除以下四类外全部自动尝试（服务端动态新增任务无需维护清单）
WED_MANUAL_SKIP = {
    'yqyh': '需邀请真实用户助力',
    'czhf': '需真实充值满 10 元（消费类不做）',
    'llym': '需 App 内真实浏览签到页',
    'dlhd': '进页自动完成',
}
CMLIVE_BASE = 'https://v.cmcc-cs.cn/cmlive/api'
#: 追剧视频站 sourceId（017027，视频站只认这个）
CMLIVE_SOURCE_ID = '017027'
CMLIVE_REFERER = ('https://v.cmcc-cs.cn/cmlive/live/feed?id=102518'
                  '&cmliveChannelId=203&belongChannelId=203&secondChannel=spr'
                  '&openSelfResearch=1&tagId=157&jtCode=1')
#: 辽宁「充值先领券」入口（仅辽宁移动实名号）
LN_ENTRY = BASE + '/qwhdhub/goldeneggs/1025093012?A_C_CODE=RLnnD8f2p6'
LN_BACK_URL = (BASE + '/qwhdhub/goldeneggs/1025093012'
               '?A_C_CODE=RLnnD8f2p6&channelId=P00000003143#/')


#: 原生层 UA（appTokenLogin 直连必需）
UA_NATIVE = 'leadeon/chinamobile/1.0'
#: H5 端 UA
UA = ('Mozilla/5.0 (Linux; Android 16; RMQ) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Version/4.0 Chrome/120.0.0.0 Mobile Safari/537.36 leadeon/12.5.4/CMCCIT')
APP_PKG = 'com.greenpoint.android.mc10086.activity'
#: 信封组件信息（urlCode → componentID；缺失时网关回 403）
N_COMP_BASE = {'appKey': '00000159', 'baseFrameVersion': '0.0.1',
               'componentVersion': '2.0.0'}
N_COMPONENTS = {
    '60012': dict(N_COMP_BASE, componentID='BI2021SA000055'),
    '30077': dict(N_COMP_BASE, componentID='BI2021SA000055'),
    '20007': dict(N_COMP_BASE, componentID='BI2021SA000061'),
    '20002': dict(N_COMP_BASE, componentID='BI2021SA000061'),
    '20016': dict(N_COMP_BASE, componentID='BI2021SA000061'),
    '20008': dict(N_COMP_BASE, componentID='BI2021SA000061'),
    '30645': dict(N_COMP_BASE, componentID='BI2021SA000061'),
}
#: 表内没有的 urlCode 不带组件信息
N_COMPONENTS_DEFAULT = {}
#: SSO 登录页正文中一次性 sid 的形态：loginPath = '/appTokenLogin?sid=xxxx'
RE_LOGIN_PATH = re.compile(r"loginPath\s*=\s*'([^']+)'")


def env(key, default=''):
    v = os.environ.get(key)
    return v.strip() if (v and v.strip()) else str(default)


class Cfg(object):
    def __init__(self):
        for k, v in DEFAULTS.items():
            setattr(self, k.lower(), env(k, v))
        self.gap = float(self.cmcc_gap or 1.2)
        self.dry_run = str(self.cmcc_dry_run).strip() in ('1', 'true', 'True')
        self.notify_on = str(self.cmcc_notify).strip() in ('1', 'true', 'True')
        self.task_set = set(t.strip() for t in str(self.cmcc_tasks).split(',') if t.strip())

    def want(self, name):
        return ('all' in self.task_set) or (name in self.task_set)


# HTTP（curl_cffi 优先；requests 回退时挂老式 TLS 适配器——wx.10086.cn 只收旧套件）
class Http(object):
    def __init__(self, timeout=25):
        self.timeout = timeout
        self.backend = 'requests'
        try:
            from curl_cffi import requests as cr
            self._s = cr.Session(impersonate='chrome131_android')
            self.backend = 'curl_cffi'
        except Exception:  # noqa: BLE001
            import requests
            self._s = requests.Session()
            self._s.trust_env = False
            try:
                self._s.mount('https://', _make_adapter())  # wx.10086.cn 只收老式 TLS 套件
            except Exception:  # noqa: BLE001
                pass
        self.last = None

    def request(self, method, url, headers=None, body=None, params=None, cookies=None,
                raw=None, form=None, allow_redirects=True):
        if params:
            url = url + ('&' if '?' in url else '?') + \
                '&'.join('%s=%s' % (k, v) for k, v in params.items())
        h = dict(headers or {})
        h.setdefault('User-Agent', UA)
        data = raw
        if raw is None and form is not None:
            h.setdefault('Content-Type', 'application/x-www-form-urlencoded;charset=UTF-8')
            data = '&'.join('%s=%s' % (k, v) for k, v in form.items())
        elif raw is None and body is not None:
            h.setdefault('Content-Type', 'application/json;charset=UTF-8')
            data = json.dumps(body, ensure_ascii=False)
        if cookies:
            h['Cookie'] = cookies
        kw = {} if allow_redirects else {'allow_redirects': False}
        try:
            if method.upper() == 'POST':
                r = self._s.post(url, data=(data or '{}').encode('utf-8'), headers=h,
                                 timeout=self.timeout, **kw)
            else:
                r = self._s.get(url, headers=h, timeout=self.timeout, **kw)
        except Exception as e:  # noqa: BLE001
            self.last = {'url': url, 'error': str(e)}
            return {'ok': False, 'http': None, 'json': None, 'text': '',
                    'raw_text': '', 'set_cookies': {}, 'error': str(e)}
        text = getattr(r, 'text', '') or ''
        try:
            js = r.json()
        except Exception:  # noqa: BLE001
            js = None
        if isinstance(js, dict) and set(js) <= {'root'} and isinstance(js.get('root'), str):
            try:
                js = json.loads(js['root'])
            except Exception:  # noqa: BLE001
                pass
        if js is None and text.lstrip().startswith('<'):
            # coc3 部分端点按内容协商回 XML 包装（<ResponseWrapper><code>0</code>…），统一拆成 dict
            mc = re.search(r'<code>([^<]*)</code>', text)
            mm = re.search(r'<msg>([^<]*)</msg>', text)
            js = {'code': mc.group(1) if mc else None,
                  'msg': mm.group(1) if mm else None, '_xml': True}
        sc = {}
        try:
            lines = r.headers.get_list('Set-Cookie')
        except Exception:  # noqa: BLE001
            lines = [r.headers.get('Set-Cookie')]
        for line in lines:
            if not line:
                continue
            kv = str(line).split(';')[0]
            if '=' in kv:
                k, v = kv.split('=', 1)
                sc[k.strip()] = v.strip()
        try:
            hdrs = {k.lower(): v for k, v in r.headers.items()}
        except Exception:  # noqa: BLE001
            hdrs = {}
        rec = {'ok': r.status_code == 200, 'http': r.status_code, 'json': js,
               'text': text[:500], 'raw_text': text, 'set_cookies': sc, 'headers': hdrs,
               'url': url}
        self.last = rec
        return rec


def _legacy_ctx():
    ctx = ssl.create_default_context()
    ctx.set_ciphers('DEFAULT@SECLEVEL=1')
    return ctx


def _make_adapter():
    """requests 后端的旧式 TLS 适配器（wx.10086.cn 拒绝现代 GCM 套件）。"""
    from requests.adapters import HTTPAdapter

    class A(HTTPAdapter):
        def init_poolmanager(self, *a, **k):
            k['ssl_context'] = _legacy_ctx()
            return super(A, self).init_poolmanager(*a, **k)
    return A()


def jget(js, *path, **kw):
    cur = js
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and -len(cur) <= p < len(cur):
            cur = cur[p]
        else:
            return kw.get('default')
    return cur if cur is not None else kw.get('default')


def is_success(js):
    if not isinstance(js, dict):
        return False
    return str(js.get('code')).upper() == 'SUCCESS' or js.get('success') is True


_AES = None


def _aes():
    global _AES
    if _AES is None:
        try:
            from Crypto.Cipher import AES
            _AES = AES
        except ImportError:
            _AES = False
    return _AES or None
def _pkcs7_pad(data):
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def aes_ecb_enc(key, plain):
    AES = _aes()
    if AES is None:
        raise RuntimeError('缺少 pycryptodome：pip3 install pycryptodome')
    data = plain.encode('utf-8') if isinstance(plain, str) else plain
    return base64.b64encode(AES.new(key.encode('utf-8'), AES.MODE_ECB)
                            .encrypt(_pkcs7_pad(data))).decode('ascii')


def aes_ecb_dec(key, b64text):
    AES = _aes()
    if AES is None:
        raise RuntimeError('缺少 pycryptodome')
    pt = AES.new(key.encode('utf-8'), AES.MODE_ECB).decrypt(base64.b64decode(b64text))
    pad = pt[-1] if pt else 0
    if 0 < pad <= 16 and pt[-pad:] == bytes([pad]) * pad:
        pt = pt[:-pad]
    return pt.decode('utf-8')


def aes_cbc_enc(key, iv, plain):
    AES = _aes()
    if AES is None:
        raise RuntimeError('缺少 pycryptodome')
    data = plain.encode('utf-8') if isinstance(plain, str) else plain
    return base64.b64encode(AES.new(key, AES.MODE_CBC, iv)
                            .encrypt(_pkcs7_pad(data))).decode('ascii')


COC_RSA_PUB_B64 = (
    'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0a66eNBd/ioaGnEM2AV+ctQX8VYt6dszzI8zOv26Kc1A7ex/'
    'cu/txmbB6WrU5ARoTeS0gk3KS21e/xP1BR1FUqL/+YJ4v8gpFY21/2gWuEPVBoHEd34FMpDRuiQkpmrvuuRF+QJQIg5ASE'
    'Bpwz4n15ltl7Fd4E+NLp/JRJxzNR55ZeVLLFaucWYe8FIvX5BKx8i+sJWZYDoxTbjrBTy2CZDZObT+PxhOBXsCkolsG8Ux'
    '+tIAoU+UAVSQ0qT62z/sHPVPngHAqmLP/sF3IM6Mrub7BqhwJe3rvddqrw+Q2Jg8upZ/oUlZvXtQZCxJXtg0eJVdKTmnE6'
    'hD+/M99ukEoQIDAQAB')
COC_EN_VERSION = '1486234121417629696'
MIGU_KEY = 'zPRNydk5jqynvPjf68GkZvOhvxkBjamP'


def _der_read(buf, i):
    tag = buf[i]; i += 1
    ln = buf[i]; i += 1
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(buf[i:i + n], 'big'); i += n
    return tag, i, i + ln, i + ln


def _rsa_modulus():
    der = base64.b64decode(COC_RSA_PUB_B64)
    _t, s, _e, _n = _der_read(der, 0)
    _t, s2, e2, _n = _der_read(der, s)
    _t, s3, e3, _n = _der_read(der, e2)
    _t, s4, e4, _n = _der_read(der, s3 + 1)
    _t, s5, e5, nxt = _der_read(der, s4)
    nn = int.from_bytes(der[s5:e5], 'big')
    _t, s6, e6, _n = _der_read(der, nxt)
    return nn, int.from_bytes(der[s6:e6], 'big')


def coc_encrypt(data):
    """coc web6（aurora）加密：返回 (body, headers)。"""
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.PublicKey import RSA
    key = secrets.token_hex(8)
    plain = json.dumps(data, ensure_ascii=False, separators=(',', ':')) \
        if isinstance(data, (dict, list)) else str(data)
    body = {'cocEnContent': aes_ecb_enc(key, plain)}
    n, e = _rsa_modulus()
    aurora = base64.b64encode(PKCS1_v1_5.new(RSA.construct((n, e)))
                               .encrypt(key.encode('utf-8'))).decode('ascii')
    return body, {'Coc-En-Version': COC_EN_VERSION, 'coc-aurora': aurora, 'coc-aurora-v': '2'}


NATIVE_HOST = 'https://client.app.coc.10086.cn'
N_AES_ENC_KEY = b'tVkdaRWRY0ZkV1Vr'
N_AES_DEC_KEY = b'RYV0hCV1lV25KYVJ'
N_AES_IV = b'VjFSQ1ZtVkQxRTlQ'
XTOKEN_KEY = b'bAIgvwAuA4tbDr9d'
XTOKEN_IV = b'9791027341711819'
CID_KEY = bytes([64, 120, 105, 39, 97, 110, 37, 108, 118, 100, 105, 97,
                 110, 35, 120, 105, 116, 111, 110, 103, 98, 117, 126, 38])
CID_IV = b'01234567'
SEC_SUFFIX = 'Leadeon/SecurityOrganization'
RSA_S_N = int('968e9acf9e59b4e2e1bb24266ee39043788a45f788ecdc5c971f4964c3267cf'
              '20afd3b7f029b68a9a9b65d77d0306b8319df0c174f3dd82ef3894a6c38e236'
              '34f6095a81901ad7e6650911c0910f12c7de50a6fcee3ae3563cc5985c46a96'
              '5db2af49e94f69b62f67ff3d5c0f79782572375e5f8b44aa43c0ca6d48e8a969beb', 16)
RSA_E = 0x10001
N_DEVICE = {'oaid': '50115c6d-9861-4ce2-a7ea-00df6dff6d8a',
            'android_id': '6c3fa4e7c530df45', 'mac': '00:00:00:00:00:00',
            'model': 'PGT-AN00', 'brand': 'HUAWEI', 'os_release': '12',
            'screen': '1080x2340', 'channel': 'A2081', 'net_type': '3'}


class Native(object):
    """App 原生网关（纯协议）：信封 + 三条签名 + AES 报文体 + 响应解密。"""

    #: urlCode → 网关路径
    PATHS = {
        '30077': 'biz-orange/DN/security/getSecurityId',
        '20007': 'biz-orange/BN/userInformationService/getUserInformation',
        '20002': 'biz-orange/BN/scoreQueryService/getScoreQuery',
        '50528': 'biz-orange/SHS/card/getCardVolume',
        '50576': 'biz-orange/SHS/activity/getUserAward',
        '50514': 'biz-orange/SHS/gsm/getUserRights',
        '20084': 'biz-orange/BN/cardArea/getGsm5GCard',
        '30324': 'biz-orange/DN/careVersion/filialPietyCard',
        '30301': 'biz-orange/DN/receiveRights/getPushContent',
        '30645': 'biz-orange/DN/activity/getActivityPageInfo',
        '10000': 'biz-orange/DN/init/startInit',
        '30313': 'biz-orange/DN/careVersion/getClock',
        '30314': 'biz-orange/DN/careVersion/getIcon',
        '30323': 'biz-orange/DN/careVersion/getBannerList',
        '30679': 'biz-orange/DN/carePopup/getDrawerOpens',
        '50375': 'biz-orange/SHS/rights/vipReceiveList',
        '30690': 'biz-orange/DN/welfareWidget/getAdverList',
        '30344': 'biz-orange/SHS/myPage/personalAssets',
        '50491': 'biz-orange/SHS/myPage/getMembershipInfo',
        '50495': 'biz-orange/SHS/myPage/getBirthdayPrivilegeInfo',
        '50426': 'biz-orange/SHS/myPage/getDailyHealthInfo',
        '50047': 'biz-orange/WN/mailService/getUnreadCount',
        '50492': 'biz-orange/SHS/myPage/getAvatarEdit',
        '50493': 'biz-orange/SHS/myPage/getAvatarList',
        '50541': 'biz-orange/SHS/digital/getAvatarInfo',
        '50546': 'biz-orange/SHS/digital/roleInstTokenGen',
        '50415': 'biz-orange/SHG/worldCup/getTaskInfo',
        '60012': 'biz-orange/LN/uamrandcodelogin/autoLogin',
        '20016': 'biz-orange/BN/realFeeQuery/getRealFee',
        '20008': 'biz-orange/BN/payFeesQuery/getPayFeesQueryInfo',
        '30653': 'biz-orange/DN/dataReport/getSwitchSetupInformation',
    }

    def __init__(self, cfg, http, note):
        self.cfg = cfg
        self.http = http
        self.note = note
        self.xk = ''
        self.cookie = ''
        self._session_city = ''
        self._session_prov = ''
        try:
            sp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              '04_脚本', 'data', 'session.json')
            d = json.load(open(sp, encoding='utf-8'))
            if str(d.get('phone')) == str(cfg.cmcc_phone) and d.get('cookie'):
                self.cookie = d['cookie']
                note('原生', '票据取自项目 session.json（%s，比脚本常量新）' % cfg.cmcc_phone)
            if str(d.get('phone')) == str(cfg.cmcc_phone):
                if d.get('city_code'):
                    self._session_city = str(d['city_code'])
                if d.get('province_code'):
                    self._session_prov = str(d['province_code'])
                if d.get('xk'):
                    self.xk = str(d['xk'])
        except Exception:  # noqa: BLE001
            pass
        if not self._session_city:
            try:
                _cc = json.load(open(os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    '.cmcc_cookie_%s.json' % str(cfg.cmcc_phone)[-4:]), encoding='utf-8'))
                if str(_cc.get('phone')) == str(cfg.cmcc_phone):
                    if _cc.get('city'):
                        self._session_city = str(_cc['city'])
                    if _cc.get('prov'):
                        self._session_prov = str(_cc['prov'])
                    if _cc.get('xk') and not self.xk:
                        self.xk = str(_cc['xk'])
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------- 加密原语
    @staticmethod
    def _md5(s):
        import hashlib
        return hashlib.md5(s.encode('utf-8') if isinstance(s, str) else s).hexdigest()

    @staticmethod
    def _cbc(key, iv, data):
        AES = _aes()
        if AES is None:
            raise RuntimeError('缺少 pycryptodome：pip3 install pycryptodome')
        if isinstance(data, str):
            data = data.encode('utf-8')
        p = 16 - (len(data) % 16)
        return AES.new(key, AES.MODE_CBC, iv).encrypt(data + bytes([p]) * p)

    @staticmethod
    def _uncbc(key, iv, raw):
        AES = _aes()
        pt = AES.new(key, AES.MODE_CBC, iv).decrypt(raw)
        pad = pt[-1] if pt else 0
        if 0 < pad <= 16 and pt[-pad:] == bytes([pad]) * pad:
            pt = pt[:-pad]
        return pt.decode('utf-8', 'replace')

    def _cid(self):
        cid = getattr(self, '_cid_cache', None)
        if cid is not None:
            return cid
        try:
            from Crypto.Cipher import DES3
            plain = 'oaid|#$deviceId|#$%s|#$%s|#$null' % (N_DEVICE['android_id'], N_DEVICE['mac'])
            p = 8 - (len(plain.encode('utf-8')) % 8)
            ct = DES3.new(CID_KEY, DES3.MODE_CBC, CID_IV).encrypt(
                plain.encode('utf-8') + bytes([p]) * p)
            cid = base64.b64encode(ct).decode('ascii')
        except Exception:  # noqa: BLE001
            cid = ''
        self._cid_cache = cid
        return cid

    # ------------------------------- 组装
    def _envelope(self, req_body, tel=''):
        env = {'ak': '1EFCFEA0D579D2E66F3B68E68F4CC6560B546230',
               'cid': self._cid(), 'ctid': self._cid(), 'cv': self.cfg.cmcc_app_version,
               'en': '0', 'imei': 'oaid:%s' % N_DEVICE['oaid'], 'nt': N_DEVICE['net_type'],
               'packageName': APP_PKG, 'sb': N_DEVICE['brand'], 'sn': N_DEVICE['model'],
               'sp': N_DEVICE['screen'], 'st': '1', 'sv': '12', 'xc': N_DEVICE['channel'],
               'xk': self.xk or 'null'}
        env.update(N_COMPONENTS.get(getattr(self, '_cur_url_code', '')) or {})
        env['t'] = self._md5(self.cookie) if self.cookie else ''
        env['tel'] = tel or (self.cfg.cmcc_phone or '99999999999')
        env['prov'] = self._session_prov or self.cfg.cmcc_province_code
        env['city'] = self._session_city or self.cfg.cmcc_city_code
        env['reqBody'] = req_body
        # fastjson：键名 ASCII 升序、丢弃 None、紧凑无空格
        return json.dumps({k: v for k, v in sorted(env.items()) if v is not None},
                          ensure_ascii=False, separators=(',', ':'))

    def _headers(self, url, plain):
        ts = int(time.time() * 1000)
        nonce = int((random.random() * 9 + 1) * 10 ** 7)
        path = '/' + url.split('/', 3)[3] if url.count('/') >= 3 else url
        xt_plain = '%s_%s_%d_%d' % (self.xk or 'null', path, ts, nonce)
        x_token = base64.b64encode(self._cbc(XTOKEN_KEY, XTOKEN_IV, xt_plain)).decode('ascii')
        jsession = ''
        for part in (self.cookie or '').split(';'):
            if 'jsessionid' in part.lower() and '=' in part:
                jsession = part.split('=', 1)[1].strip()
                break
        return {'xs': self._md5('%s_%s_%s' % (url, plain, SEC_SUFFIX)), 'x-qen': '14',
                'x-time': str(ts), 'x-nonce': str(nonce), 'x-token': x_token,
                'x-sign': self._md5('%s_%d_%d_%s' % (x_token, ts, nonce, jsession or 'null')),
                'Content-Type': 'application/json; charset=utf-8',
                'User-Agent': 'okhttp/3.14.9 ChinaMobile/%s (Linux; Android %s; %s)'
                              % (self.cfg.cmcc_app_version, N_DEVICE['os_release'],
                                 N_DEVICE['model'])}

    # ------------------------------- 调用
    @staticmethod
    def _rsa_phone(phone):
        """明文 = ``b'leadeon' + utf8(手机号) + 本地时间('%Y%m%d%H%M%S')``；输出 128 字节后标准 Base64（172 字符）。"""
        from Crypto.Cipher import PKCS1_v1_5
        from Crypto.PublicKey import RSA
        pt = b'leadeon' + str(phone).encode('utf-8') + time.strftime('%Y%m%d%H%M%S').encode()
        return base64.b64encode(
            PKCS1_v1_5.new(RSA.construct((RSA_S_N, RSA_E))).encrypt(pt)).decode('ascii')

    def auto_login(self, phone):
        """**免密续期**（`60012 /LN/uamrandcodelogin/autoLogin`）——会话空闲超时（`410000`）后自愈。"""
        if str(getattr(self.cfg, 'cmcc_auto_renew', '') or '').strip() not in ('1', 'true', 'True'):
            return {'ok': False, 'skipped': True,
                    'error': 'CMCC_AUTO_RENEW=0（默认：不轮换票据——轮换会使其它副本的旧票'
                             '全部孤儿化；确需自动续期设 1）'}
        try:
            import json as _json
            import time as _time
            if not self.xk:
                self.fetch_xk()
            body = {'cellNum': self._rsa_phone(phone),
                    'imei': 'oaid:%s' % N_DEVICE['oaid'], 'loginFlag': '1',
                    'sendSmsFlag': '0', 'reqSource': 'switch',
                    'sysTime': str(int(_time.time() * 1000))}
            url = NATIVE_HOST + '/' + self.PATHS['60012']
            self._cur_url_code = '60012'
            plain = self._envelope(body, tel=phone)
            headers = self._headers(url, plain)
            if self.cookie:
                headers['Cookie'] = self.cookie
            body_b64 = base64.b64encode(self._cbc(N_AES_ENC_KEY, N_AES_IV, plain)).decode('ascii')
            r = self.http.request('POST', url, headers=headers, raw=body_b64)
            _raw_sc = (r.get('headers') or {}).get('set-cookie') or ''
            if _raw_sc:
                self.cookie = _raw_sc
            else:
                for k, v in (r.get('set_cookies') or {}).items():
                    if k.lower() == 'jsessionid' and v:
                        self.cookie = 'JSESSIONID=%s' % v
            txt = r.get('raw_text') or ''
            xpen = (r.get('headers') or {}).get('x-pen')
            rc = None
            if xpen == '14' and txt:
                try:
                    obj = json.loads(self._uncbc(N_AES_DEC_KEY, N_AES_IV, base64.b64decode(txt)))
                    rc = obj.get('retCode')
                except Exception:  # noqa: BLE001
                    pass
            out = {'ok': rc == '000000', 'retCode': rc, 'cookie': self.cookie,
                   'cookie_updated': bool((r.get('set_cookies') or {}).get('jsessionid'))}
            if out['ok'] and self.cookie and out['cookie_updated']:
                try:
                    sp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      '04_脚本', 'data', 'session.json')
                    d = json.load(open(sp, encoding='utf-8'))
                    if str(d.get('phone')) == str(phone):
                        d['cookie'] = self.cookie
                        d['last_login_at'] = int(time.time())
                        d['login_time'] = int(time.time())
                        with open(sp, 'w', encoding='utf-8') as fh:
                            json.dump(d, fh, ensure_ascii=False, indent=1)
                except Exception:  # noqa: BLE001
                    pass
            if out['ok'] and self.cookie:
                try:
                    _cache = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          '.cmcc_cookie_%s.json' % str(phone)[-4:])
                    with open(_cache, 'w', encoding='utf-8') as _fh:
                        json.dump({'phone': str(phone), 'cookie': self.cookie,
                                   'at': time.time(),
                                   'city': self._session_city or self.cfg.cmcc_city_code,
                                   'prov': self._session_prov or self.cfg.cmcc_province_code,
                                   'xk': self.xk or ''},
                                  _fh, ensure_ascii=False, indent=1)
                except Exception:  # noqa: BLE001
                    pass
            return out
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'error': str(exc)[:100]}

    def fetch_xk(self):
        """`30077` 取设备安全标识 xk（参与每个请求的 x-token）。失败保持 `'null'`。"""
        if self.xk:
            return self.xk
        try:
            r = self.call('30077', {'ssk': self._cid()}, need_xk=False)
            d = r.get('data')
            ssv = (d or {}).get('ssv') if isinstance(d, dict) else ''
            if ssv:
                self.xk = ssv
                self.note('原生', 'xk 已取回（%d 位）' % len(ssv))
        except Exception as exc:  # noqa: BLE001
            self.note('原生', '取 xk 失败：%s' % str(exc)[:80])
        return self.xk

    def set_cookie(self, cookie):
        """设置 App 登录 Cookie（整串原文；用于信封 `t` 与请求头 `Cookie`）。"""
        self.cookie = (cookie or '').strip()

    def call(self, url_code, req_body, need_xk=True, tel=''):
        """发一次原生请求。返回 dict（**不抛业务异常**，`retCode != 000000` 也返回）。"""
        if url_code not in self.PATHS:
            raise RuntimeError('未登记的原生 urlCode=%s（加进 Native.PATHS）' % url_code)
        self._cur_url_code = url_code
        if need_xk and not self.xk:
            self.fetch_xk()
        url = NATIVE_HOST + '/' + self.PATHS[url_code]
        plain = self._envelope(req_body, tel=tel)
        headers = self._headers(url, plain)
        if self.cookie:
            headers['Cookie'] = self.cookie
        body = base64.b64encode(self._cbc(N_AES_ENC_KEY, N_AES_IV, plain)).decode('ascii')
        r = self.http.request('POST', url, headers=headers, raw=body)
        for k, v in (r.get('set_cookies') or {}).items():
            if k.lower() == 'jsessionid' and v and 'JSESSIONID=%s' % v not in self.cookie:
                self.cookie = ('JSESSIONID=%s' % v) if not self.cookie else self.cookie
        raw_text = r.get('raw_text') or ''
        xpen = (r.get('headers') or {}).get('x-pen')
        obj = None
        if xpen == '14' and raw_text:
            try:
                obj = json.loads(self._uncbc(N_AES_DEC_KEY, N_AES_IV, base64.b64decode(raw_text)))
            except Exception as exc:  # noqa: BLE001
                self.note('原生', '%s 响应解密失败：%s' % (url_code, str(exc)[:60]))
        if obj is None:
            obj = r.get('json') if isinstance(r.get('json'), dict) else {}
        return {'urlCode': url_code, 'http': r.get('http'), 'retCode': (obj or {}).get('retCode'),
                'retDesc': (obj or {}).get('retDesc'), 'data': (obj or {}).get('rspBody'),
                'obj': obj}


def jwt_cache_path(phone):
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, '.cmcc_jwt_cache_%s.json' % str(phone)[-4:])


def load_jwt(phone):
    try:
        d = json.load(open(jwt_cache_path(phone), encoding='utf-8'))
        if d.get('phone') == phone and time.time() - d.get('saved_at', 0) < 30 * 86400:
            return d.get('jwt') or ''
    except Exception:  # noqa: BLE001
        pass
    return ''


def save_jwt(phone, jwt):
    if not jwt:
        return
    p = jwt_cache_path(phone)
    first = time.time()
    try:
        old = json.load(open(p, encoding='utf-8'))
        if old.get('phone') == phone and old.get('first_seen'):
            first = old['first_seen']
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump({'jwt': jwt, 'phone': phone, 'first_seen': first,
                       'saved_at': time.time()}, fh)
    except Exception:  # noqa: BLE001
        pass


class Sso(object):
    """qwhdhub / hlwyxhdhub 的会话换票器。jwt 账号级通用；每个活动页各自换 30min 会话。"""

    def __init__(self, cfg, http, note):
        self.cfg = cfg
        self.http = http
        self.note = note
        self.jwt = load_jwt(cfg.cmcc_phone)
        self.acts = {}  # key -> {'cookie','referer'}
        self.entries = {}  # key -> act_url（api() 自动重换会话时用）
        self.dead = ''  # 熔断原因（App 票据/JWT 均失效时置位，后续任务直接跳过换票）
        self._healed = False
        _cands = []
        try:
            _cc = json.load(open(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                '.cmcc_cookie_%s.json' % str(cfg.cmcc_phone)[-4:]), encoding='utf-8'))
            if str(_cc.get('phone')) == str(cfg.cmcc_phone) and _cc.get('cookie'):
                _cands.append((float(_cc.get('at') or 0), _cc['cookie']))
        except Exception:  # noqa: BLE001
            pass
        try:
            _sp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               '04_脚本', 'data', 'session.json')
            _d = json.load(open(_sp, encoding='utf-8'))
            if str(_d.get('phone')) == str(cfg.cmcc_phone) and _d.get('cookie'):
                _cands.append((float(_d.get('last_login_at') or 0), _d['cookie']))
        except Exception:  # noqa: BLE001
            pass
        if _cands and max(_cands)[1] != cfg.cmcc_app_token:
            cfg.cmcc_app_token = max(_cands)[1]

    def _login_url(self, act_url):
        r = self.http.request('GET', BASE + '/qwhdsso/login',
                              params={'dlwmh': 'true', 'actUrl': act_url})
        m = RE_LOGIN_PATH.search(r.get('raw_text') or '')
        if not m:
            raise RuntimeError('SSO 登录页未返回 sid（入口可能变更）')
        return BASE + '/qwhdsso' + m.group(1)

    def _post_login(self, url, jwt_token, token):
        body = {'provinceCode': self.cfg.cmcc_province_code,
                'cityCode': self.cfg.cmcc_city_code,
                'userCheckId': '%x' % int(self.cfg.cmcc_phone),
                'carrierOperator': self.cfg.cmcc_carrier_operator,
                'appVersionCode': self.cfg.cmcc_app_version,
                'took': random.randint(120, 900),
                'jwtToken': jwt_token, 'token': token}
        r = self.http.request('POST', url, body=body)
        return (r.get('json') or {}), r

    def session(self, key, act_url, force=False):
        """换取（或复用）某活动页的会话。返回 {'cookie','referer'}；失败抛 RuntimeError。"""
        if key in self.acts and not force:
            return self.acts[key]
        self.entries[key] = act_url
        if self.dead:
            raise RuntimeError('SSO 已熔断：%s' % self.dead)
        url = self._login_url(act_url)
        resp, r = None, None
        resp = None
        sent_token = False
        if self.jwt and self.cfg.cmcc_app_token:
            resp, r = self._post_login(url, self.jwt, self.cfg.cmcc_app_token)
            sent_token = True
        if (resp is None or resp.get('code') != 'SUCCESS') and self.cfg.cmcc_app_token and not sent_token:
            resp, r = self._post_login(url, None, self.cfg.cmcc_app_token)
        if (resp is None or resp.get('code') != 'SUCCESS') and self.jwt and not sent_token:
            resp, r = self._post_login(url, self.jwt, '')
        if (resp is None or resp.get('code') != 'SUCCESS') and                 str(getattr(self.cfg, 'cmcc_auto_renew', '') or '').strip() in ('1', 'true', 'True') and                 callable(getattr(self, 'renew_cookie', None)) and not self._healed:
            self._healed = True
            out = self.renew_cookie() or {}
            if not out.get('ok'):
                self.note('SSO', '60012 自愈失败（retCode=%s %s）——若为属地问题请核对 '
                          'CMCC_CITY_CODE（须为会话真实属地，当前 %s）'
                          % (out.get('retCode') or '-', str(out.get('error') or '')[:36],
                             self.cfg.cmcc_city_code))
            if out.get('ok') and out.get('cookie'):
                self.cfg.cmcc_app_token = out['cookie']
                self.note('SSO', '⭐ 60012 自愈成功（票据已轮换——注意：其它副本的旧票'
                          '已全部失效，需同步新票）')
                resp, r = self._post_login(url, self.jwt, out['cookie'])
                if resp.get('code') != 'SUCCESS':
                    resp, r = self._post_login(url, None, out['cookie'])
        if resp is None or resp.get('code') != 'SUCCESS':
            if not self.cfg.cmcc_app_token:
                raise RuntimeError('无可用凭证：请配置 CMCC_APP_TOKEN')
            self.dead = ('票据已失效且自愈未成（常见根因：① 属地不符——CMCC_CITY_CODE 非'
                         '会话真实属地时 60012 必败；② 票据被其它副本轮换孤儿化；'
                         '③ 自然过期）。恢复：核对 CMCC_CITY_CODE 后重跑；仍败则短信重登一次')
            raise RuntimeError('appTokenLogin 失败 —— %s' % self.dead)
        resp = resp
        data = resp.get('data') or {}
        jwt = data.get('jwt')
        if jwt:
            self.jwt = jwt
            save_jwt(self.cfg.cmcc_phone, jwt)
        page = data.get('url') or ''
        if not page.startswith('https://wx.10086.cn/'):
            raise RuntimeError('SSO 返回异常跳转地址：%s' % page[:60])
        route_page = page
        if '/hlwyxhdhub/act-wedrecharge/index.html?' in page:
            route_page = page.replace('/index.html?', '/1024101716?')
        r2 = self.http.request('GET', route_page, allow_redirects=False)
        tok = ''
        for k, v in (r2.get('set_cookies') or {}).items():
            if k.endswith('SESSION_TOKEN'):
                tok = '%s=%s' % (k, v)
                break
        if not tok:
            hub = 'hlwyxhdhub' if '/hlwyxhdhub/' in act_url else 'qwhdhub'
            base_body = {'provinceCode': self.cfg.cmcc_province_code,
                         'cityCode': self.cfg.cmcc_city_code,
                         'activityId': '1024101716' if hub == 'hlwyxhdhub' else '1021122301',
                         'appVersionCode': self.cfg.cmcc_app_version, 'userCheckId': ''}
            variants = [(UA_NATIVE, {'jwtToken': self.jwt, 'token': ''}),
                        (UA_NATIVE, {'jwtToken': '', 'token': self.cfg.cmcc_app_token}),
                        (UA, {'jwtToken': self.jwt, 'token': ''}),
                        (UA, {'jwtToken': '', 'token': self.cfg.cmcc_app_token})]
            for ua, extra in variants:
                body = dict(base_body); body.update(extra)
                r3 = self.http.request('POST', BASE + '/' + hub + '/user/appTokenLogin',
                                       headers={'User-Agent': ua, 'Origin': BASE,
                                                'login-check': '1',
                                                'X-Requested-With': APP_PKG,
                                                'Referer': page}, body=body)
                for k, v in (r3.get('set_cookies') or {}).items():
                    if k.endswith('SESSION_TOKEN'):
                        tok = '%s=%s' % (k, v)
                        break
                if tok:
                    break
        if not tok:
            raise RuntimeError('未取得活动会话令牌（%s）' % key)
        self.acts[key] = {'cookie': tok, 'referer': page}
        return self.acts[key]

    def api(self, key, method, path, body=None, referer=None, _retry=True):
        """带会话调 qwhdhub/hlwyxhdhub 业务接口；Referer 必须是带 token 的活动页。"""
        act = self.acts.get(key) or {}
        h = {'Accept': '*/*', 'Origin': BASE,
             'Referer': referer or act.get('referer') or BASE + '/',
             'login-check': '1', 'X-Requested-With': 'XMLHttpRequest'}
        if method.upper() == 'POST' and body is None:
            body = {}
        r = self.http.request(method, BASE + path, headers=h, body=body, cookies=act.get('cookie'))
        js = r.get('json') if isinstance(r.get('json'), dict) \
            else {'code': 'HTTP_%s' % r.get('http'), 'msg': (r.get('text') or '')[:60]}
        if _retry and str(js.get('code')).upper() in ('FAILED', 'GOTO_LOGIN'):
            try:
                self.session(key, self.entries.get(key) or BASE + '/', force=True)
                return self.api(key, method, path, body, referer, _retry=False)
            except Exception:  # noqa: BLE001
                pass
        return js


# 任务运行器
class Runner(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.http = Http()
        self.log = []
        self.sso = Sso(cfg, self.http, self.note)
        # App 原生面（自包含实现；凭 App 票据 Cookie 自举 xk）
        self.native = Native(cfg, self.http, self.note)
        if cfg.cmcc_app_token and self.native.cookie != cfg.cmcc_app_token:
            self.native.set_cookie(cfg.cmcc_app_token)
        self.sso.renew_cookie = lambda: self.native.auto_login(self.cfg.cmcc_phone)
        self.ext = cfg.cmcc_ext
        self.coc_token = ''
        self.coc3_token = cfg.cmcc_coc3_token
        self.migu_jwt = ''

    @property
    def dry_run(self):
        return self.cfg.dry_run

    def note(self, tag, msg):
        line = '[%s] %s' % (tag, msg)
        self.log.append(line)
        print(line, flush=True)

    def rest(self, lo=1.0, hi=2.5):
        time.sleep(random.uniform(lo, hi))

    # ------------------------------------------------ ① mark31 月月签到
    def task_mark31(self):
        a = self.sso.session('mark31', ACTS['mark31']['entry'])
        st = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/mark31/markstatus')
        if not is_success(st):
            self.note('月月签到', '状态失败 %s %s' % (st.get('code'), st.get('msg'))); return
        d = st.get('data') or {}
        today = time.strftime('%Y%m%d')
        signed = any(str(x.get('date')) == today and str(x.get('status')) == '1'
                     for x in (d.get('markstatus') or []))
        acc = jget(d, 'userinfo', 'accumulateTimes', default='?')
        if signed:
            self.note('月月签到', '今日已签（累计 %s 天）' % acc)
        elif self.cfg.dry_run:
            self.note('月月签到', '[dry] 今日未签，累计 %s 天' % acc)
        else:
            r = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/mark31/domark', {'date': today})
            code, msg, status = r.get('code'), r.get('msg') or '', r.get('status') or ''
            # HAVE_MARKED=服务端幂等（视为已签）；PRIZE_NO_CONFIG=签成但当日无奖品
            if is_success(r) or '已签' in msg or status == 'HAVE_MARKED':
                prize = jget(r, 'data', 'markPrize', 'name', default='')
                self.note('月月签到', '签到成功（累计 %s 天）%s%s'
                          % (acc, '奖品:%s' % prize if prize else '',
                             '（今日无单日奖品）' if status == 'PRIZE_NO_CONFIG' else ''))
            else:
                self.note('月月签到', '签到失败 %s/%s/%s' % (code, status, msg))
        # 领奖池：taskAwardChance（无则回退 accumulateTaskInfo.lackDay<=0）
        latest = d
        if not self.cfg.dry_run:
            st2 = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/mark31/markstatus')
            if is_success(st2):
                latest = st2.get('data') or {}
        pool = latest.get('taskAwardChance')
        if pool is None:
            pool = [t for t in (latest.get('accumulateTaskInfo') or [])
                    if str(t.get('lackDay', '99')).isdigit() and int(t.get('lackDay', 99)) <= 0]
        names = {}
        for p in (latest.get('taskAwardChance') or []) + (latest.get('accumulateTaskInfo') or []) \
                + (latest.get('myTaskInfo') or []):
            nm = jget(p, 'prize', 'name', default='') or p.get('lotteryText') or ''
            if p.get('id') and nm and p['id'] not in names:
                names[p['id']] = nm
        got = 0
        for t in (pool or []):
            tid = t.get('id')
            if not tid:
                continue
            if self.cfg.dry_run:
                self.note('月月签到', '[dry] 可领奖 %s（%s）' % (tid, names.get(tid, ''))); continue
            r = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/mark31/taskAward/%s' % tid)
            self.note('月月签到', '领奖 %s（%s）→ %s %s'
                      % (tid, names.get(tid, '?'), r.get('status') or r.get('code'), r.get('msg')))
            if is_success(r):
                got += 1
            self.rest()
        if pool is not None:
            self.note('月月签到', '连签奖领取 %d 个（池 %d 个）' % (got, len(pool)))

    # ------------------------------------------------ ②③ 打卡（周六/追剧）
    def _task_sign_act(self, key):
        act = ACTS[key]
        self.sso.session(key, act['entry'])
        qm, qp = act['sign_query']
        st = self.sso.api(key, qm, qp)
        d = st.get('data') or {}
        if act['signed'](d):
            self.note(act['name'], '今日已打卡')
        elif self.cfg.dry_run:
            self.note(act['name'], '[dry] 今日未打卡')
        else:
            dm, dp = act['sign_do']
            r = self.sso.api(key, dm, dp)
            ok = is_success(r) or '已签' in (r.get('msg') or '')
            self.note(act['name'], '打卡%s%s' % ('成功' if ok else '失败',
                                                 '' if ok else ' %s %s' % (r.get('code'), r.get('msg'))))
        self.rest()

    # ------------------------------------------------ ④ AI豆任务
    def task_aitasks(self):
        self.sso.session('mark31', ACTS['mark31']['entry'])
        tl = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/task/taskList')
        tasks = jget(tl, 'data', 'tasks', default=[]) or []
        todo = [t for t in tasks if str(t.get('status')) == '0']
        if self.cfg.dry_run or not todo:
            self.note('AI豆任务', '%d 个任务，待办 %d 个' % (len(tasks), len(todo))); return
        got = skip = 0
        for t in todo:
            tid = str(t.get('taskId') or '')
            if not tid:
                continue
            ju = str(t.get('jumpUrl') or '')
            info = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/task/taskInfo', {'taskId': tid})
            d = info.get('data') or {}
            ttype = str(d.get('taskType') or '')
            scan = int(d.get('scanTime') or 0)
            if str(d.get('cToken') or d.get('taskToken') or ''):
                if self._open_finish_task_v2(tid, d):
                    aw = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/task/getTaskAward',
                                      {'taskId': tid})
                    self.note('AI豆任务', '⭐ %s +%s（open链）'
                              % (t.get('taskName') or tid,
                                 jget(aw, 'data', 'awardNum', default='?')))
                    got += 1
                    self.rest(0.8, 1.8)
                    continue
            if ju.startswith('https://'):
                try:
                    self.http.request('GET', ju, allow_redirects=True)
                    if scan:
                        time.sleep(scan + 1)
                except Exception:  # noqa: BLE001
                    pass
            body = {'taskId': tid, 'taskType': ttype}
            r = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/task/finishTask', body)
            # 服务端校验 Referer=目标页：被拒时带目标页 Referer 重试一次
            if not is_success(r) and '未达到' in (r.get('msg') or '') and ju.startswith('https://'):
                r = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/task/finishTask',
                                 body, referer=ju)
            # 跳转类任务：finishTask 回"特殊处理" ⇒ 走 hlw 握手（getOneTaskInfo→openFinish）再重试
            if not is_success(r) and ('特殊处理' in (r.get('msg') or '') or 'openFinish' in (r.get('msg') or '')):
                if self._open_finish_task(t):
                    r = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/task/finishTask', body)
            if is_success(r):
                aw = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/task/getTaskAward',
                                  {'taskId': tid})
                self.note('AI豆任务', '%s +%s' % (t.get('taskName') or tid,
                                                 jget(aw, 'data', 'awardNum', default='?')))
                got += 1
            else:
                skip += 1
            self.rest(0.8, 1.8)
        self.note('AI豆任务', '待办 %d：完成 %d，跳过 %d（第三方/真实操作类）' % (len(todo), got, skip))

    # ------------------------------------------------ ④b 跳转任务握手（hlw）
    def _open_finish_task_v2(self, tid, info):
        """跳转类任务握手：taskInfo 已带 cToken 直接 openFinish；只带 taskToken 先 getOneTaskInfo 换 cToken。"""
        try:
            ct = str(info.get('cToken') or '')
            scan = int(info.get('scanTime') or 0)
            if not ct:
                tt = str(info.get('taskToken') or '')
                if not tt:
                    return False
                g = self.sso.api('mark31', 'POST',
                                 '/qwhdhub/api/open/_pub/task/getOneTaskInfo',
                                 {'jtToken': tt})
                gd = g.get('data') or {}
                ct = str(gd.get('cToken') or '')
                scan = int(gd.get('scanTime') or scan or 0)
                if not ct:
                    return False
            if scan:
                time.sleep(min(scan, 30) + 1)
            fin = self.sso.api('mark31', 'POST',
                               '/qwhdhub/api/open/_pub/task/openFinish', {'cToken': ct})
            return is_success(fin)
        except Exception:  # noqa: BLE001
            return False

    def _open_finish_task(self, task):
        """跳转类任务：jumpUrl 自带 taskToken → hlw 会话 → getOneTaskInfo 取 cToken → 停留 scanTime → openFinish。成功后由调用方回 finishTask+领奖。"""
        ju = str(task.get('jumpUrl') or '')
        m = re.search(r'taskToken=([^&]+)', ju)
        if not m:
            return False
        try:
            self.sso.session('hlw', ACTS['hlw']['entry'])
            info = self.sso.api('hlw', 'POST', '/hlwyxhdhub/api/open/_pub/task/getOneTaskInfo',
                                {'jtToken': m.group(1)})
            d = info.get('data') or {}
            ct = d.get('cToken')
            scan = int(d.get('scanTime') or 0)
            if not ct:
                return False
            if scan:
                time.sleep(min(scan, 30) + 1)
            fin = self.sso.api('hlw', 'POST', '/hlwyxhdhub/api/open/_pub/task/openFinish',
                               {'cToken': ct})
            return is_success(fin)
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------ ⑤ 代币任务
    def task_coins(self):
        for key in ('saturday', 'video', 'game666'):
            act = ACTS[key]
            comp = act.get('task_component')
            if not comp:
                continue
            try:
                self.sso.session(key, act['entry'])
            except Exception as e:  # noqa: BLE001
                self.note('代币任务', '%s 会话失败：%s' % (act['name'], str(e)[:60])); continue
            lst = self.sso.api(key, 'GET', '/qwhdhub/diyTask/list/%s' % comp)
            tasks = lst.get('data') or []
            todo = [t for t in tasks if t.get('taskStage') == 'UNDO']
            if self.cfg.dry_run or not todo:
                self.note('代币任务', '%s：%d 个任务，待办 %d' % (act['name'], len(tasks), len(todo)))
                continue
            done = 0
            for t in todo:
                tid = t.get('taskId')
                if not tid:
                    continue
                fin = self.sso.api(key, 'POST', '/qwhdhub/diyTask/finish/%s' % tid)
                chk = self.sso.api(key, 'GET', '/qwhdhub/diyTask/list/%s' % comp)
                cur = next((x for x in (chk.get('data') or []) if x.get('taskId') == tid), None)
                if cur is None or cur.get('taskStage') == 'DONE':
                    done += 1
                else:
                    self.note('代币任务', '%s finish %s 未生效：%s %s'
                              % (act['name'], tid, fin.get('code'), (fin.get('msg') or '')[:70]))
                self.rest()
            self.note('代币任务', '%s：%d 待办，完成 %d' % (act['name'], len(todo), done))
            self.rest(1.5, 3.5)

    # ------------------------------------------------ ⑥ 抽奖（窗口制）
    def task_lottery(self):
        for key in ('saturday', 'video'):
            act = ACTS[key]
            lc = act.get('lottery')
            if not lc:
                continue
            try:
                self.sso.session(key, act['entry'])
            except Exception as e:  # noqa: BLE001
                self.note('抽奖', '%s 会话失败：%s' % (act['name'], str(e)[:60])); continue
            rem = self.sso.api(key, 'POST', '/qwhdhub/diyLottery/period/remain/%s' % lc['component'])
            if not is_success(rem):
                self.note('抽奖', '%s 余额查询失败 %s' % (act['name'], rem.get('code'))); continue
            d = rem.get('data') or {}
            wallet = d.get(lc['wallet_key']) or 0
            limit = d.get(lc['count_key']) if lc.get('count_key') else None
            n = wallet // lc['cost']
            if limit is not None:
                n = min(n, int(limit))
            head = '%s [%s] 余额 %s（%s/次）→ 可抽 %d 次' % (
                act['name'], lc['label'], wallet, lc['cost'], n)
            if n <= 0:
                self.note('抽奖', head); continue
            if self.cfg.dry_run:
                self.note('抽奖', head + '（dry 不消耗）'); continue
            tm = time.localtime()
            wd = int(time.strftime('%w'))  # 0=周日 → 归一成 isoweekday（周日=7）
            wd = 7 if wd == 0 else wd
            in_week = (not lc.get('weekday')) or int(lc.get('weekday')) == wd
            if not in_week or (lc.get('start_hour') and tm.tm_hour < lc['start_hour']):
                self.note('抽奖', head + '；未到窗口，本次不消耗'); continue
            res = []
            for _ in range(n):
                r = self.sso.api(key, 'GET', '/qwhdhub/diyLottery/lotterySafely/%s' % lc['component'])
                won = (r.get('data') or [{}])[0] if is_success(r) and r.get('data') else None
                if won:
                    res.append(str(won.get('prizeName') or '?'))
                elif str(r.get('code')) in ('SUCCESS', 'NOT_WON'):
                    res.append('未中' + ('(即停)' if lc['stop_on_miss'] else ''))
                    if lc['stop_on_miss']:
                        break
                else:
                    res.append('失败(%s)' % r.get('code'))
                    break
                self.rest(1.2, 2.8)
            self.note('抽奖', head + '，已抽 %d 次：%s' % (len(res), ' / '.join(res)))

    # ------------------------------------------------ ⑦ 评价有礼
    def task_assess(self):
        self.sso.session('assess', ASSESS_ENTRY)

        def q(method, path, params=None):
            return self.sso.api('assess', method, path, params)
        st = q('GET', '/qwhdhub/assess/markStatus').get('data') or {}
        if st.get('chance'):
            if self.cfg.dry_run:
                self.note('评价有礼', '[dry] 本周可评价（将评 10 分 +10 币）')
            else:
                r = q('GET', '/qwhdhub/assess/assess',
                      {'score': 10, 'time': int(time.time() * 1000)})
                self.note('评价有礼', '满分评价%s' % ('成功 +10 币' if is_success(r)
                                                  else '失败 %s %s' % (r.get('code'), r.get('msg'))))
        else:
            self.note('评价有礼', '本周已评（上次 %s 分）' % st.get('score', '?'))
        acc = q('POST', '/qwhdhub/account/query').get('data') or {}
        self.note('评价有礼', '评价币余额 %s（累计得 %s / 花 %s）'
                  % (acc.get('balance', '?'), acc.get('income', '?'), acc.get('outcome', '?')))
        # 可选兑换
        want = self.cfg.cmcc_assess_exchange
        if str(want) not in ('0', '', 'false'):
            info = q('GET', '/qwhdhub/activity/info').get('data') or {}
            meta = {str(p.get('id')): p for p in (info.get('prizes') or [])}
            pz = q('GET', '/qwhdhub/assess/prizeStatus').get('data') or {}
            smap = pz.get('prizeStatusHashMap') or {}
            pick = None
            if str(want) == 'auto':
                pick = next((p for p in meta.values()
                             if str(p.get('name', '')).strip() == '2元话费券'
                             and str(p.get('id')) in smap), None) \
                    or next((p for p in meta.values()
                             if '话费' in str(p.get('name', '')) and str(p.get('id')) in smap), None)
                if pick is None:
                    self.note('评价有礼', '兑换：未找到话费券档位，跳过')
            else:
                pick = meta.get(str(want))
            if pick:
                price = pick.get('exchangePrice') or 0
                bal = acc.get('balance')
                if isinstance(bal, (int, float)) and price and bal < price:
                    self.note('评价有礼', '兑换 %s：余额 %s 不足（需 %s），先攒币'
                              % (pick.get('name'), bal, price))
                elif self.cfg.dry_run:
                    self.note('评价有礼', '[dry] 将兑 %s（%s 币）' % (pick.get('name'), price))
                else:
                    r = q('GET', '/qwhdhub/assess/redeem',
                          {'prizeId': pick.get('id'), 'time': int(time.time() * 1000)})
                    self.note('评价有礼', '兑换 %s%s' % (pick.get('name'),
                                                        '成功（券48h内到账）' if is_success(r)
                                                        else '失败 %s %s' % (r.get('code'), r.get('msg'))))

    #: redeem 的终态 status（触达即停，不再消耗请求）
    SECKILL_STOP = {'PRIZE_NO_STOCK': '券已抢完', 'PRIZE_LIMIT_DAY': '当日次数已用完',
                    'PRIZE_LIMIT_MONTH': '当月次数已用完', 'PRIZE_RESTRIC_LIMIT': '活动期间上限',
                    'WORK_ORDER_RESTRIC_LIMIT': '工单限流（风控/黑名单）'}

    def task_seckill(self):
        """「签到有礼」秒杀抢券（qwhdhub markSeckill 族，与 mark31 同活动）。"""
        try:
            self.sso.session('mark31', ACTS['mark31']['entry'])
        except Exception as e:  # noqa: BLE001
            self.note('秒杀', '会话失败：%s' % str(e)[:70]); return
        q = lambda path, body=None: self.sso.api('mark31', 'POST',
                                                 '/qwhdhub/api/mark/markSeckill/' + path, body)
        sysr = q('sysTime')
        srv_ms = jget(sysr, 'data', 'sysTime', default=int(time.time() * 1000))
        offset = (srv_ms or 0) - int(time.time() * 1000)
        if abs(offset) > 15 * 60 * 1000:
            self.note('秒杀', '⚠ sysTime 偏移 %.1f 分钟异常，改用本地时钟' % (offset / 60000))
            offset = 0
        zones = []
        for attempt in range(3):
            sec = q('secConfig')
            zones = jget(sec, 'data', 'secKillData', 'secKillZones', default=[]) or []
            if zones:
                break
            time.sleep(2)
        wait_budget = float(self.cfg.cmcc_seckill_wait_min or 0) * 60
        while not zones and wait_budget > 0:
            self.note('秒杀', 'secConfig 空响应（波动），%d 分钟后重拉' % 5)
            time.sleep(300)
            wait_budget -= 300
            sec = q('secConfig')
            zones = jget(sec, 'data', 'secKillData', 'secKillZones', default=[]) or []
        if not zones:
            self.note('秒杀', '当前无场次（%s）' % (jget(sec, 'msg', default='未配置') or '未配置'))
            return
        fmt = lambda ms: time.strftime('%m-%d %H:%M:%S', time.localtime((int(ms) + offset) / 1000))
        for z in zones:
            self.note('秒杀', '场次%s %s~%s %s (prizeId=%s)'
                      % (z.get('id'), fmt(z.get('startTime')), fmt(z.get('endTime')),
                         jget(z, 'prize', 'name', default='?'), jget(z, 'prize', 'id', default='?')))
        # 资格：当日已签
        elig = q('todayMarkStatus')
        marked = jget(elig, 'data', 'markStatus', default='') == 'marked'
        if not marked and not self.cfg.dry_run:
            self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/mark31/domark',
                         {'date': time.strftime('%Y%m%d')})
            marked = jget(q('todayMarkStatus'), 'data', 'markStatus', default='') == 'marked'
        self.note('秒杀', '资格（当日签到）：%s' % ('已具备' if marked else '未获取'))
        # 选场：进行中 > 下一场（等待受 CMCC_SECKILL_WAIT_MIN 限制）
        now_srv = int(time.time() * 1000) + offset
        cur = next((z for z in zones if int(z.get('startTime', 0)) <= now_srv <= int(z.get('endTime', 0))), None)
        zone, wait_s = cur, 0
        if cur is None:
            nxt = sorted((z for z in zones if int(z.get('startTime', 0)) > now_srv),
                         key=lambda z: int(z['startTime']))
            if not nxt:
                self.note('秒杀', '无进行中/未开始的场次'); return
            zone = nxt[0]
            wait_s = (int(zone['startTime']) - now_srv) / 1000.0
            limit = float(self.cfg.cmcc_seckill_wait_min or 0) * 60
            if limit <= 0 or wait_s > limit:
                self.note('秒杀', '下一场 %s 开抢（%.1f 分钟后）——本次不等待'
                          % (fmt(zone['startTime']), wait_s / 60))
                return
            self.note('秒杀', '等待 %.1f 分钟到 %s 开抢…' % (wait_s / 60, fmt(zone['startTime'])))
            time.sleep(max(0.0, wait_s - 0.2))
        if self.cfg.dry_run:
            self.note('秒杀', '[dry] 跳过 redeem（目标：%s）' % jget(zone, 'prize', 'name', default='?'))
            return
        payload = {'secId': str(zone.get('id')), 'prizeId': str(jget(zone, 'prize', 'id', default=''))}
        tries = max(1, int(float(self.cfg.cmcc_seckill_max_tries or 3)))
        for i in range(1, tries + 1):
            r = q('redeem', payload)
            code, status, msg = r.get('code'), r.get('status'), (r.get('msg') or '')
            self.note('秒杀', '第%d发：code=%s status=%s msg=%s' % (i, code, status, msg[:40]))
            if is_success(r):
                self.note('秒杀', '★ 抢到：%s —— 到 App「我的奖品」核销'
                          % jget(r, 'data', 'prize', 'name', default='券'))
                return
            if status in self.SECKILL_STOP:
                self.note('秒杀', '终态停手：%s（%s）' % (status, self.SECKILL_STOP[status]))
                return
            time.sleep(1.0)
        self.note('秒杀', '%d 发未中，按纪律停手（不做高频连发）' % tries)

    # ------------------------------------------------ ⑧ 福利营销（读）
    def task_hlw(self):
        self.sso.session('hlw', ACTS['hlw']['entry'])
        r = self.sso.api('hlw', 'GET', '/hlwyxhdhub/api/wedrecharge/_pub/rec_login',
                         {'sysSource': 'sys_app'})
        self.note('福利营销', 'rec_login：%s mobile=%s' % (r.get('code'),
                                                          jget(r, 'data', 'mobile', default='?')))
        r2 = self.sso.api('hlw', 'POST', '/hlwyxhdhub/api/wedrecharge/queryActivityInfo')
        self.note('周三充值日', '活动信息：%s %s' % (r2.get('code'), r2.get('msg')))

    def _coc3_headers(self):
        tok = self.coc3_token or ''
        return {'Accept': 'application/json, text/plain, */*',
                'Origin': 'https://dev.coc.10086.cn',
                'Referer': 'https://dev.coc.10086.cn/coc3/canvas/rightsmarket-h5-canvas/online/home',
                'X-Requested-With': APP_PKG, 'phone': tok, 'token': tok}

    def coc3_ensure(self, force=False):
        """coc3 免扫码换票：ext + userLoginV2(ticketType=11) 换 COC3_LOGIN_TOKEN，缓存 15 分钟。"""
        cache = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '.coc3_token_auto.json')
        try:
            d = json.load(open(cache, encoding='utf-8'))
            if d.get('phone') == self.cfg.cmcc_phone and \
                    time.time() - d.get('at', 0) < 15 * 60 and d.get('token'):
                self.coc3_token = d['token']
                return True
        except Exception:  # noqa: BLE001
            pass
        ext = self._fresh_ext('056002')
        if not ext:
            if self.cfg.cmcc_coc3_token:
                self.coc3_token = self.cfg.cmcc_coc3_token  # 回退：配置的扫码 token
                self.note('权益市场', 'ext 现签失败，用配置的 coc3 token（可能已过期）')
                return True
            return False
        body = {'ticket': ext, 'ticketType': 11, 'businessType': 'RIGHTS_MARKET',
                'wxoaType': '', 'm1': '', 'm2': '', 's1': '', 's2': '', 'from': ''}
        r = self.http.request('POST',
                              'https://dev.coc.10086.cn/coc3/gr/login/ssoRights/userLoginV2',
                              headers={'Accept': 'application/json, text/plain, */*',
                                       'Content-Type': 'application/json;charset=UTF-8',
                                       'Origin': 'https://dev.coc.10086.cn',
                                       'Referer': 'https://dev.coc.10086.cn/coc3/canvas/'
                                                  'rightsmarket-h5-canvas/online/home',
                                       'X-Requested-With': APP_PKG},
                              body=body)
        d = (r.get('json') or {}).get('data') or {}
        tok = d.get('token') if isinstance(d, dict) else None
        if not tok:
            self.note('权益市场', '免扫码换票失败 code=%s %s'
                      % ((r.get('json') or {}).get('code'), (r.get('raw_text') or '')[:60]))
            if self.cfg.cmcc_coc3_token:
                self.coc3_token = self.cfg.cmcc_coc3_token
                return True
            return False
        self.coc3_token = tok
        self.note('权益市场', '免扫码换票成功（%s，token %s…）'
                  % (d.get('mobileMask'), tok[:10]))
        try:
            json.dump({'phone': self.cfg.cmcc_phone, 'token': tok,
                       'refreshToken': d.get('refreshToken'),
                       'mobileMask': d.get('mobileMask'), 'at': time.time(),
                       'how': 'ext(056002)+userLoginV2(ticketType=11)'},
                      open(cache, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass
        return True

    # ------------------------------------------------ ⑨ coc3 权益市场（ext 换票，免扫码）
    def task_coc3(self):
        base = 'https://dev.coc.10086.cn'
        if not (self.coc3_ensure() and self.coc3_token):
            self.note('权益市场', '无 coc3 token（免扫码与配置均失败），跳过'); return
        ck = 'COC3_LOGIN_TOKEN=%s' % self.coc3_token
        h = self._coc3_headers()
        r = self.http.request('GET', base + '/coc3/coc3-market-activity/arrange/getProByActId',
                              headers=h,
                              params={'activityId': '17453', 'batchId': '0', 'showLimit': '3'},
                              cookies=ck)
        name = jget(r.get('json'), 'data', 'name', default='')
        self.note('权益市场', '活动详情 17453：%s' % (name or (r.get('raw_text') or '')[:60]))
        # ② 抽奖剩余次数（17451）
        r2 = self.http.request('POST', base + '/coc3/coc3-market-activity/cmpp-manage/lottery/'
                               'residueLotteryCount', headers=h,
                               body={'activityId': 17451}, cookies=ck)
        self.note('权益市场', '抽奖剩余次数(17451)：%s' %
                  jget(r2.get('json'), 'data', default=(r2.get('raw_text') or '')[:50]))
        # ③ 权益豆余额
        r3 = self.http.request('POST', base + '/coc3/gr-integral-ability/api/external/score/'
                               'digIntel/getBalance', headers=h, body={}, cookies=ck)
        self.note('权益市场', '权益豆余额：%s' %
                  jget(r3.get('json'), 'data', default='?'))
        rs = self.http.request('POST', base + '/coc3/coc3-market-activity/cmpp-manage/task/'
                               'getTaskSignInfo', headers=h, body={'taskId': 2999}, cookies=ck)
        sd = jget(rs.get('json'), 'data', default={}) or {}
        self.note('权益市场', '月月领签到：%s（累签 %s 次）'
                  % ('今日已签' if sd.get('signToday') else '今日未签',
                     sd.get('accumulateCount', '?')))
        rt = self.http.request('POST', base + '/coc3/coc3-market-activity/cmpp-manage/task/'
                               'getTaskList', headers=h,
                               body={'taskIdList': [3040, 3041, 3026], 'scene': 0,
                                     'moduleSource': 0, 'queryType': 0}, cookies=ck)
        vos = jget(rt.get('json'), 'data', 'taskQueryTimeVOS', default=[]) or []
        if vos:
            self.note('权益市场', '任务进度：%s' % ['%s %d/%d' % (v.get('taskId'),
                                                              (v.get('completedNum') or 0),
                                                              (v.get('totalNum') or 0))
                                                     for v in vos])
        visits = [('P00000139704', '26xysrqtyld'), ('P00000137248', '2026ydchl'),
                  ('P00000123846', '2026xyyxyddhl'), ('P00000067264', 'ydagrjh'),
                  ('P00000123844', 'wljxmh'), ('P00000067264', '26xysrqtyld')]
        ok = 0
        if not self.cfg.dry_run:
            for ch, code in visits:
                ref = ('%s/coc3/canvas/rightsmarket-h5-canvas/online/%s?channelCode=%s&pageRecorded=true'
                       % (base, code, ch))
                rv = self.http.request('POST', base + '/coc3/coc3-market-activity/cmpp-manage/'
                                       'task/visit', headers=dict(h, Referer=ref),
                                       body={'channelCode': ch, 'visitUrl': code}, cookies=ck)
                msg = str(jget(rv.get('json'), 'data', default=''))
                if '不在配置范围内' not in msg:
                    ok += 1
                self.rest(0.8, 1.5)
            self.note('权益市场', '任务浏览：%d/%d 组被接受' % (ok, len(visits)))
        else:
            self.note('权益市场', '[dry] 跳过任务浏览（%d 组）' % len(visits))

    COC3_LOTTERY_ACTS = ((17524, 'AI你幸运礼', 'count'), (17761, '一豆赢惊喜', 'bean'),
                         (17451, '三日签·流量', 'count'), (17452, '三日签·话费', 'count'),
                         (17453, '三日签·大额话费', 'count'))

    # ------------------------------------------------ ⑨b coc3 0 元单（抽奖 / 权益豆）
    def task_coc3_draw(self):
        """coc3 0 元单抽奖：有次数就抽、有权益豆也抽；每活动单次最多 5 抽、间隔 1.5s，遇风控立即停。"""
        if not self.coc3_ensure(force=True):
            self.note('coc3抽奖', '换票失败，跳过'); return
        base = 'https://dev.coc.10086.cn'
        h = self._coc3_headers()
        ck = 'COC3_LOGIN_TOKEN=%s' % self.coc3_token
        wpx = (getattr(self.cfg, 'cmcc_wasm_params', '') or '').strip()
        if not wpx:
            wpx = self._harvest_wasm_params()
            if wpx:
                self.note('coc3抽奖', 'wasmParams 已自动收割（%d 字符）' % len(wpx))
        h_submit = dict(h)
        if wpx:
            h_submit['wasmParams'] = wpx
        beans = None
        rb = self.http.request('POST', base + '/coc3/gr-integral-ability/api/external/score/'
                               'digIntel/getBalance', headers=h, body={}, cookies=ck)
        try:
            beans = int(str(jget(rb.get('json'), 'data', default='0')))
        except Exception:  # noqa: BLE001
            beans = 0
        self.note('coc3抽奖', '权益豆余额 %s' % beans)
        for aid, name, mode in self.COC3_LOTTERY_ACTS:
            # 剩余次数
            r = self.http.request('POST', base + '/coc3/coc3-market-activity/cmpp-manage/lottery/'
                                  'residueLotteryCount', headers=h, body={'activityId': aid}, cookies=ck)
            n = jget(r.get('json'), 'data', default=0)
            try:
                n = int(n)
            except Exception:  # noqa: BLE001
                n = 0
            # 抽奖 sku（currentLottery 的 skuId；历史样本：17761→95408）
            r2 = self.http.request('POST', base + '/coc3/coc3-market-activity/cmpp-manage/lottery/'
                                   'currentLottery', headers=h,
                                   body={'activityId': aid, 'scene': 1}, cookies=ck)
            sku = jget(r2.get('json'), 'data', 'skuId', default=None)
            if sku is None and aid == 17761:
                sku = 95408
            if mode == 'bean':
                can = min(n if n else 99, beans or 0, 5)
            else:
                can = min(n, 5)
            if n == 0 and mode == 'count':
                continue
            if mode == 'bean' and (beans or 0) <= 0:
                self.note('coc3抽奖', '%s(%s)：次数=%s 但权益豆不足，跳过' % (name, aid, n or '不限'))
                continue
            if not sku:
                self.note('coc3抽奖', '%s(%s)：次数=%s 但无抽奖 sku（currentLottery 未下发），跳过'
                          % (name, aid, n))
                continue
            self.note('coc3抽奖', '%s(%s)：可抽 %d 次（sku=%s）%s'
                      % (name, aid, can, sku, '【演练不抽】' if self.cfg.dry_run else ''))
            if self.cfg.dry_run:
                continue
            for _ in range(can):
                body = {'skuId': str(sku), 'channelCode': 'P00000011939',
                        'selfChannelCode': 'P00000123844', 'smsCode': '', 'dealType': 0,
                        'basePayType': 3, 'basePayAmount': 0,
                        'actId': aid, 'activityId': aid}
                ep = '/coc3/coc3-market-order/api/external/mixedOrder'
                if mode == 'bean':
                    body.update({'basePayType': 32, 'mixPaymentInfo': {'digitalBean': 1}})
                    ep = '/coc3/coc3-market-order/api/external/secKill/cmtOrder'
                rd = self.http.request('POST', base + ep, headers=h_submit, body=body, cookies=ck)
                jd = rd.get('json') or {}
                raw = (rd.get('raw_text') or '')[:120]
                # 中奖与风控判定
                txt = json.dumps(jd, ensure_ascii=False)
                if 'risk' in txt and jd.get('data') and isinstance(jd.get('data'), dict)                         and jd['data'].get('risk'):
                    self.note('coc3抽奖', '⛔ 触发风控：%s —— 立即停手（本活动）'
                              % str(jd['data'].get('explain'))[:40])
                    break
                prize = jget(jd, 'data', 'prizeName', default=None)                     or jget(jd, 'data', 'orderInfo', 'prizeName', default=None)                     or jget(jd, 'data', 'skuName', default=None)
                if prize:
                    self.note('coc3抽奖', '★ 中奖：%s' % prize)
                else:
                    self.note('coc3抽奖', '提交返回：%s %s'
                              % (str(jd.get('resultCode') or jd.get('code')), str(jd.get('msg'))[:40] or raw))
                self.rest(1.2, 2.0)
                beans = max(0, (beans or 0) - 1) if mode == 'bean' else beans

    # ------------------------------------------------ ⑩ coc web6 · 7 日打卡
    def task_coc7(self):
        ext = self._fresh_ext('056002')
        if not ext:
            self.note('7日打卡', '拿不到 ext，跳过'); return
        if _aes() is None:
            self.note('7日打卡', '缺 pycryptodome（aurora 加密），跳过'); return
        body, extra = coc_encrypt({'verifyType': 18, 'ext': ext})
        h = {'Accept': 'application/json, text/plain, */*',
             'Content-Type': 'application/json;charset=utf-8',
             'Origin': 'https://dev.coc.10086.cn',
             'Referer': 'https://dev.coc.10086.cn/coc/web6/dailyLoginEvent/'}
        h.update(extra)
        r = self.http.request('POST', 'https://dev.coc.10086.cn/coc/user/loginCheck',
                              headers=h, body=body)
        tok = (r.get('set_cookies') or {}).get('User-Token')
        if not tok:
            self.note('7日打卡', 'User-Token 获取失败 %s' % (r.get('text') or '')[:60]); return
        self.coc_token = tok
        ck = 'User-Token=%s' % tok
        base = 'https://dev.coc.10086.cn'
        h2 = {'Accept': 'application/json, text/plain, */*',
              'Content-Type': 'application/json;charset=utf-8', 'Origin': base,
              'Referer': base + '/coc/web6/dailyLoginEvent/', 'X-Requested-With': APP_PKG}
        r1 = self.http.request('POST', base + '/coc/activities/prize/consecutiveClockInQuery',
                               headers=h2, body={'activityId': COC_7DAY_ACTIVITY_ID}, cookies=ck)
        days = jget(r1.get('json'), 'data', 'clockInDateList', default=[])
        self.note('7日打卡', '已打卡 %s' % days)
        if self.cfg.dry_run:
            self.note('7日打卡', '[dry] 跳过签到'); return
        r2 = self.http.request('POST', base + '/coc/activities/prize/templatePrize',
                               headers=h2,
                               body={'activityId': COC_7DAY_ACTIVITY_ID,
                                     'touchCode': COC_7DAY_TOUCH_CODE}, cookies=ck)
        j2 = r2.get('json') or {}
        if str(j2.get('code')) == '0':
            r3 = self.http.request('POST', base + '/coc/activities/prize/consecutiveClockInQuery',
                                   headers=h2, body={'activityId': COC_7DAY_ACTIVITY_ID}, cookies=ck)
            self.note('7日打卡', '签到成功，已打卡 %s'
                      % jget(r3.get('json'), 'data', 'clockInDateList', default=[]))
        else:
            self.note('7日打卡', '签到：%s %s' % (j2.get('code'), j2.get('msg')))

    def _fresh_ext(self, target_source_id):
        """用 qwhdhub 会话现签 ext（QWHD 会话由 SSO 换得）。"""
        try:
            self.sso.session('mark31', ACTS['mark31']['entry'])
        except Exception:  # noqa: BLE001
            return self.ext
        r = self.http.request('GET', BASE + '/qwhdhub/user/getYDRZToken',
                              headers={'Accept': '*/*', 'Origin': BASE,
                                       'Referer': self.sso.acts['mark31']['referer'],
                                       'login-check': '1', 'X-Requested-With': 'XMLHttpRequest'},
                              params={'targetSourceId': target_source_id},
                              cookies=self.sso.acts['mark31']['cookie'])
        v = jget(r.get('json'), 'data')
        return v if isinstance(v, str) and v else self.ext

    # ------------------------------------------------ ⑪ 趣玩贵州（读）
    def task_migu(self):
        ext = self._fresh_ext('030019') or self._fresh_ext('001191')
        if not ext or _aes() is None:
            self.note('趣玩贵州', '无 ext 或缺 pycryptodome，跳过'); return
        h = {'Content-Type': 'application/json', 'Accept': '*/*',
             'Referer': 'https://qwh.migufun.com:8000/quwanhe/?CHANNELID=qxq',
             'X-Requested-With': APP_PKG}
        url = 'https://qwh.migufun.com:8000/qwh_api/auth/cas/yd/uniTokenValidate'
        r = self.http.request('POST', url, headers=h,
                              raw=aes_ecb_enc(MIGU_KEY, json.dumps({'token': ext},
                                                                    ensure_ascii=False)))
        js = self._migu_json(r)
        tok = jget(js, 'data', 'token', default='')
        self.note('趣玩贵州', 'ext→JWT：%s jwt=%s' % (jget(js, 'code', default='?'),
                                                     (tok[:14] + '…') if tok else '无'))

    @staticmethod
    def _migu_json(r):
        js = r.get('json')
        if isinstance(js, dict):
            return js
        txt = (r.get('raw_text') or '').strip().strip('"')
        if txt and _aes() is not None:
            try:
                return json.loads(aes_ecb_dec(MIGU_KEY, txt))
            except Exception:  # noqa: BLE001
                return None
        return None

    # ------------------------------------------------ ⑫ 全球通（签到 + 兑换驱动）
    CMIC_SSO = '/2016tyjf/xhmqqthy/new/ssoTokenLogin.action'
    CMIC_REF = ('https://qqt.cmicrwx.cn/2016tyjf/xhmqqthy/res/wap/'
                'xdrNormal.html?sspId=75714')

    def _cmic_fresh_cookie(self):
        """现签全球通 Cookie：判据是本次响应新发，失败返回空串。"""
        ext = self._fresh_ext('001191')
        if not ext:
            return ''
        r = self.http.request('GET', 'https://qqt.cmicrwx.cn' + self.CMIC_SSO,
                              headers={'User-Agent': UA, 'Accept': '*/*',
                                       'Referer': self.CMIC_REF},
                              params={'sspId': '75900', 'QWHD_YDRZ': '001191_token',
                                      'token': ext,
                                      'targetUrl': 'https://qqt.cmicrwx.cn' + self.CMIC_SSO,
                                      'yx': ''},
                              allow_redirects=False)
        at = (r.get('set_cookies') or {}).get('access_token') or ''
        if at:
            return 'access_token=%s' % at
        return ''

    def task_cmic(self):
        """`sign/info` 回 `status:"1"`（可签）时 POST `sign/click`；会员资格是业务门禁 （`本活动仅全球通客户可参与`）则如实报。"""
        ck = self._cmic_fresh_cookie()
        if not ck:
            ck = self.cfg.cmcc_cmic_cookie
            self.note('全球通', '现签失败，回落配置 Cookie（可能已过期）')
        h = {'Accept': 'application/json, text/plain, */*', 'login-check': '1',
             'X-Requested-With': APP_PKG, 'User-Agent': UA,
             'Referer': self.CMIC_REF}
        r = self.http.request('GET', 'https://qqt.cmicrwx.cn/2016tyjf/xhmqqthy/sign/info',
                              headers=h, cookies=ck)
        js = r.get('json') or {}
        st = jget(js, 'status', default='?')
        self.note('全球通星动日', 'status=%s %s' % (st, jget(js, 'msg', default='')))
        if st == '1' and not self.cfg.dry_run:
            self.rest(1.0, 2.0)
            rc = self.http.request('POST',
                                   'https://qqt.cmicrwx.cn/2016tyjf/xhmqqthy/sign/click',
                                   headers=h, cookies=ck)
            jc = rc.get('json') or {}
            self.note('全球通星动日', '签到提交：%s %s'
                      % (jget(jc, 'status', default='?'), jget(jc, 'msg', default='')))


    HN_BASE = 'https://wap.ha.10086.cn'
    HN_REFERER = HN_BASE + '/hnmccClientWap/act_h5/html/h5/globalFirst/index'
    HN_SIGN_BASE = HN_BASE + '/hnmccClientWap/signLottery'
    HN_SIGN_CHANNEL = 'channel_YHY'

    def _henan_cookie(self):
        """河南免登换 `hncmjsSSOCookie`（`task_henan` / `task_henan_sign` 共用）。失败返回 ''。"""
        ext = self._fresh_ext('035001')
        if not ext:
            return ''
        r = self.http.request('POST', self.HN_BASE + '/h5-rest/login/unifyLogin',
                              headers={'User-Agent': UA, 'Content-Type': 'application/json;charset=UTF-8',
                                       'Accept': 'application/json', 'Origin': self.HN_BASE,
                                       'Referer': self.HN_REFERER},
                              body={'token': ext, 'sourceId': '', 'frontCostTime': 1200})
        j = r.get('json') or {}
        d = j.get('data') or {}
        ck = ((r.get('set_cookies') or {}).get('hncmjsSSOCookie') or (d.get('cookie') or ''))
        if str(j.get('code')) != '1' or not ck:
            return ''
        return 'hncmjsSSOCookie=%s' % ck if '=' not in str(ck) else str(ck)

    def task_henan_sign(self):
        """达门槛时会自动 `click.do` 抽奖（写动作，幂等由服务端判）。"""
        if self.cfg.cmcc_province_code != '371':
            self.note('河南签到', '非河南归属（省=%s），跳过' % self.cfg.cmcc_province_code)
            return
        ck = self._henan_cookie()
        if not ck:
            self.note('河南签到', '免登失败（035001 未签发或 unifyLogin 失败）')
            return
        hd = {'User-Agent': UA, 'X-Requested-With': APP_PKG,
              'Accept': 'application/json, text/plain, */*',
              'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'Referer': self.HN_SIGN_BASE + '/index.html'}
        r = self.http.request('POST', self.HN_SIGN_BASE + '/init.do', headers=hd, body='', cookies=ck)
        j = r.get('json') or {}
        code = str(j.get('code'))
        self.note('河南签到', 'init: code=%s %s' % (code, str(j.get('msg'))[:60]))
        if code != '0':
            self.note('河南签到', '无资格 / 暂不可签（code=%s）' % code)
            return
        self.rest(0.8, 1.6)
        r2 = self.http.request('POST', self.HN_SIGN_BASE + '/click.do', headers=hd,
                               body='channel=%s' % self.HN_SIGN_CHANNEL, cookies=ck)
        j2 = r2.get('json') or {}
        self.note('河南签到', 'click: code=%s %s' % (j2.get('code'), str(j2.get('msg'))[:60]))

    HN_RD_BASE = 'https://wap.ha.10086.cn/h5-act/tuesDayJt'
    HN_RD_PAGE = ('https://wap.ha.10086.cn/hnmccClientWap/act_h5/html/h5/activity/'
                  'rechargeDayJT?channel=channel_YJ')

    def task_henan_rechargeday(self):
        """周二充值日：init 查状态 → click 抽奖 → share2 补次数 → 再抽 → prizeList 汇总。"""
        if self.cfg.cmcc_province_code != '371':
            self.note('周二充值日', '非河南归属（省=%s），跳过' % self.cfg.cmcc_province_code)
            return
        ck = self._henan_cookie()
        if not ck:
            self.note('周二充值日', '免登失败（035001 未签发或 unifyLogin 失败）')
            return
        hd = {'User-Agent': UA, 'Accept': 'application/json, text/plain, */*',
              'Content-Type': 'application/json;charset=UTF-8',
              'Origin': 'https://wap.ha.10086.cn', 'Referer': self.HN_RD_PAGE}

        def call(path, method='POST', q=None):
            u = self.HN_RD_BASE + path
            if q:
                u += '?' + '&'.join('%s=%s' % kv for kv in q.items())
            r = self.http.request(method, u, headers=hd, cookies=ck, body={})
            return (r.get('json') or {})

        # ① init：星期与状态
        j = call('/init', q={'channel': 'channel_YJ', 'version': '', 'code': ''})
        if str(j.get('code')) != '1':
            self.note('周二充值日', 'init 失败：%s %s' % (j.get('code'), str(j.get('msg'))[:40]))
            return
        d = j.get('data') or {}
        fettle = str(d.get('fettle') or '')
        self.note('周二充值日', 'init：今日=%s（status=%s）' % (fettle, d.get('status')))
        if fettle != '星期二':
            self.note('周二充值日', '📅 非周二，仅查询不消耗（奖品：%s）'
                      % str((call('/prizeList').get('data') or {}).get('prizeList'))[:60])
            return
        prizes = []
        self.rest(1.0, 1.8)
        j2 = call('/click', q={'channel': 'channel_YJ', 'version': '', 'code': ''})
        d2 = j2.get('data') or {}
        if str(j2.get('code')) == '1' and str(d2.get('status')) == '6':
            prizes.append(str(d2.get('prizeName')))
            self.note('周二充值日', '🎉 首抽中奖：%s' % d2.get('prizeName'))
        else:
            self.note('周二充值日', '首抽：status=%s %s' % (d2.get('status'), str(d2.get('msg'))[:30]))
        # ③ share2 补卡 + 再抽（有界：一轮）
        self.rest(1.0, 1.6)
        if not self.cfg.dry_run:
            js2 = call('/share2', method='GET')
            if str(js2.get('code')) == '1':
                self.rest(1.0, 1.8)
                j3 = call('/click', q={'channel': 'channel_YJ', 'version': '', 'code': ''})
                d3 = j3.get('data') or {}
                if str(j3.get('code')) == '1' and str(d3.get('status')) == '6' \
                        and str(d3.get('prizeName') or '') != 'None':
                    prizes.append(str(d3.get('prizeName')))
                    self.note('周二充值日', '🎉 补抽中奖：%s' % d3.get('prizeName'))
                if str(d3.get('num')) == '0':
                    self.note('周二充值日', '今日次数已用尽（num=0），停手 🛑')
        # ④ 奖品汇总
        self.rest(0.8, 1.4)
        jp = call('/prizeList')
        pl = ((jp.get('data') or {}).get('prizeList')) or []
        today = time.strftime('%Y%m%d')
        todays = [p for p in pl if str(p.get('C_GIFT')) == today]
        self.note('周二充值日', '🏆 今日奖品 %d 笔：%s'
                  % (len(todays), '；'.join(str(p.get('C_PRIZE_NAME')) for p in todays[:4])))

    def task_henan(self):
        """河南专属面：免登换会话 + 业务接口连通性探测（globalFirst 落地页）。"""
        if self.cfg.cmcc_province_code != '371':
            self.note('河南专属', '非河南归属（省=%s），跳过' % self.cfg.cmcc_province_code)
            return
        ext = self._fresh_ext('035001')
        if not ext:
            self.note('河南专属', '035001 签发失败（该源仅对河南号开放）'); return
        r = self.http.request('POST', self.HN_BASE + '/h5-rest/login/unifyLogin',
                              headers={'User-Agent': UA,
                                       'Content-Type': 'application/json;charset=UTF-8',
                                       'Accept': 'application/json', 'Origin': self.HN_BASE,
                                       'Referer': self.HN_REFERER},
                              body={'token': ext, 'sourceId': '', 'frontCostTime': 1200})
        j = r.get('json') or {}
        d = j.get('data') or {}
        ck = ((r.get('set_cookies') or {}).get('hncmjsSSOCookie')
              or (d.get('cookie') or ''))
        if str(j.get('code')) != '1' or not ck:
            self.note('河南专属', '免登失败 code=%s %s' % (j.get('code'), str(j.get('msg'))[:50]))
            return
        self.note('河南专属', '免登成功（%s，会话已取得）' % d.get('phone'))
        ck = 'hncmjsSSOCookie=%s' % ck if '=' not in str(ck) else str(ck)
        # 业务连通性：globalFirst 落地页配置（返回 code=1 即通）
        for tag, path in (('全球通权益包', '/h5-act/gsmploywrap'),
                          ('新客首射礼', '/h5-act/globalrevup')):
            r2 = self.http.request('POST', self.HN_BASE + path,
                                   headers={'User-Agent': UA,
                                            'Content-Type': 'application/json;charset=UTF-8',
                                            'Accept': 'application/json', 'Origin': self.HN_BASE,
                                            'Referer': self.HN_REFERER},
                                   body={}, cookies=ck)
            j2 = r2.get('json')
            if j2 is None:
                self.note('河南专属', '%s：http=%s（非 JSON，页面型端点）' % (tag, r2.get('http')))
            else:
                self.note('河南专属', '%s：code=%s %s' % (tag, j2.get('code'),
                                                          str(j2.get('msg'))[:40]))
            self.rest()
        self.note('河南专属', '业务端点以页面型为主（保险/权益营销），任务型河南活动待 App 内抓包定位')

    # ------------------------------------------------ ⑬ 全量只读扫描（其余已探测端点）
    def task_native(self):
        """App 原生面只读汇总：把 Native.PATHS 里登记的只读端点各调一次。"""
        if not self.cfg.cmcc_app_token:
            self.note('原生面', '跳过：未配置 CMCC_APP_TOKEN')
            return
        ph = self.cfg.cmcc_phone
        base = {'cellNum': ph}
        prov = {'cellNum': ph, 'provinceCode': self.cfg.cmcc_province_code,
                'cityCode': self.cfg.cmcc_city_code}
        #: `30344`（个人资产）额外要 `location`
        prov_loc = dict(prov, location=0)
        expired = 0
        for code, tag, body in (('50514', 'GSM权益四计数', base), ('20084', '5G卡', base),
                                ('30301', '权益推送', base), ('30324', '孝心卡', prov),
                                ('30645', '热门活动页配置', prov), ('20007', '个人信息', base),
                                ('20002', '积分', base),
                                ('10000', '客户端配置面', base), ('30313', '关爱版·时钟', prov),
                                ('30314', '关爱版·图标', prov), ('30323', '关爱版·横幅', prov),
                                ('30679', '关爱版·抽屉弹窗', prov), ('50375', '权益VIP列表', base),
                                ('30690', '福利组件广告', prov), ('30344', '个人资产', prov_loc),
                                ('50491', '会员信息', base), ('50495', '生日特权', base),
                                ('50426', '每日健康', base), ('50047', '邮箱未读', base),
                                ('50492', '头像编辑', base), ('50493', '头像列表', base),
                                ('50541', '数字头像', prov), ('50546', '数字角色令牌', prov),
                                ('50415', '世界杯任务', base)):
            if code not in Native.PATHS:
                continue
            try:
                r = self.native.call(code, body)
                if str(r['retCode']) == '410000':
                    # 会话空闲超时 ⇒ 免密续期（60012）后**重试一次**（自愈，不再需人工重新登录）
                    if self.native.auto_login(ph).get('ok'):
                        self.rest(0.6, 1.2)
                        r = self.native.call(code, body)
                if str(r['retCode']) == '410000':
                    expired += 1
                self.note('原生面', '%s(%s) %s | %s' % (code, tag, r['retCode'],
                                                       str(r['data'])[:70]))
            except Exception as exc:  # noqa: BLE001
                self.note('原生面', '%s(%s) 异常：%s' % (code, tag, str(exc)[:60]))
            self.rest(0.6, 1.2)
        if expired:
            self.note('原生面', '⚠ 有 %d 个端点回 `410000`（会话空闲超时且免密续期未成功）'
                                '——通常是 `CMCC_APP_TOKEN` 已过期，请更新该票据（或先用 login 台跑一次）'
                      % expired)

    def task_discover(self):
        """**活动发现**：从原生 `30645`（热门活动页配置）拉出**当前在投的活动**清单。"""
        if not self.cfg.cmcc_app_token:
            self.note('活动发现', '跳过：未配置 CMCC_APP_TOKEN')
            return
        r = self.native.call('30645', {'cellNum': self.cfg.cmcc_phone,
                                       'provinceCode': self.cfg.cmcc_province_code,
                                       'cityCode': self.cfg.cmcc_city_code})
        if r['retCode'] != '000000' or not isinstance(r['data'], dict):
            self.note('活动发现', '取活动页配置失败：%s %s' % (r['retCode'], r['retDesc']))
            return
        found = []
        for area in r['data'].get('areaList') or []:
            for mod in area.get('moduleList') or []:
                for adv in mod.get('adverList') or []:
                    url = adv.get('linkUrl') or adv.get('jumpUrl') or ''
                    if url:
                        found.append((adv.get('markName') or adv.get('title') or '?', url))
        self.note('活动发现', '当前在投活动 **%d 个**' % len(found))
        for name, url in found[:10]:
            self.note('  活动', '%s → %s' % (name, url[:96]))

    MCLOUD_GP = 'https://gp.mcloud.139.com'
    MCLOUD_ACT = 'https://m.mcloud.139.com/portal/hcyview/index.html?path=National_AnnualRewarding'
    TYRZ_SALT = 'sekaMdYYLIZfbCfm'

    def _mcloud_login(self):
        """成功回 `{token:<JWT>, mobile, userDomainId}`， 之后所有 139 端点带 **`jwtToken` 头**。失败返回 ''。`CMCC_MCLOUD_NO_CACHE=1` 可强制现签。"""
        import hashlib
        import uuid as _uuid
        cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.mcloud_jwt_cache.json')
        if not str(getattr(self.cfg, 'cmcc_mcloud_no_cache', '') or '').strip() == '1':
            try:
                d = json.load(open(cache, encoding='utf-8'))
                if (d.get('phone') == self.cfg.cmcc_phone
                        and time.time() - d.get('at', 0) < 50 * 60 and d.get('jwt')):
                    return d['jwt']
            except Exception:  # noqa: BLE001
                pass
        try:
            jwt = self._mcloud_login_fresh()
        except Exception:  # noqa: BLE001
            jwt = ''
        if jwt:
            try:
                json.dump({'phone': self.cfg.cmcc_phone, 'jwt': jwt, 'at': time.time()},
                          open(cache, 'w', encoding='utf-8'))
            except Exception:  # noqa: BLE001
                pass
        return jwt

    def _mcloud_login_fresh(self):
        """真正走一次免登链（不读缓存）。"""
        import hashlib
        import uuid as _uuid
        s = self.sso.session('t139', BASE + '/qwhdhub/qwhdmark/1021122301')
        rr = self.http.request('GET', BASE + '/qwhdhub/user/getYDRZToken',
                               params={'targetSourceId': '001005'},
                               headers={'User-Agent': UA_NATIVE, 'login-check': '1',
                                        'X-Requested-With': 'XMLHttpRequest',
                                        'Origin': BASE,
                                        'Referer': s.get('referer', BASE + '/qwhdhub/')},
                               cookies=s.get('cookie', ''))
        ext = ((rr.get('json') or {}).get('data')) or ''
        if not ext:
            return ''
        tr = self.http.request('GET', self.MCLOUD_GP + '/ycloud/api/timestamp')
        ts = str(((tr.get('json') or {}).get('result')) or int(time.time() * 1000))
        rid, nonce = _uuid.uuid4().hex, _uuid.uuid4().hex
        sig = hashlib.md5(('%s%s%s%s%s' % (self.TYRZ_SALT, rid, ts, nonce,
                                           self.TYRZ_SALT)).encode()).hexdigest()
        r = self.http.request('POST', self.MCLOUD_GP + '/ycloud/auth-service/auth/tyrzLogin',
                              headers={'User-Agent': UA,
                                       'Content-Type': 'application/json;charset=UTF-8',
                                       'Origin': 'https://m.mcloud.139.com',
                                       'Referer': 'https://m.mcloud.139.com/portal/mobilecloud/index.html',
                                       'x-request-id': rid, 'x-timestamp': ts, 'x-nonce': nonce,
                                       'x-signature': sig},
                              body={'token': ext, 'openAccount': 0, 'sourceId': '1103',
                                    'marketName': 'newsign_139mail', 'channel': '', 'channelSrc': ''})
        j = r.get('json') or {}
        return ((j.get('result') or {}).get('token')) or '' if str(j.get('code')) == '0' else ''

    def _mcloud_hdr(self, jwt, referer=None):
        """带 139 jwtToken + tyrz 签名的公共头（referer 缺省为移动云盘首页；活动页传各自 path）。"""
        import hashlib
        import uuid as _uuid
        rid, nonce = _uuid.uuid4().hex, _uuid.uuid4().hex
        ts = str(int(time.time() * 1000))
        sig = hashlib.md5(('%s%s%s%s%s' % (self.TYRZ_SALT, rid, ts, nonce,
                                           self.TYRZ_SALT)).encode()).hexdigest()
        return {'User-Agent': UA, 'Content-Type': 'application/json;charset=UTF-8',
                'Origin': 'https://m.mcloud.139.com',
                'Referer': referer or 'https://m.mcloud.139.com/portal/mobilecloud/index.html',
                'jwtToken': jwt, 'x-request-id': rid, 'x-timestamp': ts,
                'x-nonce': nonce, 'x-signature': sig}

    def _mcloud_udi(self):
        """139 侧 userDomainId（`tyrzLogin` 响带的字段；AI_PHOTO 等任务的 param 要它）。"""
        try:
            s = self.sso.session('t139', BASE + '/qwhdhub/qwhdmark/1021122301')
            rr = self.http.request('GET', BASE + '/qwhdhub/user/getYDRZToken',
                                   params={'targetSourceId': '001005'},
                                   headers={'User-Agent': UA_NATIVE, 'login-check': '1',
                                            'X-Requested-With': 'XMLHttpRequest',
                                            'Origin': BASE,
                                            'Referer': s.get('referer', BASE + '/qwhdhub/')},
                                   cookies=s.get('cookie', ''))
            ext = ((rr.get('json') or {}).get('data')) or ''
            if not ext:
                return ''
            tr = self.http.request('GET', self.MCLOUD_GP + '/ycloud/api/timestamp')
            ts = str(((tr.get('json') or {}).get('result')) or int(time.time() * 1000))
            rid, nonce = _uuid.uuid4().hex, _uuid.uuid4().hex
            sig = hashlib.md5(('%s%s%s%s%s' % (self.TYRZ_SALT, rid, ts, nonce,
                                               self.TYRZ_SALT)).encode()).hexdigest()
            r = self.http.request('POST', self.MCLOUD_GP + '/ycloud/auth-service/auth/tyrzLogin',
                                  headers={'User-Agent': UA,
                                           'Content-Type': 'application/json;charset=UTF-8',
                                           'Origin': 'https://m.mcloud.139.com',
                                           'Referer': 'https://m.mcloud.139.com/portal/mobilecloud/index.html',
                                           'x-request-id': rid, 'x-timestamp': ts,
                                           'x-nonce': nonce, 'x-signature': sig},
                                  body={'token': ext, 'openAccount': 0, 'sourceId': '1103',
                                        'marketName': 'newsign_139mail', 'channel': '',
                                        'channelSrc': ''})
            return ((r.get('json') or {}).get('result') or {}).get('userDomainId') or ''
        except Exception:  # noqa: BLE001
            return '' 

    def task_139sign(self):
        """链：qwhdhub `getYDRZToken(001005)` → `tyrzLogin` 换 jwt → `startSignIn`（GET）签到。"""
        jwt = self._mcloud_login()
        if not jwt:
            self.note('139签到', '免登失败（ext 签发或 tyrzLogin 未过）')
            return
        self.note('139签到', '免登成功（jwt %d 字符）' % len(jwt))
        hd = self._mcloud_hdr(jwt)
        r = self.http.request('GET', self.MCLOUD_GP + '/ycloud/signin/page/startSignIn',
                              headers=hd, params={'sourceid': '1103'})
        j = r.get('json') or {}
        res = j.get('result') or {}
        if str(j.get('code')) == '0':
            self.note('139签到', '⭐ 签到成功：本次 +%s 云朵（todaySignIn=%s）'
                      % (res.get('signInPoints'),
                         res.get('todaySignIn')))
        else:
            self.note('139签到', 'startSignIn code=%s %s' % (j.get('code'), str(j.get('msg'))[:50]))
        self.rest(0.8, 1.6)
        r2 = self.http.request('GET', self.MCLOUD_GP + '/ycloud/signin/page/getCloudNum',
                               headers=hd)
        j2 = r2.get('json') or {}
        cloud = 0
        if str(j2.get('code')) == '0':
            try:
                cloud = int(j2.get('result') or 0)
            except (TypeError, ValueError):
                cloud = 0
            self.note('139签到', '云朵余额：%s' % cloud)
        hd2 = dict(self._mcloud_hdr(jwt))
        hd2['isDeviceId'] = 'true'
        dev = (getattr(self.cfg, 'cmcc_sm_device_id', '') or '').strip()
        if not dev:
            dev = self._harvest_sm_device_id()
            if dev:
                self.note('139签到', 'deviceId 已自动收割（%d 字符）' % len(dev))
        if dev:
            hd2['appVersion'] = str(getattr(self.cfg, 'cmcc_app_version', '') or ''
                                    ).replace('.', '') or '1254'
        # ① 待领云朵（`receiveV3`，体 `{client, cloudId, cloudType[, deviceId]}`）
        try:
            res3 = ((self.http.request('GET', self.MCLOUD_GP + '/ycloud/signin/page/infoV3',
                                       headers=hd2).get('json') or {}).get('result') or {})
            pend = res3.get('toReceive') or 0
            rl = res3.get('receiveList') or []
            if res3.get('signCount') is not None:
                self.note('139签到', '本月已签 %s 天（连签日历 %s 格）'
                          % (res3.get('signCount'), len(res3.get('cal') or [])))
            if pend and rl:
                self.note('139签到', '待领云朵 %s（%d 条记录）%s'
                          % (pend, len(rl), '' if dev else ' ⚠ 未配 CMCC_SM_DEVICE_ID，大概率失败'))
                got = 0
                for it in rl:
                    if self.cfg.dry_run:
                        break
                    self.rest(1.5, 2.5)
                    body = {'client': 'out', 'cloudId': str(it.get('recordId')),
                            'cloudType': it.get('cloudType')}
                    if dev:
                        body['deviceId'] = dev
                    rv = self.http.request('POST',
                                           self.MCLOUD_GP + '/ycloud/signin/page/receiveV3',
                                           headers=hd2, body=body)
                    jv = rv.get('json') or {}
                    if str(jv.get('code')) == '0':
                        got += 1
                        self.note('139签到', '⭐ 领取 +%s 云朵（余额 %s）'
                                  % (it.get('cloudNum'),
                                     (jv.get('result') or {}).get('total')))
                    else:
                        self.note('139签到', '领取回 %s %s'
                                  % (jv.get('code'), str(jv.get('msg'))[:30]))
                if got:
                    self.rest(0.8, 1.4)
                    cloud = int(((self.http.request('GET', self.MCLOUD_GP
                                                    + '/ycloud/signin/page/getCloudNum',
                                                    headers=hd).get('json') or {})
                                 .get('result')) or cloud)
                    self.note('139签到', '**云朵余额 %s**（本次 +%d 笔）' % (cloud, got))
        except Exception as exc:  # noqa: BLE001
            self.note('139签到', '待领云朵异常：%s' % str(exc)[:60])
        # ② 云朵兑换商城：exchangeList 查奖品，exchangeV3 提交（余额够就兑最便宜一项）
        try:
            self.rest(0.8, 1.5)
            res4 = ((self.http.request('GET', self.MCLOUD_GP + '/ycloud/signin/page/exchangeList',
                                       headers=hd).get('json') or {}).get('result') or {})
            items = []
            for _grp, lst in (res4 or {}).items():
                for it in (lst or []):
                    try:
                        items.append((int(it.get('POrder') or 0), it))
                    except (TypeError, ValueError):
                        pass
            items.sort(key=lambda x: x[0])
            if items:
                self.note('139签到', '兑换商城 %d 项，最便宜：%s（%d 云朵）'
                          % (len(items), str(items[0][1].get('prizeName') or items[0][1].get('name'))[:20], items[0][0]))
                afford = [x for x in items if 0 < x[0] <= cloud]
                if afford and not self.dry_run:
                    price, it = afford[0]
                    self.note('139签到', '试兑：%s（%d 云朵，余额 %d）'
                              % (str(it.get('prizeName'))[:18], price, cloud))
                    self._mcloud_exchange(jwt, it, dev)
                elif afford:
                    self.note('139签到', '【演练】可兑 %s，跳过' % str(afford[0][1].get('prizeName'))[:18])
                elif afford:
                    self.note('139签到', '【演练】可兑 %s，跳过' % str(afford[0][1].get('prizeName') or afford[0][1].get('name'))[:18])
                else:
                    self.note('139签到', '云朵 %d，距最便宜项还差 %d'
                              % (cloud, max(0, items[0][0] - cloud)))
        except Exception as exc:  # noqa: BLE001
            self.note('139签到', '兑换商城异常：%s' % str(exc)[:60])
        try:
            self.rest(0.8, 1.5)
            rt = self.http.request('POST', self.MCLOUD_GP + '/ycloud/signin/task/taskListV3',
                                   headers=hd,
                                   body={'marketname': 'newsign_139mail', 'client': 0,
                                         'clientVersion': '1254'})
            tl = (rt.get('json') or {}).get('result')
            tl = tl if isinstance(tl, list) else []
            done = [t for t in tl if str(t.get('currstep')) == '3']
            self.note('139签到', '云朵任务 %d 个（已完成 %d）' % (len(tl), len(done)))
            for t in done[:6]:
                if self.cfg.dry_run:
                    break
                self.rest(0.9, 1.6)
                rv = self.http.request('GET',
                                       self.MCLOUD_GP + '/ycloud/signin/page/receiveTask',
                                       headers=hd, params={'taskId': t.get('id')})
                jv = rv.get('json') or {}
                got = jv.get('result') if str(jv.get('code')) == '0' else None
                if got:
                    self.note('139签到', '⭐ 任务奖励 +%s 云朵（%s）'
                              % (got, str(t.get('name'))[:16]))
                else:
                    self.note('139签到', '任务「%s」无可领（%s）'
                              % (str(t.get('name'))[:16], jv.get('code')))
        except Exception as exc:  # noqa: BLE001
            self.note('139签到', '任务清单异常：%s' % str(exc)[:60])
        try:
            self.rest(0.8, 1.5)
            ES = self.MCLOUD_GP + '/ycloud/openemailsms-service/openEmailsms/'
            ti = ((self.http.request('GET', ES + 'getTaskInfo', headers=hd).get('json') or {})
                  .get('result') or {})
            t1, t2 = ti.get('taskOneInfo') or {}, ti.get('taskTwoInfo') or {}
            self.note('139邮箱', '邮件短信任务：一 %s（奖励 %s）/ 二 %s（奖励 %s，进度 %s）'
                      % (t1.get('taskStatus'), t1.get('sum'), t2.get('taskStatus'),
                         t2.get('sum'), t2.get('progress')))
            self.rest(0.8, 1.4)
            pl = ((self.http.request('GET', ES + 'prizelist', headers=hd).get('json') or {})
                  .get('result') or [])
            if pl:
                self.note('139邮箱', '奖品池 %d 项：%s'
                          % (len(pl), '；'.join(str(x.get('prizeName'))[:14] for x in pl[:3])))
            # 未领且任务已完成才试领；否则只报
            if all(str(x.get('taskStatus')) not in ('0', '2', '3')
                   for x in (t1, t2) if x):
                self.rest(1.5, 2.5)
                rw = self.http.request('GET', ES + 'reward', headers=hd)
                jr = rw.get('json') or {}
                self.note('139邮箱', '领奖 → %s %s' % (jr.get('code'), str(jr.get('msg'))[:34]))
            else:
                self.note('139邮箱', '任务未全部完成（`reward` 会回 411），不试领')
        except Exception as exc:  # noqa: BLE001
            self.note('139邮箱', '异常：%s' % str(exc)[:60])
        try:
            code_in = (getattr(self.cfg, 'cmcc_sms_code', '') or '').strip()
            for MK in ('NationalMailAppFreeSpace', 'NationalMailH5FreeSpace'):
                self.rest(0.9, 1.6)
                sw = ((self.http.request('GET', self.MCLOUD_GP + '/ycloud/api/prize/querySmsSwitch',
                                         headers=hd,
                                         params={'paramName': 'smsSwitch',
                                                 'marketName': MK}).get('json') or {})
                      .get('result'))
                pq = self.http.request('GET', self.MCLOUD_GP + '/ycloud/api/prize/query',
                                       headers=hd, params={'marketName': MK})
                jq = pq.get('json') or {}
                if str(jq.get('code')) != '0':
                    self.note('139礼包', '%s：%s' % (MK, str(jq.get('msg'))[:24]))
                    continue
                pid = jq.get('result')
                self.note('139礼包', '%s：%s（prizeId=%s，短信开关=%s）'
                          % (MK, str(jq.get('msg'))[:10], pid, sw))
                if self.cfg.dry_run:
                    continue
                if str(sw) == '1' and not code_in:
                    self.note('139礼包', '  ⚠ 需短信验证码（服务端开关=1）—— 设 CMCC_SMS_CODE 可自动领')
                    continue
                body = {'marketName': MK, 'prizeId': pid}
                if str(sw) == '1' and code_in:
                    import hashlib as _h
                    body['smsCode'] = _h.md5(code_in.encode()).hexdigest()
                self.rest(1.5, 2.5)
                ra = self.http.request('POST', self.MCLOUD_GP + '/ycloud/api/prize/accept',
                                       headers=hd, body=body)
                ja = ra.get('json') or {}
                if str(ja.get('code')) == '0':
                    self.note('139礼包', '  🎉 **领取成功**：%s' % MK)
                else:
                    self.note('139礼包', '  领取回 %s %s' % (ja.get('code'), str(ja.get('msg'))[:26]))
        except Exception as exc:  # noqa: BLE001
            self.note('139礼包', '异常：%s' % str(exc)[:60])
        try:
            self.rest(0.8, 1.4)
            rd = self.http.request('POST', self.MCLOUD_GP + '/ycloud/signin/page/doTaskPost',
                                   headers=hd2, body={'client': 'out'})
            jd = rd.get('json') or {}
            if str(jd.get('code')) != '0':
                self.note('139签到', 'doTaskPost → %s %s' % (jd.get('code'),
                                                            str(jd.get('msg'))[:30]))
        except Exception as exc:  # noqa: BLE001
            self.note('139签到', 'doTaskPost 异常：%s' % str(exc)[:50])
        # 抽奖次数（和彩云活动，同一登录态）
        self.rest(0.8, 1.6)
        r3 = self.http.request('GET', self.MCLOUD_GP + '/ycloud/bill/taskLottery/queryCount',
                               headers=hd)
        j3 = r3.get('json') or {}
        if str(j3.get('code')) == '0':
            self.note('139签到', '25周年抽奖次数：%s' % j3.get('result'))

    #: 滑块求解开关（1=用图像匹配解缺口；0=只报"需滑块"）
    MCLOUD_SLIDE_SOLVE = True

    def _mcloud_slide_offset(self, d):
        """**滑块（拼图）求解**：对 `getSlide` 的 `{picture,puzzle}` 做去均值归一化相关（NCC）， 返回 `(puzzleOffset, 相似度)`；缺 PIL/numpy 时返回 `(None, 0)`。"""
        if not self.MCLOUD_SLIDE_SOLVE:
            return None, 0.0
        try:
            import base64 as _b64
            import io as _io
            import numpy as _np
            from PIL import Image as _Im
        except Exception:  # noqa: BLE001
            return None, 0.0
        try:
            puz = _np.asarray(_Im.open(_io.BytesIO(_b64.b64decode(d['puzzle']))).convert('RGBA'))
            pic = _np.asarray(_Im.open(_io.BytesIO(_b64.b64decode(d['picture']))).convert('RGB'))
            a = puz[:, :, 3] > 8
            rows = _np.where(a.any(axis=1))[0]
            cols = _np.where(a.any(axis=0))[0]
            if not len(rows) or not len(cols):
                return None, 0.0
            h0, h1, c0, c1 = rows[0], rows[-1], cols[0], cols[-1]
            tmpl = puz[h0:h1 + 1, c0:c1 + 1, :3].astype(_np.float32)
            mk = a[h0:h1 + 1, c0:c1 + 1]
            th, tw = tmpl.shape[:2]
            H, W = pic.shape[:2]
            tm = tmpl[mk]
            tm = tm - tm.mean()
            tn = float(_np.sqrt((tm * tm).sum()))
            if tn <= 0:
                return None, 0.0
            best = (1e18, 0)
            for y in range(0, H - th + 1, 2):  # 步长 2 提速（缺口是整块）
                for x in range(0, W - tw + 1):
                    sub = pic[y:y + th, x:x + tw].astype(_np.float32)[mk]
                    s = sub - sub.mean()
                    den = float(_np.sqrt((s * s).sum())) * tn
                    if den <= 0:
                        continue
                    v = 1 - float((s * tm).sum()) / den
                    if v < best[0]:
                        best = (v, x)
            return int(best[1]), 1 - best[0]
        except Exception:  # noqa: BLE001
            return None, 0.0

    def _mcloud_exchange(self, jwt, item, dev=''):
        """云朵兑换提交：514=验证失败（重取题）；610=兑换失败；407=非目标用户。"""
        GP = self.MCLOUD_GP

        def H():
            h = self._mcloud_hdr(jwt)
            h['appVersion'] = '1254'
            return h
        for attempt in range(1, 4):
            try:
                rq = self.http.request('POST', GP + '/ycloud/auth-service/slide/getSlide',
                                       headers=H(), body={})
                jq = rq.get('json') or {}
                if str(jq.get('code')) != '0':
                    self.note('139签到', '取滑块题失败：%s %s'
                              % (jq.get('code'), str(jq.get('msg'))[:30]))
                    return False
                off, sim = self._mcloud_slide_offset(jq.get('result') or {})
                if off is None:
                    self.note('139签到', '兑换需**滑块验证**且本地无 PIL/numpy 可解 —— '
                                         '装 `pillow numpy` 后本任务会**自动解并兑换**')
                    return False
                self.rest(1.2, 2.0)
                body = {'prizeId': item.get('prizeId'), 'client': 'out',
                        'clientVersion': '1254', 'puzzleOffset': off, 'smsCode': ''}
                if dev:
                    body['deviceId'] = dev
                rx = self.http.request('POST', GP + '/ycloud/signin/page/exchangeV3',
                                       headers=H(), body=body)
                jx = rx.get('json') or {}
                code = str(jx.get('code'))
                if code == '0':
                    res = jx.get('result') or {}
                    self.note('139签到', '🎉 **兑换成功**：%s（offset=%d 相似度 %.3f，oid=%s）'
                              % (str(item.get('prizeName'))[:20], off, sim, res.get('oid')))
                    return True
                if code == '514':
                    self.note('139签到', '滑块未过（offset=%d 相似度 %.3f），重取题…'
                              % (off, sim))
                    self.rest(1.5, 2.5)
                    continue
                self.note('139签到', '兑换回 %s %s（offset=%d）'
                          % (code, str(jx.get('msg'))[:34], off))
                return False
            except Exception as exc:  # noqa: BLE001
                self.note('139签到', '兑换异常：%s' % str(exc)[:60])
                return False
        self.note('139签到', '滑块重试耗尽（下次 cron 再来）')
        return False

    def task_mcloud(self):
        """和彩云（移动云盘）活动面：任务列表 + 福袋配置（**免登录**）+ 抽奖次数（需 139 登录态）。`taskLottery/queryCount` 回 `code=90001 未登录`（抽奖需和彩云登录态，属外部前提）。"""
        hd = {'User-Agent': UA, 'Accept': 'application/json, text/plain, */*',
              'Origin': 'https://m.mcloud.139.com', 'Referer': self.MCLOUD_ACT}
        # ① 任务列表（免登录）
        try:
            r = self.http.request('GET', self.MCLOUD_GP + '/ycloud/bill/toDoTask/taskInfos',
                                  headers=hd)
            j = r.get('json') or {}
            tasks = j.get('result') if isinstance(j.get('result'), list) else []
            self.note('和彩云', '任务 %d 个（code=%s）' % (len(tasks), j.get('code')))
            for t in tasks[:8]:
                self.note('  任务', '%s [%s] %s/%s' % (t.get('taskName'), t.get('taskCode'),
                                                       t.get('completedCount'),
                                                       t.get('totalCount')))
        except Exception as exc:  # noqa: BLE001
            self.note('和彩云', '任务列表异常：%s' % str(exc)[:70])
        self.rest(0.8, 1.6)
        # ② 福袋配置（免登录）
        try:
            r = self.http.request('GET', self.MCLOUD_GP + '/ycloud/luckybag/bag/config/list',
                                  headers=hd)
            j = r.get('json') or {}
            bags = j.get('result') if isinstance(j.get('result'), list) else []
            self.note('和彩云', '福袋 %d 期（code=%s）%s' % (
                len(bags), j.get('code'),
                '；'.join(str(b.get('title')) for b in bags[:3])))
        except Exception as exc:  # noqa: BLE001
            self.note('和彩云', '福袋配置异常：%s' % str(exc)[:70])
        self.rest(0.8, 1.6)
        try:
            jwt = self._mcloud_login()
            if jwt:
                r = self.http.request('GET', self.MCLOUD_GP + '/ycloud/bill/taskLottery/queryCount',
                                      headers=self._mcloud_hdr(jwt))
                j = r.get('json') or {}
                self.note('和彩云', '抽奖次数（已免登）：%s' % j.get('result'))
            else:
                self.note('和彩云', '抽奖次数：免登失败（ext 签发或 tyrzLogin 未过）')
        except Exception as exc:  # noqa: BLE001
            self.note('和彩云', '抽奖次数查询异常：%s' % str(exc)[:70])

    def task_mcloud_acts(self):
        """mcloud 活动全家桶：25 周年 / 福袋天天开 / 抽奖码 / 许愿 / AI 通讯 / 美图备份 / 酷狗 / 推送签到。"""
        jwt = self._mcloud_login()
        if not jwt:
            self.note('mcloud活动', '免登失败（ext 签发或 tyrzLogin 未过）——全部跳过')
            return
        self.note('mcloud活动', '免登成功（jwt %d 字符），开始 8 个活动面' % len(jwt))
        GP = self.MCLOUD_GP
        HC = 'https://m.mcloud.139.com/portal/hcyview/index.html?path='

        def H(path_key):
            return self._mcloud_hdr(jwt, referer=HC + path_key)

        # ===== ① 25 周年（National_25AnnualReview）=====
        try:
            h = H('National_25AnnualReview')
            r = self.http.request('GET', GP + '/ycloud/bill/toDoTask/taskInfos', headers=h)
            tasks = (r.get('json') or {}).get('result') or []
            todo = [t for t in tasks if str(t.get('status')) != '2']
            self.note('25周年', '任务 %d 个（待完成 %d）' % (len(tasks), len(todo)))
            for t in tasks:
                mark = '✅' if str(t.get('status')) == '2' else '…'
                self.note('  任务', '%s %s（%s/%s）' % (mark, t.get('taskName'),
                                                       t.get('completedCount'), t.get('totalCount')))
            # 逐个执行可自动完成的任务（FEEDBACK 带问卷、AI_PHOTO 带 userDomainId）
            udi = self._mcloud_udi() or ''
            friend_code = (getattr(self.cfg, 'cmcc_friend_invite', '') or '').strip()
            for t in todo:
                tc = t.get('taskCode') or ''
                if tc == 'SHARE_FRIENDS':  # param='<邀请者的邀请码>|<sourceid>'，需另一账号的码
                    if not friend_code:
                        continue
                self.rest(1.0, 2.0)
                body = {'taskCode': tc}
                if tc == 'AI_PHOTO':
                    body['param'] = udi
                elif tc == 'SHARE_FRIENDS':
                    body['param'] = '%s|1001' % friend_code
                elif tc == 'FEEDBACK':
                    body['param'] = {'reasonChoice': 'A', 'reasonDesc': '',
                                     'suggestChoice': 'B', 'suggestDesc': ''}
                if self.dry_run:
                    self.note('25周年', '【演练】跳过 executeTask %s' % tc)
                    continue
                r2 = self.http.request('POST', GP + '/ycloud/bill/toDoTask/executeTask',
                                       headers=H('National_25AnnualReview'), body=body)
                j2 = r2.get('json') or {}
                self.note('25周年', 'executeTask %s → %s %s' % (tc, j2.get('code'),
                                                                str(j2.get('msg'))[:40]))
            # 抽奖：queryCount → prize（channel=other）
            self.rest(1.0, 1.8)
            r = self.http.request('GET', GP + '/ycloud/bill/taskLottery/queryCount', headers=h)
            cnt = (r.get('json') or {}).get('result') or 0
            self.note('25周年', '抽奖次数 %s' % cnt)
            if not self.dry_run:
                for i in range(min(int(cnt or 0), 5)):
                    self.rest(1.4, 2.2)
                    r3 = self.http.request('GET', GP + '/ycloud/bill/taskLottery/prize',
                                           headers=h, params={'channel': 'other'})
                    j3 = r3.get('json') or {}
                    msg = str(j3.get('msg'))[:30]
                    self.note('25周年', '第 %d 抽 → %s %s' % (i + 1, j3.get('code'), msg))
                    if str(j3.get('code')) not in ('0', '30004'):
                        break
                r4 = self.http.request('GET', GP + '/ycloud/bill/taskLottery/getMyPrizeList', headers=h)
                prizes = (r4.get('json') or {}).get('result') or []
                self.note('25周年', '奖品记录 %d 条' % len(prizes))
        except Exception as exc:  # noqa: BLE001
            self.note('25周年', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ② 福袋天天开（National_OpenLuckybag）=====
        try:
            h = H('National_OpenLuckybag')
            r = self.http.request('GET', GP + '/ycloud/luckybag/common/user/info', headers=h)
            info = (r.get('json') or {}).get('result') or {}
            cnt = info.get('totalLotteryCount') or 0
            self.note('福袋', '开袋次数 %s（任务可得 %s/%s）' % (
                cnt, info.get('taskGetLotteryCount'), info.get('taskMaxDayLotteryCount')))
            if not self.dry_run and int(cnt or 0) > 0:
                self.rest(1.0, 1.8)
                r2 = self.http.request('POST', GP + '/ycloud/luckybag/blindbox/lottery',
                                       headers=h, body={'source': 'other'})
                j2 = r2.get('json') or {}
                res2 = j2.get('result') or {}
                self.note('福袋', '开袋 → %s %s' % (j2.get('code'),
                          json.dumps(res2.get('prize') or {}, ensure_ascii=False)[:80]))
            r3 = self.http.request('GET', GP + '/ycloud/luckybag/bag/config/list', headers=h)
            bags = (r3.get('json') or {}).get('result') or []
            self.note('福袋', '在售期数 %d' % len(bags))
        except Exception as exc:  # noqa: BLE001
            self.note('福袋', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ③ iPhone 抽奖码（lotteryIphone / market/rafflecode）=====
        try:
            h = H('lotteryIphone')
            r = self.http.request('GET', GP + '/market/task/task/taskList', headers=h,
                                  params={'marketname': 'rafflecode'})
            res = (r.get('json') or {}).get('result') or {}
            groups = [g for g in ('month', 'month_other') if isinstance(res.get(g), list)]
            total = sum(len(res.get(g) or []) for g in groups)
            self.note('抽奖码', '任务 %d 个（组：%s）' % (total, '+'.join(groups)))
            if not self.dry_run:
                for g in groups:
                    for t in res.get(g) or []:
                        self.rest(1.0, 1.8)
                        r2 = self.http.request('GET', GP + '/market/task/task/click', headers=h,
                                               params={'key': 'rafflecodeTask', 'id': t.get('id')})
                        j2 = r2.get('json') or {}
                        self.note('  抽奖码任务', '%s → %s %s' % (t.get('name'), j2.get('code'),
                                                                  str(j2.get('msg'))[:36]))
                # 兑换抽奖码（服务端校验前置任务；validate 返回 1=未完成 0=可兑 2=已达上限）
                self.rest(1.0, 1.8)
                r3 = self.http.request('POST',
                                       GP + '/market/task/task/rafflecode/exchange/token', headers=h)
                j3 = r3.get('json') or {}
                if str(j3.get('code')) == '0' and j3.get('result'):
                    self.rest(1.0, 1.8)
                    r4 = self.http.request('POST',
                                           GP + '/market/task/task/rafflecode/exchange/redeem',
                                           headers=h, body={'exchangeToken': j3.get('result')})
                    j4 = r4.get('json') or {}
                    self.note('抽奖码', '⭐ 兑换 → %s %s' % (j4.get('code'), str(j4.get('msg'))[:40]))
                else:
                    self.note('抽奖码', '兑换未成：%s（前置任务未全部完成/服务端拒绝）'
                              % str(j3.get('msg'))[:50])
                    self.note('  抽奖码前置', '剩余任务为行为对账型（通知/备份/上传/互动/分享），'
                              '服务端校验真实行为，协议不可代做')
                r5 = self.http.request('POST', GP + '/market/rafflecode/list', headers=h,
                                       body={'month': time.strftime('%Y%m')})
                codes = (r5.get('json') or {}).get('result') or []
                self.note('抽奖码', '本月抽奖码 %d 张' % len(codes))
        except Exception as exc:  # noqa: BLE001
            self.note('抽奖码', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ④ 许愿（National_MakeWish）=====
        try:
            h = H('National_MakeWish')
            r = self.http.request('GET', GP + '/ycloud/makewish/page/home', headers=h,
                                  params={'platform': 'android', 'appVersion': '', 'channel': 'out'})
            home = (r.get('json') or {}).get('result') or {}
            wish = home.get('wish')
            codes = (home.get('raffleCodes') or {}).get('totalCount')
            self.note('许愿', '当前心愿 %s | 抽奖码 %s' % (
                (wish or {}).get('prizeName') if isinstance(wish, dict) else '未许愿', codes))
            if not self.dry_run and not wish:
                r2 = self.http.request('GET', GP + '/ycloud/makewish/prize/list', headers=h,
                                       params={'platform': 'android', 'appVersion': '',
                                               'channel': 'out'})
                pl = (r2.get('json') or {}).get('result') or []
                if isinstance(pl, list) and pl:
                    idx = int(getattr(self.cfg, 'cmcc_wish_index', '0') or 0) % len(pl)
                    target = pl[idx]
                    self.rest(1.0, 1.8)
                    r3 = self.http.request('POST', GP + '/ycloud/makewish/wish', headers=h,
                                           body={'makewishPrizeId': target.get('makewishPrizeId')
                                                 or target.get('id')})
                    j3 = r3.get('json') or {}
                    self.note('许愿', '许愿「%s」→ %s %s' % (
                        target.get('prizeName') or target.get('name'),
                        j3.get('code'), str(j3.get('msg'))[:40]))
        except Exception as exc:  # noqa: BLE001
            self.note('许愿', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ⑤ AI 通讯（National_AItongxun）=====
        try:
            h = H('National_AItongxun')
            r = self.http.request('GET', GP + '/ycloud/aitongxun/invite/user/info', headers=h)
            u = (r.get('json') or {}).get('result') or {}
            self.note('AI通讯', '配额 %s | 奖品 %s | 邀请 %s（新 %s/旧 %s）' % (
                u.get('qoutaCnt'), u.get('prizeCnt'), u.get('inviteCnt'),
                u.get('inviteNewCnt'), u.get('inviteOldCnt')))
        except Exception as exc:  # noqa: BLE001
            self.note('AI通讯', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ⑥ 美图备份（National_Meitubackup）=====
        try:
            h = H('National_Meitubackup')
            r = self.http.request('GET', GP + '/ycloud/meitu/new/prizeCount', headers=h)
            pc = (r.get('json') or {}).get('result') or {}
            self.note('美图备份', '授权 %s 次（今日 %s）| 备份 %s 次（今日 %s）' % (
                pc.get('authTotalCount'), pc.get('authDailyCount'),
                pc.get('backupTotalCount'), pc.get('backupDailyCount')))
        except Exception as exc:  # noqa: BLE001
            self.note('美图备份', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ⑦ 酷狗音乐卡（National_KugouMusicCard）=====
        try:
            h = H('National_KugouMusicCard')
            r = self.http.request('GET', GP + '/ycloud/simple/yunpanli/prize/status', headers=h)
            j = r.get('json') or {}
            self.note('酷狗音乐卡', 'prize/status → %s %s' % (j.get('code'), str(j.get('msg'))[:40]))
        except Exception as exc:  # noqa: BLE001
            self.note('酷狗音乐卡', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ⑧ 消息推送签到（openNoticeGift）=====
        try:
            h = H('openNoticeGift')
            r = self.http.request('GET', GP + '/ycloud/msgPushOn/task/status', headers=h)
            st = (r.get('json') or {}).get('result') or {}
            self.note('推送签到', '推送 %s | 首任务 %s | 次任务 %s | 已连续 %s/%s 天' % (
                st.get('pushOn'), st.get('firstTaskStatus'), st.get('secondTaskStatus'),
                st.get('onDuaration'), st.get('total')))
            # 领豆（任务达成后 obtain 才成功；obtainCode=0 表示未达成，如实报）
            if not self.dry_run:
                for tp in (1, 2):
                    self.rest(1.0, 1.8)
                    r2 = self.http.request('POST', GP + '/ycloud/msgPushOn/task/obtain',
                                            headers=h, body={'type': tp})
                    o = (r2.get('json') or {}).get('result') or {}
                    if o.get('obtainCode'):
                        self.note('推送签到', '⭐ 领取 type=%s：%s' % (tp, o.get('description')))
        except Exception as exc:  # noqa: BLE001
            self.note('推送签到', '异常：%s' % str(exc)[:90])

    def task_springgift(self):
        """139 邮箱年度权益（springgift）：活动页 + 任务 + 抽奖。"""
        jwt = self._mcloud_login()
        if not jwt:
            self.note('年度权益', '免登失败')
            return
        GP = self.MCLOUD_GP
        HR = 'https://m.mcloud.139.com/huiyuanri/index.html?path=annualRewarding'

        def H():
            return self._mcloud_hdr(jwt, referer=HR)

        try:
            r = self.http.request('GET', GP + '/ycloud/simple/springgift/getTaskList', headers=H())
            tasks = (r.get('json') or {}).get('result') or []
            self.note('年度权益', '任务 %d 个' % len(tasks))
            for t in tasks:
                self.note('  任务', '%s %s（complete=%s）' % (t.get('id'), t.get('name'),
                                                              t.get('complete')))
                if self.dry_run or t.get('complete'):
                    continue
                if t.get('needRegister'):
                    self.rest(1.0, 1.8)
                    r2 = self.http.request('POST',
                                           GP + '/ycloud/simple/springgift/registerTask',
                                           headers=H(), form={'mark': t.get('id')})
                    j2 = r2.get('json') or {}
                    self.note('  注册', '%s → %s（501=需先访问 url，正常）' % (t.get('id'),
                                                                            j2.get('code')))
                if t.get('directComplete'):
                    self.rest(1.0, 1.8)
                    r3 = self.http.request('POST',
                                           GP + '/ycloud/simple/springgift/completeTask',
                                           headers=H(), body={'id': t.get('id')})
                    j3 = r3.get('json') or {}
                    self.note('  完成', '%s → %s %s' % (t.get('id'), j3.get('code'),
                                                        str(j3.get('msg'))[:30]))
            self.rest(1.0, 1.8)
            r = self.http.request('GET', GP + '/ycloud/simple/springgift/getLotteryCount',
                                   headers=H())
            cnt = (r.get('json') or {}).get('result') or 0
            self.note('年度权益', '抽奖次数 %s' % cnt)
            if not self.dry_run:
                for i in range(min(int(cnt or 0), 5)):
                    self.rest(1.4, 2.2)
                    r4 = self.http.request('POST',
                                           GP + '/ycloud/simple/springgift/lotteryPrize',
                                           headers=H(), body={'isClient': False})
                    j4 = r4.get('json') or {}
                    res = j4.get('result') or {}
                    if str(j4.get('code')) == '0':
                        self.note('年度权益', '⭐ 第 %d 抽中奖：%s' % (i + 1,
                                                                    res.get('prizeName')))
                    else:
                        self.note('年度权益', '第 %d 抽 → %s %s' % (i + 1, j4.get('code'),
                                                                   str(j4.get('msg'))[:40]))
                        break
        except Exception as exc:  # noqa: BLE001
            self.note('年度权益', '异常：%s' % str(exc)[:90])

    def task_huiyuanri(self):
        """139 邮箱会员日（cloudCircle）：innerCloud 建档 / playAI 报名 / 邮件短信 / 云盘日盲盒。"""
        jwt = self._mcloud_login()
        if not jwt:
            self.note('会员日', '免登失败')
            return
        GP = self.MCLOUD_GP
        HC = 'https://m.mcloud.139.com/huiyuanri/index.html?path='

        def H(key):
            return self._mcloud_hdr(jwt, referer=HC + key)

        try:
            h = H('National_playAI')

            def do_task(mid_, t):
                if t.get('isComplete'):
                    return
                sign = str(t.get('taskSign'))
                if sign == '1':  # 邀请类：非纯协议，跳过
                    self.note('playAI', '跳过邀请类 %s' % t.get('id'))
                    return
                path = ('/ycloud/funai/api/timed/transcriptioncompleted' if sign == '2'
                        else '/ycloud/funai/api/page/register')
                verb = '⭐ 完成' if sign == '2' else '报名'
                self.rest(0.8, 1.6)
                rr = self.http.request('POST', GP + path, headers=h,
                                       body={'moduleId': mid_, 'taskId': t.get('id')})
                jj = rr.get('json') or {}
                if str(jj.get('code')) == '0':
                    self.note('playAI', '%s %s' % (verb, t.get('id')))
                else:
                    self.note('playAI', '%s %s → %s %s' % (verb, t.get('id'),
                                                           jj.get('code'), str(jj.get('msg'))[:24]))

            r = self.http.request('GET', GP + '/ycloud/funai/api/page/getmodule', headers=h)
            mod = (r.get('json') or {}).get('result') or {}
            mid, tasks = mod.get('moduleId'), mod.get('taskIds') or []
            self.note('playAI', '主模块 %s 任务 %d 个（完成 %d）' % (
                mid, len(tasks), sum(1 for t in tasks if t.get('isComplete'))))
            if not self.dry_run:
                for t in tasks:
                    do_task(mid, t)
            self.rest(1.0, 1.8)
            r = self.http.request('GET', GP + '/ycloud/funai/api/timed/getmodule', headers=h)
            mods = (r.get('json') or {}).get('result') or []
            if isinstance(mods, list):
                for mod2 in mods:
                    mid2, tasks2 = mod2.get('moduleId'), mod2.get('taskIds') or []
                    self.note('playAI', '限时模块 %s「%s」任务 %d 个' % (
                        mid2, str(mod2.get('title'))[:20], len(tasks2)))
                    if self.dry_run:
                        continue
                    for t in tasks2:
                        do_task(mid2, t)
            if not self.dry_run:
                self.rest(1.0, 1.8)
                r4 = self.http.request('POST', GP + '/ycloud/funai/api/page/lottery',
                                       headers=h, body={'moduleId': mid})
                j4 = r4.get('json') or {}
                if str(j4.get('code')) == '0':
                    self.note('playAI', '⭐ 主抽奖：%s' % json.dumps(j4.get('result'),
                                                                    ensure_ascii=False)[:120])
                else:
                    self.note('playAI', '主抽奖：%s' % str(j4.get('msg'))[:36])
                if isinstance(mods, list):
                    for mod2 in mods:
                        self.rest(0.8, 1.4)
                        r5 = self.http.request('POST', GP + '/ycloud/funai/api/timed/lottery',
                                               headers=h, body={'moduleId': mod2.get('moduleId')})
                        j5 = r5.get('json') or {}
                        if str(j5.get('code')) == '0':
                            self.note('playAI', '⭐ 限时抽奖 %s：%s' % (
                                mod2.get('moduleId'),
                                json.dumps(j5.get('result'), ensure_ascii=False)[:100]))
                self.rest(0.8, 1.4)
                r6 = self.http.request('POST', GP + '/ycloud/funai/api/page/zhangtingLottery',
                                       headers=h, body={'isZhangting': True,
                                                        'marketName': 'National_playAI'})
                j6 = r6.get('json') or {}
                zt = j6.get('result') or {}
                if str(j6.get('code')) == '0' and zt.get('isWin'):
                    self.note('playAI', '⭐ 掌厅抽奖中奖：%s' % str(zt.get('prizeName')))
                else:
                    self.note('playAI', '掌厅抽奖：%s' % ('今日已抽' if str(j6.get('code')) == '0'
                                                         else str(j6.get('msg'))[:30]))
                self.rest(0.6, 1.2)
                r7 = self.http.request('GET', GP + '/ycloud/funai/api/invite/getcode', headers=h)
                code = (r7.get('json') or {}).get('result')
                if code:
                    self.note('playAI', '邀请码 %s' % code)
        except Exception as exc:  # noqa: BLE001
            self.note('playAI', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ② innerCloud：注册建档（领奖需短信码，属外部前提）=====
        try:
            h = H('innerCloud')
            r = self.http.request('GET', GP + '/ycloud/cmi/vip/register', headers=h)
            j = r.get('json') or {}
            self.note('体验官', 'register → %s（领取需短信验证码，见 README）' % j.get('code'))
        except Exception as exc:  # noqa: BLE001
            self.note('体验官', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ③ openemailSMS：任务状态 =====
        try:
            h = H('openemailSMS')
            r = self.http.request('GET', GP + '/ycloud/openemailsms-service/openEmailsms/getTaskInfo',
                                  headers=h)
            res = (r.get('json') or {}).get('result') or {}
            t1, t2 = res.get('taskOneInfo') or {}, res.get('taskTwoInfo') or {}
            self.note('邮件短信', '任务一 %s（%s 云朵）/ 任务二 %s（%s 云朵，进度 %s）' % (
                t1.get('taskStatus'), t1.get('sum'), t2.get('taskStatus'),
                t2.get('sum'), t2.get('progress')))
        except Exception as exc:  # noqa: BLE001
            self.note('邮件短信', '异常：%s' % str(exc)[:90])
        self.rest(1.0, 2.0)

        # ===== ④ mCloudDay 云盘日：盲盒信息 + 奖品清单 =====
        try:
            h = H('mCloudDay')
            r = self.http.request('GET', GP + '/ycloud/mcloudday/blindbox/lotteryInfo', headers=h)
            info = (r.get('json') or {}).get('result') or {}
            self.note('云盘日', '盲盒次数 %s（邀请 %s；关联会员加 %s 次）' % (
                info.get('lotteryCount'), info.get('totalInviteCount'),
                info.get('associateLotteryCount')))
            r2 = self.http.request('GET', GP + '/ycloud/mcloudday/gift/list', headers=h)
            gifts = (r2.get('json') or {}).get('result') or {}
            np = gifts.get('nationalPrizeList') or []
            self.note('云盘日', '全国奖品 %d 种%s' % (len(np),
                      ('：' + '；'.join(str(g.get('prizeName')) for g in np[:3])) if np else ''))
            # 盲盒窗口自动开（lotteryCount>0 时；窗口未开服务端回 20001，如实报）
            if not self.dry_run and int(info.get('lotteryCount') or 0) > 0:
                self.rest(1.0, 1.8)
                r4 = self.http.request('POST', GP + '/ycloud/mcloudday/blindbox/lottery',
                                       headers=h, body={'client': 2})
                j4 = r4.get('json') or {}
                if str(j4.get('code')) == '0':
                    self.note('云盘日', '⭐ 开盲盒：%s' % json.dumps(
                        (j4.get('result') or {}).get('prize') or j4.get('result'),
                        ensure_ascii=False)[:100])
                else:
                    self.note('云盘日', '开盲盒 → %s %s' % (j4.get('code'),
                                                             str(j4.get('msg'))[:40]))
        except Exception as exc:  # noqa: BLE001
            self.note('云盘日', '异常：%s' % str(exc)[:90])

    def task_agegrowth(self):
        """网龄成长计划（ageFeedbackV2）：等级查询 + 评价任务。"""
        ext = self._fresh_ext('056002')
        if not ext:
            self.note('网龄成长', '拿不到 ext，跳过')
            return
        if _aes() is None:
            self.note('网龄成长', '缺 pycryptodome，跳过')
            return
        body, extra = coc_encrypt({'verifyType': 18, 'ext': ext})
        h = {'Accept': 'application/json, text/plain, */*',
             'Content-Type': 'application/json;charset=utf-8',
             'Origin': 'https://dev.coc.10086.cn',
             'Referer': 'https://dev.coc.10086.cn/coc/web6/ageFeedbackV2/'}
        h.update(extra)
        r = self.http.request('POST', 'https://dev.coc.10086.cn/coc/user/loginCheck',
                              headers=h, body=body)
        tok = (r.get('set_cookies') or {}).get('User-Token')
        if not tok:
            self.note('网龄成长', 'User-Token 获取失败')
            return
        base = 'https://dev.coc.10086.cn'
        h2 = {'Accept': 'application/json, text/plain, */*',
              'Content-Type': 'application/json;charset=utf-8', 'Origin': base,
              'Referer': base + '/coc/web6/ageFeedbackV2/', 'X-Requested-With': APP_PKG,
              'Cookie': 'User-Token=%s' % tok}
        # ① 等级查询（touchCode=页面渠道）
        r1 = self.http.request('POST', base + '/coc/activities/growth/query', headers=h2,
                               body={'touchCode': 'P00000106669',
                                     'provinceCode': self.cfg.cmcc_province_code})
        j1 = r1.get('json') or {}
        self.note('网龄成长', '等级 %s' % (j1.get('data') or {}).get('level'))
        self.rest(1.0, 1.8)
        # ② 评价任务（可做则提交）
        r2 = self.http.request('POST', base + '/coc/activities/growth/query/evaluate',
                               headers=h2, body={})
        can = (r2.get('json') or {}).get('data')
        if can is True:
            if self.dry_run:
                self.note('网龄成长', '【演练】可做评价，跳过提交')
            else:
                self.rest(1.0, 1.8)
                r3 = self.http.request('POST', base + '/coc/activities/growth/submit/evaluate',
                                       headers=h2,
                                       body={'score': 5, 'label': '1,2', 'content': '很满意'})
                j3 = r3.get('json') or {}
                # code=0 提交成功；code=1 且复查已 True ⇒ 幂等（当天已提交过），按成功归一
                self.rest(0.8, 1.4)
                r4 = self.http.request('POST', base + '/coc/activities/growth/query/evaluate',
                                       headers=h2, body={})
                done = (r4.get('json') or {}).get('data') is True
                self.note('网龄成长', '%s评价%s' % (
                    '⭐ ' if done else '⚠ ', '完成（幂等）' if done and str(j3.get('code')) != '0'
                    else '提交 → %s' % j3.get('code')))
        else:
            self.note('网龄成长', '评价任务状态：%s（False=已完成或不可做）' % can)

    def task_wallet(self):
        """App 原生「我的奖励」只读：卡券余量（50528）+ 心愿金 / AI 豆（50576）。"""
        if not self.cfg.cmcc_app_token:
            self.note('我的奖励', '跳过：未配置 CMCC_APP_TOKEN（原生面需要 App 票据 Cookie）')
            return
        r1 = self.native.call('50528', {'cellNum': self.cfg.cmcc_phone})
        if r1['retCode'] == '000000' and isinstance(r1['data'], list):
            cards = r1['data']
            self.note('我的奖励', '卡券 **%d 张**' % len(cards))
            for c in cards[:8]:
                self.note('  券', '%s %s%s（%s）%s' % (
                    c.get('pcardName') or '?', c.get('pcardCash') or '', c.get('pcardUnit') or '',
                    c.get('pcardActName') or '', c.get('pcardInfo') or ''))
        else:
            self.note('我的奖励', '卡券查询失败：%s %s' % (r1['retCode'], r1['retDesc']))
        self.rest(0.8, 1.5)
        r2 = self.native.call('50576', {'cellNum': self.cfg.cmcc_phone,
                                        'provinceCode': self.cfg.cmcc_province_code,
                                        'cityCode': self.cfg.cmcc_city_code})
        d2 = r2['data'] if isinstance(r2['data'], dict) else {}
        if r2['retCode'] == '000000':
            self.note('我的奖励', '心愿金/AI豆：**%s**（%s / %s）' % (
                d2.get('current'), d2.get('desc') or '', d2.get('awardType') or ''))
        else:
            self.note('我的奖励', '心愿金查询失败：%s %s' % (r2['retCode'], r2['retDesc']))

    def task_lxrp(self):
        """lx 口令红包：下一场开抢时间 / 口令 / 历史中奖（只查不抢）。"""
        entry = BASE + '/qwhdhub/diy-client/1126037461?A_C_CODE=hL58BH6l2U'
        self.sso.session('lxrp', entry)
        w = self.sso.api('lxrp', 'POST',
                         '/qwhdhub/api/diyLxRedPacket/diyLottery/queryLxWord', {'componentId': ''})
        code = str(w.get('code') or '')
        data = w.get('data')
        if code == 'HAVE_NEW' and isinstance(data, str):
            self.note('lx红包', '下一场开抢：%s（尚未开抢，已抢0%%）' % data)
        elif code == 'NO_STOCK':
            self.note('lx红包', '无库存/已抢完（%s）' % (w.get('msg') or ''))
        elif code == 'SUCCESS':
            word = data.get('lxWord') if isinstance(data, dict) else data
            self.note('lx红包', '⭐ 口令：%s' % word)
        else:
            self.note('lx红包', 'lxWord code=%s msg=%s' % (code, w.get('msg')))
        self.rest(0.8, 1.6)
        h = self.sso.api('lxrp', 'POST',
                         '/qwhdhub/api/diyLxRedPacket/diyLottery/queryHisWinRecord', {})
        recs = h.get('data') if isinstance(h.get('data'), list) else []
        self.note('lx红包', '历史中奖 %d 条%s' % (
            len(recs), ('：' + '；'.join(str(r)[:36] for r in recs[:3])) if recs else ''))

    def task_qwhd_complete(self):
        """qwhdhub 任务面补全：对 status=2 的任务调 getTaskAward 领奖，条件未达如实报。"""
        try:
            self.sso.session('mark31', BASE + '/qwhdhub/qwhdmark/1021122301')
            r = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/task/taskList', {})
            d = r.get('data') or {}
            tasks = d.get('tasks') or []
            self.note('qwhd任务', '清单 %d 条，AI豆 currentFee=%s'
                      % (len(tasks), d.get('currentFee')))
            done = [t for t in tasks if str(t.get('status')) == '2']
            prog = [t for t in tasks if str(t.get('status')) == '1']
            self.note('qwhd任务', '已完成 %d 条 / 进行中 %d 条' % (len(done), len(prog)))
            got = miss = 0
            if not self.dry_run:
                for t in done[:12]:  # 单次最多试 12 条
                    self.rest(1.6, 2.8)  # 写操作长间隔
                    rr = self.sso.api('mark31', 'POST',
                                      '/qwhdhub/api/mark/task/getTaskAward',
                                      {'taskId': str(t.get('taskId'))})
                    if is_success(rr):
                        got += 1
                        self.note('qwhd任务', '⭐ 领到：%s（+%s AI豆）'
                                  % (str(t.get('taskName'))[:20], t.get('awardNum')))
                    else:
                        miss += 1
                self.note('qwhd任务', '领奖结果：成功 %d / 未达条件 %d' % (got, miss))
            # 连签奖励
            self.rest(1.2, 2.0)
            r2 = self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/mark31/myTaskInfo', {})
            mi = r2.get('data') if isinstance(r2.get('data'), list) else []
            self.note('qwhd连签', '%d 档连签任务' % len(mi))
            for t in mi[:6]:
                self.note('  连签', 'id=%s 缺 %s 天 %s'
                          % (t.get('id'), t.get('lackDay'),
                             str(t.get('prizeAlertText'))[:30]))
                if not self.dry_run and str(t.get('lackDay') or '9') in ('0', 'None', 'null'):
                    self.rest(1.6, 2.8)
                    rr = self.sso.api('mark31', 'POST',
                                      '/qwhdhub/api/mark/mark31/taskAward/%s' % t.get('id'), {})
                    if is_success(rr):
                        self.note('qwhd连签', '⭐ 领到连签奖励 id=%s' % t.get('id'))
        except Exception as exc:  # noqa: BLE001
            self.note('qwhd任务', '异常：%s' % str(exc)[:90])

    def task_qwhd_readall(self):
        """qwhdhub 全量只读扫描：写端点不调用。"""
        try:
            self.sso.session('newcomer', BASE + '/qwhdhub/diy-client/1126072224?A_C_CODE=3C43AjX2Cz')
            r = self.sso.api('newcomer', 'GET', '/qwhdhub/api/diyNewcomerGift/getClaimFlag', None)
            self.note('新人礼', '可领标记：%s' % r.get('data'))
            self.rest(0.8, 1.4)
            r2 = self.sso.api('newcomer', 'POST', '/qwhdhub/api/diyNewcomerGift/queryModulePrize', {})
            pz = r2.get('data') if isinstance(r2.get('data'), list) else []
            self.note('新人礼', '奖品模块 %d 个' % len(pz))
            self.rest(0.8, 1.4)
            r3 = self.sso.api('newcomer', 'POST', '/qwhdhub/customerGroup/filter', [])
            self.note('客户群', 'filter：%s' % str(r3.get('code')))
        except Exception as exc:  # noqa: BLE001
            self.note('新人礼', '异常：%s' % str(exc)[:70])
        self.rest(1.0, 1.6)
        try:
            self.sso.session('guandan', BASE + '/qwhdhub/diy-client/1126090514')
            r4 = self.sso.api('guandan', 'GET',
                              '/qwhdhub/api/diyAiTryFirst/diyGuandanGame/V2/getUserRankInfo/1', None)
            self.note('掼蛋周榜', '排名信息：%s' % (r4.get('data') if r4.get('data') else '无（未上榜）'))
        except Exception as exc:  # noqa: BLE001
            self.note('掼蛋周榜', '异常：%s' % str(exc)[:70])
        self.rest(1.0, 1.6)
        try:
            self.sso.session('track', ACTS.get('track', {}).get('entry')
                             or BASE + '/qwhdhub/diy-client/1126082530')
            r5 = self.sso.api('track', 'GET',
                              '/qwhdhub/api/diyVideoDayRedesign/task/queryDiyVideoTask', None)
            td = r5.get('data')
            n = len(td) if isinstance(td, list) else (len(td.get('tasks') or [])
                                                      if isinstance(td, dict) else 0)
            self.note('追剧任务', '%d 条任务' % n)
        except Exception as exc:  # noqa: BLE001
            self.note('追剧任务', '异常：%s' % str(exc)[:70])

    COC3_REAL_ACTS = (
        (17456, '好礼月月领'), (17524, '移动AI你幸运礼'), (17761, '一豆赢惊喜'),
        (17766, '好物福利仓二期'), (17451, '三日签·第2天'), (17452, '三日签·第3天'),
        (17453, '三日签·话费券'), (17518, 'AI你科技礼秒杀'), (17764, '周周有权益秒杀'),
        (17765, '周周有权益'), (17756, '金科苹果18pro'), (17085, '新人有好礼'),
        (17071, 'hzl盲盒兑换'), (17238, '超级芒盒·黄金'), (17239, '超级芒盒·钻石'),
    )
    #: 抽奖提交端点的**成功码**
    COC3_LOTTERY_OK = ('60000', '60010')

    def _harvest_sm_device_id(self):
        """139 云朵领取/兑换必需的数美设备 ID（receiveV3 的硬闸门），缺失返回空串。"""
        if getattr(self.cfg, 'cmcc_sm_device_id', ''):
            return self.cfg.cmcc_sm_device_id
        try:
            import subprocess as _sp, glob as _g, json as _json
            here = os.path.dirname(os.path.abspath(__file__))
            exp = os.path.abspath(os.path.join(
                here, '..', '02_分析', '过程产物', 'exp_mcloud_deviceid.py'))
            if not os.path.exists(exp):
                return ''
            _sp.run([sys.executable, '-X', 'utf8', exp], capture_output=True, timeout=120)
            outs = sorted(_g.glob(os.path.join(os.path.dirname(exp), 'mcloud_deviceId_*.json')))
            if not outs:
                return ''
            d = _json.load(open(outs[-1], encoding='utf-8'))
            dev = str(d.get('deviceId') or '')
            if len(dev) >= 80:
                self.cfg.cmcc_sm_device_id = dev
                return dev
        except Exception:  # noqa: BLE001
            pass
        return ''
    def _harvest_wasm_params(self):
        """产物即 `CMCC_WASM_PARAMS` 的值（约 1KB，`JSON.stringify([aurora, content])`）。"""
        if getattr(self.cfg, 'cmcc_wasm_params', ''):
            return self.cfg.cmcc_wasm_params  # 配置给了就用配置的
        try:
            import subprocess as _sp
            here = os.path.dirname(os.path.abspath(__file__))
            exp = os.path.abspath(os.path.join(
                here, '..', '02_分析', '过程产物', 'exp_coc3_wasm_harvest.py'))
            if not os.path.exists(exp):
                return ''
            py = sys.executable
            _sp.run([py, '-X', 'utf8', exp], capture_output=True, timeout=120)
            import glob as _g
            outs = sorted(_g.glob(os.path.join(os.path.dirname(exp), 'coc3wasm指纹_*.json')))
            if not outs:
                return ''
            import json as _json
            d = _json.load(open(outs[-1], encoding='utf-8'))
            rs = ((d.get('result') or {}).get('roto_simple'))
            if isinstance(rs, list) and len(rs) == 2:
                wp = _json.dumps([str(rs[0]), str(rs[1])])
                self.cfg.cmcc_wasm_params = wp
                return wp
        except Exception:  # noqa: BLE001
            pass
        return ''
    def task_coc3_complete(self):
        """coc3 全量活动抽奖驱动器：逐个活动查次数并抽奖（纯协议）。"""
        if not self.coc3_ensure():
            self.note('coc3抽奖全量', 'coc3 token 获取失败')
            return
        base = 'https://dev.coc.10086.cn'
        h = dict(self._coc3_headers())
        h['Accept'] = 'application/json'
        rt = (getattr(self.cfg, 'cmcc_risk_token', '') or '').strip()
        wpx = (getattr(self.cfg, 'cmcc_wasm_params', '') or '').strip()
        if not wpx:
            wpx = self._harvest_wasm_params()
            if wpx:
                self.note('coc3抽奖全量', 'wasmParams 已自动收割（%d 字符）' % len(wpx))
        #: 提交端点专用头（预检/查询用的 `h` 不带指纹头）
        h_submit = dict(h)
        if rt:
            h_submit['gr-risk-p3'] = rt
        if wpx:
            h_submit['wasmParams'] = wpx
        ck = 'COC3_LOGIN_TOKEN=%s' % self.coc3_token
        self.note('coc3抽奖全量', '扫 %d 个真实活动（指纹：gr-risk-p3=%s / wasmParams=%s）'
                  % (len(self.COC3_REAL_ACTS), '有' if rt else '无',
                     '有' if wpx else '无'))
        hit = risk = 0
        for aid, name in self.COC3_REAL_ACTS:
            try:
                # ➊ 资格预检：无资格或缺货的活动直接跳过，不白撞风控
                rq = self.http.request('GET', base + '/coc3/coc3-market-activity/arrange/'
                                       'checkQualificByActivityId', headers=h, cookies=ck,
                                       params={'activityId': str(aid)})
                jq = rq.get('json') or {}
                if str(jq.get('resultCode')) != '0':
                    dq = jq.get('data') if isinstance(jq.get('data'), dict) else {}
                    oos = len(dq.get('outOfStockList') or [])
                    noq = len(dq.get('noQualificationList') or [])
                    why = (('缺货 %d / 无资格 %d' % (oos, noq)) if (oos or noq) else
                           ('%s %s' % (jq.get('resultCode'), str(jq.get('msg'))[:24])))
                    self.note('coc3抽奖全量', '%s(%d)：预检未过（%s）—— 跳过'
                              % (name, aid, why))
                    continue
                self.rest(1.0, 1.8)
                r = self.http.request('POST', base + '/coc3/coc3-market-activity/cmpp-manage/'
                                      'lottery/residueLotteryCount', headers=h,
                                      body={'activityId': aid}, cookies=ck)
                d = (r.get('json') or {}).get('data')
                try:
                    cnt = int(d)
                except (TypeError, ValueError):
                    cnt = 0
                if cnt <= 0:
                    continue
                self.note('coc3抽奖全量', '%s(%d)：剩余 %d 次' % (name, aid, cnt))
                if self.cfg.dry_run:
                    self.note('coc3抽奖全量', '  【演练】不提交')
                    continue
                for i in range(min(cnt, 3)):  # 单活动 ≤3 抽（不硬撞）
                    self.rest(2.5, 4.5)  # 写操作长间隔（风控敏感）
                    rr = self.http.request('POST', base + '/coc3/coc3-market-activity/cmpp-manage/'
                                           'lottery/lottery', headers=h_submit, cookies=ck,
                                           body={'activityId': aid, 'channelNo': 'P00000123844',
                                                 'operDfp': (getattr(self.cfg, 'cmcc_bsfit_dfp', '') or '').strip(),
                                                 'referer': 'https://dev.coc.10086.cn/coc3/canvas/'
                                                            'rightsmarket-h5-canvas/online/home'})
                    j = rr.get('json') or {}
                    code = str(j.get('code'))
                    dd = j.get('data') or {}
                    if code in self.COC3_LOTTERY_OK or (isinstance(j.get('code'), int)
                                                        and j.get('code') in (60000, 60010)):
                        hit += 1
                        self.note('coc3抽奖全量', '  ⭐ 第 %d 抽成功：%s —— 本轮收手（IP 窗口≈1 发）' % (
                            i + 1, json.dumps(dd, ensure_ascii=False)[:110]))
                        return
                    elif isinstance(dd, dict) and dd.get('risk'):
                        risk += 1
                        self.note('coc3抽奖全量', '  ⛔ 风控：%s（%s）—— 停本活动'
                                  % (str(dd.get('explain'))[:24], str(j.get('message'))[:20]))
                        break
                    else:
                        self.note('coc3抽奖全量', '  第 %d 抽 → code=%s %s'
                                  % (i + 1, code, str(j.get('message') or j.get('msg'))[:40]))
                        break
            except Exception as exc:  # noqa: BLE001
                self.note('coc3抽奖全量', '%s 异常：%s' % (name, str(exc)[:60]))
        self.note('coc3抽奖全量', '完成：%d 次中奖 / %d 次风控拦截' % (hit, risk))

    def task_coc3_scan(self):
        """coc3 活动全量扫描（只读）：输出在窗口且有 0 元商品的活动清单。"""
        if not self.coc3_ensure():
            self.note('coc3扫描', 'coc3 token 获取失败')
            return
        base = 'https://dev.coc.10086.cn'
        h = dict(self._coc3_headers())
        h['Accept'] = 'application/json'
        ck = 'COC3_LOGIN_TOKEN=%s' % self.coc3_token
        # 活动目录：优先读外部包，缺失则用内置名单
        ids = []
        for cand in (os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                                  'cmcc活动接口包_20261001', 'data', '在线活动清单.json'),
                     os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  'cmcc活动接口包_20261001', 'data', '在线活动清单.json')):
            try:
                with open(os.path.abspath(cand), encoding='utf-8') as fh:
                    ids = [(a['id'], a.get('name')) for a in json.load(fh)]
                break
            except Exception:  # noqa: BLE001
                continue
        if not ids:
            ids = list(self.COC3_REAL_ACTS)
        now = int(time.time())
        live = []
        for i, (aid, name) in enumerate(ids):
            try:
                rr = self.http.request('GET', base + '/coc3/coc3-market-activity/arrange/getProByActId',
                                       headers=h, cookies=ck,
                                       params={'activityId': str(aid), 'batchId': '0',
                                               'showLimit': '3', 'seckillType': '1',
                                               'from': 'page', 't': str(now)})
                j = rr.get('json') or {}
                d = j.get('data') or {}
                goods = []
                for sub in d.get('subActivityList') or []:
                    goods.extend(sub.get('goodsList') or [])
                zeros = [g for g in goods if str(g.get('price')) in ('0', '0.0', '')]
                try:
                    inw = int(d.get('startTime')) <= now <= int(d.get('endTime'))
                except (TypeError, ValueError):
                    inw = None
                if inw and zeros:
                    live.append((aid, d.get('name') or name, len(zeros)))
            except Exception:  # noqa: BLE001
                pass
            if (i + 1) % 20 == 0:
                self.rest(1.0, 2.0)
        self.note('coc3扫描', '%d 个活动中，在窗口且有 0 元商品：**%d** 个' % (len(ids), len(live)))
        for aid, name, nz in live[:20]:
            self.note('  可用', '%d %s（0元商品 %d）' % (aid, str(name)[:20], nz))

    def task_native_complete(self):
        """原生网关全量任务：关爱版 / 权益面 / 世界杯任务 / 热门活动自动参与。"""
        if not self.cfg.cmcc_app_token:
            self.note('原生完整', '跳过：未配置 CMCC_APP_TOKEN')
            return

        # ① 关爱版面（老年版，4条全通）
        for code, name in [('30313', '时钟提醒'), ('30314', '图标配置'),
                           ('30323', '横幅广告'), ('30679', '抽屉弹窗')]:
            try:
                r = self.native.call(code, {'cellNum': self.cfg.cmcc_phone,
                                           'provinceCode': self.cfg.cmcc_province_code,
                                           'cityCode': self.cfg.cmcc_city_code})
                if str(r['retCode']) == '410000':
                    if self.native.auto_login(self.cfg.cmcc_phone).get('ok'):
                        self.rest(0.6, 1.2)
                        r = self.native.call(code, {'cellNum': self.cfg.cmcc_phone,
                                                    'provinceCode': self.cfg.cmcc_province_code,
                                                    'cityCode': self.cfg.cmcc_city_code})
                if r['retCode'] == '000000':
                    d = r['data'] if isinstance(r['data'], dict) else {}
                    self.note('关爱版', '%s：%s' % (name, json.dumps(d, ensure_ascii=False)[:80]))
                else:
                    self.note('关爱版', '%s：%s' % (name, r['retDesc']))
            except Exception as exc:
                self.note('关爱版', '%s异常：%s' % (name, str(exc)[:60]))
            self.rest(0.8, 1.4)

        # ② 权益面（4条）
        for code, name in [('50514', 'GSM权益'), ('20084', '5G卡'),
                           ('30301', '权益推送'), ('30324', '孝心卡')]:
            try:
                r = self.native.call(code, {'cellNum': self.cfg.cmcc_phone,
                                           'provinceCode': self.cfg.cmcc_province_code,
                                           'cityCode': self.cfg.cmcc_city_code})
                if str(r['retCode']) == '410000':
                    if self.native.auto_login(self.cfg.cmcc_phone).get('ok'):
                        self.rest(0.6, 1.2)
                        r = self.native.call(code, {'cellNum': self.cfg.cmcc_phone,
                                                    'provinceCode': self.cfg.cmcc_province_code,
                                                    'cityCode': self.cfg.cmcc_city_code})
                    else:
                        self.note('权益面', '%s：会话超时且免密续期未成功（需重登）' % name)
                if r['retCode'] == '000000':
                    d = r['data'] if isinstance(r['data'], dict) else {}
                    self.note('权益面', '%s：%s' % (name, json.dumps(d, ensure_ascii=False)[:80]))
                else:
                    self.note('权益面', '%s：%s' % (name, r['retDesc']))
            except Exception as exc:
                self.note('权益面', '%s异常：%s' % (name, str(exc)[:60]))
            self.rest(0.8, 1.4)

        try:
            r = self.native.call('50415', {'taskType': '1'})
            d = r['data'] if isinstance(r['data'], dict) else {}
            if r['retCode'] == '000000':
                lists = [(k, v) for k, v in d.items() if isinstance(v, list) and v]
                if lists:
                    k, rows = lists[0]
                    self.note('世界杯任务', '列表键 `%s`：%d 项' % (k, len(rows)))
                    for t in rows[:5]:
                        if isinstance(t, dict):
                            self.note('  任务', json.dumps(t, ensure_ascii=False)[:80])
                else:
                    self.note('世界杯任务', '无列表字段；标量：%s'
                              % json.dumps({k: v for k, v in d.items()
                                            if not isinstance(v, (list, dict))},
                                           ensure_ascii=False)[:100])
            else:
                self.note('世界杯任务', '%s %s' % (r['retCode'], r['retDesc']))
        except Exception as exc:  # noqa: BLE001
            self.note('世界杯任务', '异常：%s' % str(exc)[:70])

        self.rest(1.0, 1.8)
        try:
            r = self.native.call('30645', {'cellNum': self.cfg.cmcc_phone,
                                          'provinceCode': self.cfg.cmcc_province_code,
                                          'cityCode': self.cfg.cmcc_city_code})
            d = r['data'] if isinstance(r['data'], dict) else {}
            if r['retCode'] == '000000':
                lists = [(k, v) for k, v in d.items() if isinstance(v, list) and v]
                if lists:
                    k, rows = lists[0]
                    self.note('热门活动', '列表键 `%s`：%d 个在投活动' % (k, len(rows)))
                    for act in rows[:3]:
                        if isinstance(act, dict):
                            self.note('  活动', json.dumps(act, ensure_ascii=False)[:90])
                else:
                    self.note('热门活动', '无列表字段；键：%s' % ','.join(list(d.keys())[:8]))
            else:
                self.note('热门活动', '%s %s' % (r['retCode'], r['retDesc']))
        except Exception as exc:  # noqa: BLE001
            self.note('热门活动', '异常：%s' % str(exc)[:70])

    def task_hlw_complete(self):
        """hlw 福利营销面收尾：周三充值日各查询端点 + 追剧用户信息（只读）。"""
        try:
            self.sso.session('hlw', ACTS['hlw']['entry'])
            r = self.sso.api('hlw', 'GET', '/hlwyxhdhub/api/wedrecharge/_pub/rec_login',
                             {'sysSource': 'sys_app'})
            self.note('hlw全量', 'rec_login：%s mobile=%s'
                      % (r.get('code'), jget(r, 'data', 'mobile', default='?')))
            reads = [
                ('POST', '/hlwyxhdhub/api/wedrecharge/queryActivityInfo', '活动信息'),
                ('POST', '/hlwyxhdhub/api/wedrecharge/getPrizeList', '奖品清单'),
                ('POST', '/hlwyxhdhub/api/wedrecharge/queryDrawPrizeInfo', '抽奖记录'),
                ('POST', '/hlwyxhdhub/api/wedrecharge/queryFloorInfo', '楼层信息'),
                ('POST', '/hlwyxhdhub/api/wedrecharge/queryPictureActInfo', '图片活动'),
                ('POST', '/hlwyxhdhub/api/wedrecharge/queryShareImgInfo', '分享图'),
                ('POST', '/hlwyxhdhub/api/wedrecharge/queryYnActInfo', 'YN活动'),
                ('POST', '/hlwyxhdhub/api/wedrecharge/queryYwFloorInfo', 'YW楼层'),
                ('POST', '/hlwyxhdhub/api/wedrecharge/queryChInfo', '渠道信息'),
                ('POST', '/hlwyxhdhub/api/sunvideo/userInfo', '追剧用户'),
                ('GET', '/hlwyxhdhub/api/wedrecharge/_pub/getContactInfo', '联系人信息'),
                ('GET', '/hlwyxhdhub/api/wedrecharge/_pub/getFloorRuleInfo', '楼层规则'),
            ]
            for method, path, label in reads:
                self.rest(0.8, 1.5)
                try:
                    rr = self.sso.api('hlw', method, path, {} if method == 'POST' else None)
                    data = rr.get('data')
                    n = len(data) if isinstance(data, (list, dict)) else 0
                    self.note('hlw全量', '%-8s %s（%s，%s 项）'
                              % (label, rr.get('code'), str(rr.get('msg'))[:16], n))
                except Exception as exc:  # noqa: BLE001
                    self.note('hlw全量', '%s 异常：%s' % (label, str(exc)[:50]))
        except Exception as exc:  # noqa: BLE001
            self.note('hlw全量', '异常：%s' % str(exc)[:90])

    def task_mcloud_complete(self):
        """mcloud 活动深度收尾：福袋循环开袋 + 许愿任务清单 + 抽奖码再兑。"""
        jwt = self._mcloud_login()
        if not jwt:
            self.note('mcloud深度', '免登失败')
            return
        GP = self.MCLOUD_GP
        HC = 'https://m.mcloud.139.com/portal/hcyview/index.html?path='

        def H(path_key):
            return self._mcloud_hdr(jwt, referer=HC + path_key)

        try:
            h = H('National_OpenLuckybag')
            opened = 0
            for i in range(8):
                r = self.http.request('GET', GP + '/ycloud/luckybag/common/user/info', headers=h)
                cnt = ((r.get('json') or {}).get('result') or {}).get('totalLotteryCount') or 0
                try:
                    cnt = int(cnt)
                except (TypeError, ValueError):
                    cnt = 0
                if cnt <= 0:
                    break
                if self.dry_run:
                    self.note('福袋循环', '【演练】剩 %d 次未开' % cnt)
                    break
                self.rest(1.2, 2.0)
                r2 = self.http.request('POST', GP + '/ycloud/luckybag/blindbox/lottery',
                                       headers=h, body={'source': 'other'})
                prize = ((r2.get('json') or {}).get('result') or {}).get('prize') or {}
                opened += 1
                self.note('福袋循环', '第 %d 开：%s'
                          % (opened, prize.get('prizeName') or '谢谢参与'))
            self.note('福袋循环', '本轮共开 %d 次（停止条件：次数用尽/演练）' % opened)
        except Exception as exc:  # noqa: BLE001
            self.note('福袋循环', '异常：%s' % str(exc)[:80])
        self.rest(1.0, 1.8)

        try:
            h = H('National_MakeWish')
            r = self.http.request('GET', GP + '/ycloud/makewish/task/list', headers=h,
                                  params={'platform': 'android', 'appVersion': '',
                                          'channel': 'out'})
            j = r.get('json') or {}
            tasks = j.get('result') if isinstance(j.get('result'), list) else []
            self.note('许愿任务', '%d 条（code=%s）%s' % (
                len(tasks), j.get('code'),
                '；'.join('%s:%s' % (str(t.get('name'))[:12], t.get('status'))
                          for t in tasks[:4]) if tasks else ''))
        except Exception as exc:  # noqa: BLE001
            self.note('许愿任务', '异常：%s' % str(exc)[:80])
        self.rest(1.0, 1.8)

        # ③ 抽奖码兑换链**重试**（前置任务补完后 validate 会转 0）
        try:
            h = H('lotteryIphone')
            r = self.http.request('POST', GP + '/market/task/task/rafflecode/exchange/token',
                                  headers=h)
            j = r.get('json') or {}
            if str(j.get('code')) == '0' and j.get('result'):
                self.rest(1.2, 2.0)
                if not self.dry_run:
                    r2 = self.http.request('POST', GP + '/market/task/task/rafflecode/exchange/redeem',
                                           headers=h, body={'exchangeToken': j.get('result')})
                    j2 = r2.get('json') or {}
                    self.note('抽奖码', '⭐ 兑换 → %s %s' % (j2.get('code'),
                                                           str(j2.get('msg'))[:36]))
            else:
                self.note('抽奖码', '兑换未成：%s' % str(j.get('msg'))[:44])
            self.rest(0.9, 1.6)
            r3 = self.http.request('POST', GP + '/market/rafflecode/list', headers=h,
                                   body={'month': time.strftime('%Y%m')})
            codes = (r3.get('json') or {}).get('result') or []
            self.note('抽奖码', '本月持有 %d 张' % len(codes))
        except Exception as exc:  # noqa: BLE001
            self.note('抽奖码', '异常：%s' % str(exc)[:80])

    def task_native_rights(self):
        """**原生面权益只读清点**（`50375` VIP 权益列表 / `50514` GSM 权益计数）。"""
        if not self.cfg.cmcc_app_token:
            self.note('原生权益', '跳过：未配置 CMCC_APP_TOKEN')
            return
        for code, name, extra in (('50375', 'VIP权益列表', {}),
                                  ('50514', 'GSM权益计数', {}),
                                  ('20084', '5G卡', {}),
                                  ('30324', '孝心卡', {}),
                                  ('30301', '权益推送', {})):
            try:
                body = {'cellNum': self.cfg.cmcc_phone,
                        'provinceCode': self.cfg.cmcc_province_code,
                        'cityCode': self.cfg.cmcc_city_code}
                body.update(extra)
                r = self.native.call(code, body)
                if str(r['retCode']) == '410000':
                    # 会话空闲超时 ⇒ 免密续期（60012）后重试一次（自愈）
                    if self.native.auto_login(self.cfg.cmcc_phone).get('ok'):
                        self.rest(0.6, 1.2)
                        r = self.native.call(code, body)
                if r['retCode'] == '000000':
                    data = r['data']
                    if isinstance(data, list):
                        self.note('原生权益', '%-8s %d 项' % (name, len(data)))
                        for it in data[:4]:
                            if isinstance(it, dict):
                                self.note('  权益', '%s %s'
                                          % (str(it.get('rightName') or it.get('name'))[:20],
                                             str(it.get('status') or it.get('canReceive') or '')))
                    else:
                        self.note('原生权益', '%-8s %s' % (
                            name, json.dumps(data, ensure_ascii=False)[:90]))
                else:
                    self.note('原生权益', '%-8s %s %s' % (name, r['retCode'], r['retDesc']))
            except Exception as exc:  # noqa: BLE001
                self.note('原生权益', '%s 异常：%s' % (name, str(exc)[:50]))
            self.rest(0.8, 1.4)

    COC3_SIGN_TASKS = ((2999, '好礼月月领'),)
    COC3_SIGN_CHANNEL = 'P00000067264'

    def task_coc3_daily_checkin(self):
        """coc3 每日签到（accumulateSign taskId=2999）：签到成功直接下发奖励。"""
        if not self.coc3_ensure():
            self.note('coc3签到', 'coc3 token 获取失败')
            return
        base = 'https://dev.coc.10086.cn'
        h = dict(self._coc3_headers())
        h['Accept'] = 'application/json'
        ck = 'COC3_LOGIN_TOKEN=%s' % self.coc3_token
        T = base + '/coc3/coc3-market-activity/cmpp-manage/task/'
        for tid, tname in self.COC3_SIGN_TASKS:
            try:
                jr = (self.http.request('POST', T + 'getTaskSignInfo', headers=h,
                                        body={'taskId': tid}, cookies=ck).get('json')) or {}
                if str(jr.get('resultCode')) not in ('0', '200'):
                    self.note('coc3签到', '%s(%d)：%s（resultCode=%s），跳过'
                              % (tname, tid, str(jr.get('msg'))[:24] or '不可用',
                                 jr.get('resultCode')))
                    continue
                info = jr.get('data') or {}
                signed = bool(info.get('signToday'))
                acc = info.get('accumulateCount')
                self.note('coc3签到', '%s(%d)：今日%s | 累计 %s 天'
                          % (tname, tid, '已签' if signed else '未签',
                             acc if acc is not None else '?'))
                skus = info.get('skuList') if isinstance(info.get('skuList'), list) else []
                for sk in skus[:6]:
                    if isinstance(sk, dict):
                        self.note('  档位', '%s 天：%s（余量 %s）'
                                  % (sk.get('signDays'), sk.get('awardName'),
                                     sk.get('hasRemainCount')))
                if signed:
                    self.note('coc3签到', '今日已签，跳过提交（幂等）')
                    continue
                if self.cfg.dry_run:
                    self.note('coc3签到', '【演练】跳过 accumulateSign 提交')
                    continue
                self.rest(2.0, 3.5)  # 写操作长间隔
                rr = self.http.request('POST', T + 'accumulateSign', headers=h, cookies=ck,
                                       body={'taskId': tid, 'channelCode': self.COC3_SIGN_CHANNEL})
                j = rr.get('json') or {}
                rc = j.get('resultCode')
                data = j.get('data') or []
                if str(rc) in ('0', '200'):
                    names = '；'.join(str(x.get('skuName') or x.get('awardName'))
                                      for x in data[:3] if isinstance(x, dict))
                    self.note('coc3签到', '⭐ **签到成功**，本次奖励：%s' % (names or '（见 myAwardRecord）'))
                elif str(rc) in ('-9014', '-1108') or '已签' in str(j.get('msg')):
                    self.note('coc3签到', '今日已签（%s）—— 幂等，按成功归一' % str(j.get('msg'))[:24])
                else:
                    self.note('coc3签到', '提交回 %s %s' % (rc, str(j.get('msg'))[:40]))
                self.rest(1.0, 1.8)
                r2 = self.http.request('POST', T + 'myAwardRecord', headers=h,
                                       body={'taskId': tid}, cookies=ck)
                rows = ((r2.get('json') or {}).get('data') or {}).get('rows') or []
                if rows:
                    self.note('coc3签到', '中奖记录 %d 条，最近：%s（%s）'
                              % (len(rows), str(rows[0].get('skuName'))[:20],
                                 str(rows[0].get('orderUpdateTime'))[:19]))
            except Exception as exc:  # noqa: BLE001
                self.note('coc3签到', '%s 异常：%s' % (tname, str(exc)[:60]))
            self.rest(1.0, 1.8)

    def task_qwhd_team(self):
        """qwhdhub 组队面：组队进度 + 邀请链接（参与端有设备指纹风控）。"""
        try:
            self.sso.session('track', ACTS.get('track', {}).get('entry')
                             or BASE + '/qwhdhub/diy-client/1126082530')
            comp = '4DwOvs0-f1qlH03siz5_'
            r = self.sso.api('track', 'POST', '/qwhdhub/diyTeam/progress',
                             {'componentId': comp, 'activityId': 1026080780})
            d = r.get('data') or {}
            self.note('qwhd组队', '进度：%s' % json.dumps(d, ensure_ascii=False)[:110])
            self.rest(0.9, 1.6)
            r2 = self.sso.api('track', 'POST', '/qwhdhub/diyTeam/queryMembers',
                              {'componentId': comp, 'activityId': 1026080780})
            mem = r2.get('data')
            n = len(mem) if isinstance(mem, list) else (
                len((mem or {}).get('members') or []) if isinstance(mem, dict) else 0)
            self.note('qwhd组队', '成员 %d 人' % n)
            self.rest(0.9, 1.6)
            r3 = self.sso.api('track', 'POST', '/qwhdhub/diyTeam/invite/qrcode',
                              {'componentId': comp, 'activityId': 1026080780})
            self.note('qwhd组队', '邀请码/二维码：%s'
                      % str(jget(r3, 'data', default=''))[:90])
        except Exception as exc:  # noqa: BLE001
            self.note('qwhd组队', '异常：%s' % str(exc)[:80])

    def task_coc_web6_full(self):
        """coc web6全活动面（7日打卡/网龄等级/灵犀签到）——**纯协议全自动**"""
        ext = self._fresh_ext('056002')
        if not ext or _aes() is None:
            self.note('coc web6', 'ext或pycryptodome缺失')
            return

        # 获取User-Token
        body, extra = coc_encrypt({'verifyType': 18, 'ext': ext})
        h = {'Accept': 'application/json, text/plain, */*',
             'Content-Type': 'application/json;charset=utf-8',
             'Origin': 'https://dev.coc.10086.cn',
             'Referer': 'https://dev.coc.10086.cn/coc/web6/'}
        h.update(extra)
        r = self.http.request('POST', 'https://dev.coc.10086.cn/coc/user/loginCheck',
                             headers=h, body=body)
        tok = (r.get('set_cookies') or {}).get('User-Token')
        if not tok:
            self.note('coc web6', 'User-Token获取失败')
            return

        base = 'https://dev.coc.10086.cn'
        h2 = {'Accept': 'application/json, text/plain, */*',
              'Content-Type': 'application/json;charset=utf-8',
              'Origin': base,
              'Referer': base + '/coc/web6/',
              'Cookie': 'User-Token=%s' % tok}

        # ① 7日打卡连签状态
        try:
            r1 = self.http.request('POST', base + '/coc/activities/prize/consecutiveClockInQuery',
                                  headers=h2, body={'activityId': '2095709942208745472'})
            info = (r1.get('json') or {}).get('data') or {}
            self.note('7日打卡', '连签：%d天 | 今日：%s' % (
                info.get('consecutiveDays') or 0, info.get('todayClockIn')))
        except Exception as exc:
            self.note('7日打卡', '异常：%s' % str(exc)[:70])

        self.rest(1.0, 1.8)
        # ② 网龄等级查询（已在agegrowth做了，这里简化）
        try:
            r2 = self.http.request('POST', base + '/coc/activities/growth/query',
                                  headers=h2, body={'touchCode': 'P00000106669',
                                                   'provinceCode': self.cfg.cmcc_province_code})
            level = ((r2.get('json') or {}).get('data') or {}).get('level')
            self.note('网龄等级', '当前等级：%s' % level)
        except Exception as exc:
            self.note('网龄等级', '异常：%s' % str(exc)[:70])


    # ------------------------------------------------ ⓿① AI豆兑换话费券
    def _gold_pick(self, prizes, balance):
        """按面额从高到低选第一个可兑档：在架（WITHOUT）/库存 > 100/AI豆足够。离线可测（selftest 有对应断言）。"""
        by_name = {}
        for p in prizes:
            if isinstance(p, dict) and p.get('name'):
                by_name[p.get('name')] = p
        for name in GOLD_TIERS:
            p = by_name.get(name)
            if not p or p.get('status') != 'WITHOUT':
                continue
            try:
                if float(p.get('stock') or 0) <= GOLD_MIN_STOCK:
                    continue
                if float(p.get('cost') or 0) > balance:
                    continue
            except (TypeError, ValueError):
                continue
            return p
        return None

    def task_gold(self):
        """签到专属 AI 豆兑话费券：按面额从高到低顶格兑（5→2→1.5→1 元）。"""
        try:
            self.sso.session('mark31', ACTS['mark31']['entry'])
        except Exception as e:  # noqa: BLE001
            self.note('AI豆兑换', '会话失败：%s' % str(e)[:60]); return

        def q(path, body):
            return self.sso.api('mark31', 'POST', '/qwhdhub/api/mark/task/' + path, body)

        def _num(v):
            try:
                return float(v or 0)
            except (TypeError, ValueError):
                return 0.0

        r = q('getExchangeList', {'channel': 'app'})
        d = r.get('data') if isinstance(r.get('data'), dict) else {}
        prizes = d.get('prizes') if isinstance(d.get('prizes'), list) else []
        balance = _num(d.get('currentFee'))
        if not prizes:
            self.note('AI豆兑换', 'getExchangeList %s %s' %
                      (r.get('code'), str(r.get('msg'))[:60]))
            return
        self.note('AI豆兑换', 'AI豆余额 %g（累计 %s / 已兑 %s）' %
                  (balance, d.get('totalFee') or '?', d.get('exchangeFee') or '?'))
        for name in GOLD_TIERS:
            p = next((x for x in prizes
                      if isinstance(x, dict) and x.get('name') == name), None)
            if not p:
                continue
            state = {'WITHOUT': '可兑', 'USED': '已兑过'}.get(
                p.get('status'), p.get('status') or '?')
            if p.get('status') == 'WITHOUT' and _num(p.get('stock')) <= GOLD_MIN_STOCK:
                state = '已抢光'
            self.note('  档位', '%s：%s 豆（%s）' % (name, p.get('cost'), state))
        if str(self.cfg.cmcc_gold_exchange).strip() not in ('1', 'true', 'True', 'auto'):
            self.note('AI豆兑换', 'CMCC_GOLD_EXCHANGE=0 只查不兑（要兑设 1）')
            return
        if self.cfg.dry_run:
            t = self._gold_pick(prizes, balance)
            self.note('AI豆兑换', '[dry] 将兑 %s' % (t.get('name') if t else '（无可兑档）'))
            return
        got = 0
        for _ in range(GOLD_MAX_ROUNDS):
            r = q('getExchangeList', {'channel': 'app'})
            dd = r.get('data') if isinstance(r.get('data'), dict) else {}
            t = self._gold_pick(dd.get('prizes') or [], _num(dd.get('currentFee')))
            if not t:
                break
            s = q('checkStock', {'prizeId': t.get('id')})
            left = jget(s, 'data', 'stock')
            try:
                stock_ok = float(left) > GOLD_MIN_STOCK
            except (TypeError, ValueError):
                stock_ok = False
            if not stock_ok:
                self.note('AI豆兑换', '%s 库存不足（%s），看下一档' % (t.get('name'), left))
                continue
            r = q('exchange', {'prizeId': t.get('id')})
            if is_success(r):
                got += 1
                self.note('AI豆兑换', '⭐ 兑到 %s（去 App「我的奖品」核销）' % t.get('name'))
            else:
                err = r.get('data')
                why = {1: '内部员工不在兑换范围', 2: '今日已抢光', 3: '今日已抢光'}.get(
                    err, '错误码 %s' % err)
                self.note('AI豆兑换', '%s 失败：%s' % (t.get('name'), why))
                if err == 1:
                    break
            self.rest(1.0, 2.0)
        if not got:
            self.note('AI豆兑换', '本次无可兑档位（AI豆不足/已兑过/已抢光）')

    # ------------------------------------------------ ⓿② AI豆苗养成记
    def task_seed(self):
        """AI 豆苗养成记（activityId 17692）：每日签到 + 任务 + 领取阳光值气泡。"""
        if not (self.coc3_ensure() and self.coc3_token):
            self.note('AI豆苗', '无 coc3 token（免扫码与配置均失败），跳过'); return
        base = 'https://dev.coc.10086.cn/coc3/coc3-market-activity'
        ck = 'COC3_LOGIN_TOKEN=%s' % self.coc3_token
        hdr = dict(self._coc3_headers(),
                   Referer='https://dev.coc.10086.cn/coc3/canvas/rightsmarket-h5-canvas/'
                           'online/%s?pageRecorded=true' % SEED_PAGE_KEY)

        def post(path, body):
            r = self.http.request('POST', base + path, headers=hdr, body=body, cookies=ck)
            return r.get('json') if isinstance(r.get('json'), dict) else {}

        def growth():
            g = post('/api/activity/slgCredits/queryGrowthValue',
                     {'activityId': SEED_ACTIVITY_ID})
            return jget(g, 'data', 'growthValue')

        before = growth()
        # ① 每日签到（signToday 判定；服务端对重复签回「已签/重复」文案，幂等）
        info = post('/cmpp-manage/task/getTaskSignInfo',
                    {'taskId': SEED_SIGN_TASK_ID, 'needQyCredits': 1, 'needLimit': 0})
        idata = info.get('data') if isinstance(info.get('data'), dict) else {}
        if info.get('resultCode') != 0:
            self.note('AI豆苗', '签到信息获取失败：%s %s' %
                      (info.get('resultCode'), str(info.get('msg'))[:50]))
        elif str(idata.get('signToday')) in ('1', 'True', 'true'):
            self.note('AI豆苗', '今日已签（累签 %s 次）' % idata.get('accumulateCount', '?'))
        elif self.cfg.dry_run:
            self.note('AI豆苗', '[dry] 将签到（+10 阳光值）')
        else:
            r = post('/cmpp-manage/task/accumulateSign',
                     {'taskId': SEED_SIGN_TASK_ID, 'channelCode': SEED_CHANNEL,
                      'needLimit': 0, 'wtAc': SEED_WTAC,
                      'wtAcId': secrets.token_hex(16)})
            awards = r.get('data') if isinstance(r.get('data'), list) else []
            msg = str(r.get('msg') or '')
            if r.get('resultCode') == 0 and awards:
                names = '、'.join(str(a.get('awardName') or '奖励')
                                  for a in awards if isinstance(a, dict))
                self.note('AI豆苗', '✅ 签到成功：%s' % (names or '+10 阳光值'))
            elif r.get('resultCode') in (500, 502) or '已签' in msg or '重复' in msg:
                self.note('AI豆苗', '今日已签（%s），跳过' % (msg or r.get('resultCode')))
            else:
                self.note('AI豆苗', '签到失败：%s %s' % (r.get('resultCode'), msg[:50]))
            self.rest(0.8, 1.6)
        # ② 任务清单（浏览/分享自动做；订购/邀请跳过）
        #: 任务类型：1 浏览/直播；2 分享；3 订购；4/5 领取/关注；6 邀请
        seed_skip = {3: '需真实办理/订购', 6: '需真实好友助力'}

        def target_key(t):
            m = re.search(r'/online/([^/?#]+)', str(t.get('unfinishRedirectUrl') or ''))
            return m.group(1) if m else ''

        rt = post('/cmpp-manage/task/getGrowthTaskList', {'taskListId': SEED_TASK_LIST_ID})
        tasks = rt.get('data') if isinstance(rt.get('data'), list) else []
        todo = [t for t in tasks if isinstance(t, dict) and t.get('isFinish') == 0]
        done = 0
        for t in todo:
            title = t.get('taskMainTitle') or ('任务%s' % t.get('taskId'))
            ttype = t.get('taskType')
            tid = t.get('taskId')
            if ttype in seed_skip:
                self.note('  苗任务', '%s（%s，跳过）' % (title, seed_skip[ttype])); continue
            if ttype not in (1, 2, 4, 5):
                self.note('  苗任务', '%s（类型 %s，跳过）' % (title, ttype)); continue
            key = target_key(t)
            if ttype != 2 and not key:
                self.note('  苗任务', '%s（缺少目标页，跳过）' % title); continue
            if self.cfg.dry_run:
                self.note('  苗任务', '[dry] %s（将代做）' % title); continue
            if ttype == 2:
                r = post('/cmpp-manage/task/share',
                         {'channelCode': SEED_CHANNEL, 'visitUrl': SEED_PAGE_KEY,
                          'taskId': tid})
            else:
                r = post('/cmpp-manage/task/visit',
                         {'channelCode': SEED_CHANNEL, 'visitUrl': key, 'taskId': tid})
            data = r.get('data')
            if isinstance(data, str) and '频繁' in data:
                time.sleep(5)
                r = post('/cmpp-manage/task/share' if ttype == 2 else
                         '/cmpp-manage/task/visit',
                         {'channelCode': SEED_CHANNEL,
                          'visitUrl': SEED_PAGE_KEY if ttype == 2 else key,
                          'taskId': tid})
                data = r.get('data')
            if r.get('resultCode') == 0 and (data is None or data is True):
                done += 1
                self.note('  苗任务', '✅ %s 已上报' % title)
            elif r.get('resultCode') == 0:
                self.note('  苗任务', '%s：%s' % (title, str(data)[:40]))
            else:
                self.note('  苗任务', '%s 失败：%s %s' %
                          (title, r.get('resultCode'), str(r.get('msg'))[:40]))
            self.rest(0.8, 1.5)
        # ③ 领取待领阳光值气泡
        home = post('/api/activity/slgCredits/getSlgCreditsHomePage',
                    {'activityId': SEED_ACTIVITY_ID})
        recs = jget(home, 'data', 'unreceivedCreditsVOList', default=[]) or []
        got = 0
        for rec in recs:
            if not isinstance(rec, dict):
                continue
            if self.cfg.dry_run:
                self.note('  苗气泡', '[dry] 待领 %s + %s' %
                          (rec.get('showName'), rec.get('showNum'))); continue
            r = post('/api/activity/slgCredits/getGrowthBubble',
                     {'activityId': SEED_ACTIVITY_ID,
                      'creditsRecordId': rec.get('recordId'),
                      'referer': SEED_PAGE_KEY, 'channelNo': SEED_CHANNEL})
            if r.get('resultCode') == 0:
                got += int(rec.get('showNum') or 0)
                self.note('AI豆苗', '⭐ 领取 %s + %s' %
                          (rec.get('showName'), rec.get('showNum')))
            else:
                self.note('  苗气泡', '领取失败：%s %s' %
                          (r.get('resultCode'), str(r.get('msg'))[:40]))
            self.rest(0.5, 1.2)
        after = growth()
        self.note('AI豆苗', '阳光值 %s → %s%s' %
                  (before if before is not None else '?',
                   after if after is not None else '?',
                   '（本次领取 +%d）' % got if got else ''))

    # ------------------------------------------------ ⓿③ 周三充值日拼图闭环
    def task_wed(self):
        """周三充值日拼图全闭环：黑名单制做任务 + 抽光次数 + 集齐领取话费券。"""
        try:
            self.sso.session('hlw', WED_ENTRY)
        except Exception as e:  # noqa: BLE001
            self.note('周三充值日', '会话失败：%s' % str(e)[:60]); return

        def w(path, body=None):
            return self.sso.api('hlw', 'POST', '/hlwyxhdhub/api/wedrecharge/' + path,
                                body if body is not None else {})

        r = w('queryActivityInfo')
        if str(r.get('code')) != '0':
            self.note('周三充值日', '活动信息 %s %s（未开放/会话未立则跳过）' %
                      (r.get('code'), str(r.get('msg'))[:50]))
            return
        # ① 任务面（黑名单制）
        tasks = w('taskList').get('data') or []
        todo = [t for t in tasks if isinstance(t, dict)
                and t.get('taskId') not in WED_MANUAL_SKIP and t.get('status') != 2]
        done = 0
        for t in todo:
            tid = t.get('taskId')
            title = t.get('title') or tid
            add = t.get('addDrawTimes')
            if self.cfg.dry_run:
                self.note('  周三任务', '[dry] %s（+%s 次，将代做）' % (title, add)); continue
            ok = False
            fin = w('finishTask', {'taskId': tid})
            if str(fin.get('code')) == '0':
                ok = True
            else:
                # jt 握手：换目标页自己的会话再 getOneTaskInfo → openFinish
                try:
                    jt = w('getJtToken', {'taskId': tid}).get('data')
                    jump = str(t.get('jumpUrl') or '')
                    if isinstance(jt, str) and jt and jump.startswith('http'):
                        sep = '&' if '?' in jump else '?'
                        self.sso.session('wedjt', jump + sep + 'jtToken=' + jt)
                        info = self.sso.api('wedjt', 'POST',
                                            '/hlwyxhdhub/api/open/_pub/task/getOneTaskInfo',
                                            {'jtToken': jt})
                        d = info.get('data') if isinstance(info.get('data'), dict) else {}
                        ct = d.get('cToken')
                        if ct:
                            if d.get('scanTime'):
                                time.sleep(min(int(d.get('scanTime') or 0), 30) + 1)
                            fin2 = self.sso.api('wedjt', 'POST',
                                                '/hlwyxhdhub/api/open/_pub/task/openFinish',
                                                {'cToken': ct})
                            if is_success(fin2):
                                w('finishTask', {'taskId': tid})
                                ok = True
                except Exception as e:  # noqa: BLE001
                    self.note('  周三任务', '%s 握手异常：%s' % (title, str(e)[:50]))
            if ok:
                done += 1
                self.note('  周三任务', '✅ %s 代做成功（+%s 次）' % (title, add or 1))
            else:
                self.note('  周三任务', '%s 上报失败：%s %s' %
                          (title, fin.get('code'), str(fin.get('msg'))[:40]))
            self.rest(1.0, 2.0)
        if todo and not self.cfg.dry_run:
            self.note('周三充值日', '任务代做 %d/%d' % (done, len(todo)))
        # ② 抽光当日次数（当前拼图集齐即收手）
        def pic_state():
            st = w('queryPictureActInfo')
            return st.get('data') if isinstance(st.get('data'), dict) else {}

        def complete(pieces):
            return bool(pieces) and all(
                str(v).isdigit() and int(v) >= 1 for v in pieces.values())

        total = 0
        for _ in range(30):
            st = pic_state()
            try:
                times = int(st.get('drawTimes') or 0)
            except (TypeError, ValueError):
                times = 0
            if times <= 0:
                break
            try:
                active = int(st.get('activeIndex') or 1)
            except (TypeError, ValueError):
                active = 1
            pieces = (st.get('pictureList') or {}).get(str(active)) or {}
            if complete(pieces):
                self.note('周三充值日', '拼图 dp%s 已集齐但还剩 %s 次，暂存不用' %
                          (active, times))
                break
            if self.cfg.dry_run:
                self.note('周三充值日', '[dry] 跳过抽奖（剩 %s 次）' % times)
                break
            r = w('drawPicture', {'round': 'dp%d' % active})
            if str(r.get('code')) != '0':
                self.note('周三充值日', '抽奖停止：%s %s' %
                          (r.get('code'), str(r.get('msg'))[:40]))
                break
            total += 1
            self.rest(1.0, 2.0)
        if total:
            self.note('周三充值日', '✅ 本次共抽 %d 次' % total)
        # ③ 已集齐档位自动领取 + 进度汇报
        st = pic_state()
        pics = st.get('pictureList') or {}
        for idx in sorted(pics, key=lambda x: int(x) if str(x).isdigit() else 99):
            pieces = pics.get(idx) or {}
            have = sum(1 for v in pieces.values()
                       if str(v).isdigit() and int(v) >= 1)
            try:
                rnd = WED_PUZZLE_RECEIVE.get(int(idx))
            except (TypeError, ValueError):
                rnd = None
            label = WED_TIERS.get('dp%s' % idx, 'dp%s' % idx)
            if not rnd or not complete(pieces):
                self.note('  拼图', '%s 碎片 %d/%d' % (label, have, len(pieces)))
                continue
            if self.cfg.dry_run:
                self.note('  拼图', '[dry] %s 已集齐（将领 %s）' %
                          (label, WED_RECEIVE_LABELS[rnd])); continue
            r = w('receiveNew', {'round': rnd})
            msg = str(r.get('msg') or '')
            if str(r.get('code')) == '0':
                names = '、'.join(filter(None, (
                    p.get('prizeName') for p in
                    (r.get('data') or {}).get('actPrizes') or []
                    if isinstance(p, dict))))
                self.note('周三充值日', '⭐ 领取 %s 成功（%s）' %
                          (WED_RECEIVE_LABELS[rnd], names or '话费券'))
            elif '已领取' in msg:
                self.note('  拼图', '%s 已领取过' % WED_RECEIVE_LABELS[rnd])
            else:
                self.note('周三充值日', '%s 领取失败：%s' %
                          (WED_RECEIVE_LABELS[rnd], msg or r.get('code')))
            self.rest(1.0, 2.0)

    # ------------------------------------------------ ⓿④ 追剧每日视频任务
    def _video_todo(self, data):
        """周日会下发两个独立任务（各 maxPerDay=1），其他时间 1 个；按 taskStage/todayRemain/earnOpportunity 自适应，不写死任务 ID。"""
        todo = [t for t in (data.get('undoTodayTaskList') or [])
                if isinstance(t, dict) and t.get('taskStage') == 'UNDO'
                and int(t.get('todayRemain') or 0) > 0
                and int(t.get('earnOpportunity') or 0) > 0]
        vids = [t for t in todo
                if '视频' in ('%s%s' % (t.get('name') or '', t.get('description') or ''))
                or 'cmlive' in str(t.get('url') or '')]
        if not vids and len(todo) == 1:  # 服务端改名后若只剩一个待办，直接当视频任务
            vids = todo
        return vids

    def task_videoday(self):
        """追剧「看精彩视频」每日任务：jtToken 握手 + fiveMinuteTaskFinish，每完成一个重拉清单。"""
        try:
            self.sso.session('video', ACTS['video']['entry'])
        except Exception as e:  # noqa: BLE001
            self.note('视频任务', '会话失败：%s' % str(e)[:60]); return
        comp = ACTS['video']['task_component']

        def qget():
            r = self.sso.api('video', 'GET',
                             '/qwhdhub/api/diyVideoDayRedesign/task/queryDiyVideoTask')
            return r if isinstance(r.get('data'), dict) else {}

        def remain():
            wv = self.sso.api('video', 'POST', '/qwhdhub/diyLottery/period/remain/%s' % comp, {})
            try:
                return int(jget(wv, 'data', 'remain'))
            except (TypeError, ValueError):
                return None

        first = qget()
        vids = self._video_todo(first.get('data') or {})
        if not vids:
            self.note('视频任务', '今日已完成（或已到周上限 8 次）'); return
        if self.cfg.dry_run:
            self.note('视频任务', '[dry] 待办 %d 个：%s' %
                      (len(vids), '、'.join(str(t.get('name') or '看精彩视频')
                                            for t in vids)))
            return
        ext = self._fresh_ext(CMLIVE_SOURCE_ID)
        if not ext:
            self.note('视频任务', 'getYDRZToken(%s) 未签发，跳过' % CMLIVE_SOURCE_ID)
            return
        r = self.http.request('POST', CMLIVE_BASE + '/user/auth/cmpassLoginByCmToken',
                              body={'cmpassToken': ext},
                              headers={'Accept': 'application/json, text/plain, */*',
                                       'Origin': 'https://v.cmcc-cs.cn',
                                       'Referer': CMLIVE_REFERER,
                                       'X-Requested-With': 'XMLHttpRequest'})
        resp = r.get('json') if isinstance(r.get('json'), dict) else {}
        jwt = resp.get('object') if isinstance(resp.get('object'), str) else ''
        if str(resp.get('rtnCode')) != '0' or not jwt:
            self.note('视频任务', '视频站登录被拒：%s %s' %
                      (resp.get('rtnCode'), str(resp.get('rtnMsg'))[:50]))
            return

        def cget(path, params, ref):
            rr = self.http.request('GET', CMLIVE_BASE + path, params=params,
                                   headers={'Accept': 'application/json, text/plain, */*',
                                            'Origin': 'https://v.cmcc-cs.cn',
                                            'Referer': ref,
                                            'X-Requested-With': 'XMLHttpRequest',
                                            'Authorization': jwt})
            return rr.get('json') if isinstance(rr.get('json'), dict) else {}

        before = remain()
        ok = 0
        for _ in range(5):
            vids = self._video_todo(qget().get('data') or {})
            if not vids:
                break
            t = vids[0]
            name = t.get('name') or '看精彩视频'
            jt = str(t.get('param') or '')
            if not jt.startswith('QWHD_') and t.get('taskId'):
                jt = 'QWHD_' + str(t.get('taskId'))
            if not jt:
                self.note('视频任务', '%s 缺少 jtToken，跳过' % name); break
            turl = str(t.get('url') or '')
            if turl.startswith('/qwhdhub'):
                try:  # startBrowse 登记（不跟随跳转；失败不阻断）
                    self.sso.api('video', 'GET', turl)
                except Exception:  # noqa: BLE001
                    pass
            ref = CMLIVE_REFERER + '&jtToken=' + jt
            info = cget('/advert/lottery/getFiveMinuteTaskInfo', {'token': jt}, ref)
            obj = info.get('object') if isinstance(info.get('object'), dict) else {}
            d = obj.get('data') if isinstance(obj.get('data'), dict) else {}
            if str(info.get('rtnCode')) != '0' or not is_success(obj) or not d:
                self.note('视频任务', '%s 领取失败：%s %s' %
                          (name, info.get('rtnCode'),
                           str(obj.get('msg') or info.get('rtnMsg'))[:40]))
                break
            if str(d.get('status') or '') == '0':
                self.note('视频任务', '✅ %s 已完成（%s）' %
                          (name, d.get('finishDesc') or '奖励已发放'))
                ok += 1
                time.sleep(1)
                continue
            ct = d.get('cToken')
            if not ct:
                self.note('视频任务', '%s 未下发 cToken，跳过' % name); break
            fin = cget('/advert/lottery/fiveMinuteTaskFinish', {'token': ct}, ref)
            fobj = fin.get('object') if isinstance(fin.get('object'), dict) else {}
            if str(fin.get('rtnCode')) == '0' and is_success(fobj) and fobj.get('data'):
                ok += 1
                self.note('视频任务', '✅ %s 完成' % name)
            else:
                self.note('视频任务', '%s 上报失败：%s %s' %
                          (name, fin.get('rtnCode'),
                           str(fobj.get('msg') or fin.get('rtnMsg'))[:40]))
                break
            time.sleep(1)  # 给任务清单一点刷新时间
        after = remain()
        if before is not None and after is not None and after > before:
            self.note('视频任务', '⭐ 抽奖次数 +%d（现 %s）' % (after - before, after))
        elif ok:
            self.note('视频任务', '完成 %d 个任务（余量待活动页刷新）' % ok)

    # ------------------------------------------------ ⓿⑤ 辽宁充值先领券
    def task_lnlottery(self):
        """**辽宁「充值先领券」每日抽奖**（activityId 1025093012；**默认关闭** （`CMCC_LNLOTTERY=1` 开启），避免非辽号每日空跑一次写请求。"""
        if str(self.cfg.cmcc_lnlottery or '').strip() not in ('1', 'true', 'True'):
            return
        from urllib.parse import quote
        back = quote(LN_BACK_URL, safe='')
        try:
            self.sso.session('ln', LN_ENTRY)
        except Exception as e:  # noqa: BLE001
            self.note('辽宁领券', '会话失败：%s' % str(e)[:60]); return

        def task_list():
            r = self.sso.api('ln', 'GET', '/qwhdhub/task/list?backUrl=' + back)
            return r.get('data') if isinstance(r.get('data'), list) else []

        # ① 任务面（browse 自动做；邀请类跳过）
        for t in task_list():
            if not isinstance(t, dict):
                continue
            name = t.get('name') or t.get('taskId')
            stage = t.get('taskStage')
            ttype = t.get('type') or ''
            turl = str(t.get('url') or '')
            add = t.get('earnOpportunity') or 1
            if stage == 'DONE':
                self.note('  辽宁任务', '✓ %s 已完成' % name)
            elif stage != 'UNDO':
                self.note('  辽宁任务', '· %s 今日不可再做' % name)
            elif ttype == 'browse' and turl.startswith('/qwhdhub/task/startBrowse'):
                if self.cfg.dry_run:
                    self.note('  辽宁任务', '[dry] %s（+%s 次，将代做）' % (name, add))
                    continue
                try:
                    self.sso.api('ln', 'GET', turl)
                    cur = {}
                    for wait in (0, 1, 2, 3):  # 轮询代替固定等待：一登记 DONE 就继续
                        if wait:
                            time.sleep(wait)
                        cur = next((x for x in task_list()
                                    if x.get('taskId') == t.get('taskId')), {})
                        if cur.get('taskStage') == 'DONE':
                            break
                    if cur.get('taskStage') == 'DONE':
                        self.note('  辽宁任务', '✅ %s 完成（+%s 次）' % (name, add))
                    else:
                        self.note('  辽宁任务', '%s 未生效' % name)
                except Exception as e:  # noqa: BLE001
                    self.note('  辽宁任务', '%s 失败：%s' % (name, str(e)[:50]))
            else:
                self.note('  辽宁任务', '· %s（%s）需真实操作，跳过' % (name, ttype or '未知'))
            self.rest(0.3, 0.8)
        # ② 每日抽奖（DO_TASK 时做任务后重抽一次）
        if self.cfg.dry_run:
            self.note('辽宁领券', '[dry] 未抽奖'); return
        r = self.sso.api('ln', 'GET', '/qwhdhub/lottery/lotterySafely')
        resp = r if isinstance(r, dict) else {}
        if str(resp.get('code') or '').startswith('HTTP_'):
            self.note('辽宁领券', '响应非 JSON（%s）：该活动仅限辽宁移动号码参与' %
                      str(resp.get('msg'))[:40])
            return
        if resp.get('code') == 'DO_TASK':
            task = resp.get('data') if isinstance(resp.get('data'), dict) else {}
            ttype = task.get('type') or ''
            turl = str(task.get('url') or '')
            if 'share' in ttype:
                self.note('辽宁领券', '免费机会已用完；剩余任务为邀请类（需真实好友）')
                return
            if turl:
                try:
                    self.note('  辽宁任务', 'DO_TASK %s（%s），尝试完成' %
                              (task.get('name'), ttype))
                    if ttype == 'hvb':
                        self.sso.api('ln', 'POST', turl, {})
                    else:
                        self.sso.api('ln', 'GET', turl)
                    time.sleep(1)
                    resp = self.sso.api('ln', 'GET', '/qwhdhub/lottery/lotterySafely')
                except Exception as e:  # noqa: BLE001
                    self.note('辽宁领券', '任务尝试失败：%s' % str(e)[:50])
                    return
        if resp.get('code') != 'SUCCESS':
            self.note('辽宁领券', '未完成：%s %s' %
                      (resp.get('code'), str(resp.get('msg'))[:50]))
            return
        d = resp.get('data') if isinstance(resp.get('data'), dict) else {}
        prize = d.get('prizeName') or ''
        if d.get('prizeValue'):
            unit = {'FLOW': 'MB', 'FEE': '元'}.get(d.get('prizeCategory'), '')
            prize += '（%s%s）' % (d.get('prizeValue'), unit)
        if prize:
            self.note('辽宁领券', '⭐ 中得 %s' % prize)
        else:
            self.note('辽宁领券', '未中奖（%s）' % str(resp.get('msg'))[:30])

    # ------------------------------------------------ ⓿⑥ 话费余额（原生只读）
    def task_fee(self):
        """**话费余额**（App 原生网关只读：`20016` 实时费用 + `20008` 缴费查询）。"""
        if not self.cfg.cmcc_app_token:
            self.note('话费余额', '跳过：未配置 CMCC_APP_TOKEN（原生面需要 App 票据）')
            return
        for code, label in (('20016', '实时费用'), ('20008', '缴费查询')):
            r = self.native.call(code, {'cellNum': self.cfg.cmcc_phone})
            if r.get('retCode') != '000000':
                self.note('话费余额', '%s（%s）失败：%s %s' %
                          (label, code, r.get('retCode'), str(r.get('retDesc'))[:50]))
            else:
                d = r.get('data') if isinstance(r.get('data'), dict) else {}
                txt = ''
                for k in ('totalBalanceValue', 'balanceValue', 'canUseBalanceValue',
                          'totalAvailableBalance'):
                    if d.get(k) not in (None, ''):
                        txt = '%s %s%s' % (label, d[k], d.get('totalBalanceUnit') or '元')
                        break
                self.note('话费余额', '⭐ %s' % (txt or
                          json.dumps(d, ensure_ascii=False)[:110]))
            self.rest(0.8, 1.5)

    def task_sweep(self):
        """把活动规格（211 端点）里**已探测到但未纳入专项任务**的端点全部过一遍（只读）。目的：让"探测到的任务"没有盲区——每条都至少被调过一次并留下状态；具体收益动作（抽奖/秒杀/邀请/下单）不在此处触发。"""
        def sub(tag, fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.note('扫描', '%s 异常：%s' % (tag, str(e)[:80]))
            self.rest(0.6, 1.2)

        def q(key, entry, method, path, body=None, params=None):
            """确保某活动页有会话后调一个 qwhdhub/hlw 端点，返回 json。"""
            self.sso.session(key, entry)
            return self.sso.api(key, method, path, body, params)

        # ---- 趣玩盒：lx 红包 / 新人礼 / 掼蛋榜 / 组队 / 秒杀券 / mark31 扩展 ----
        sub('lx中奖记录', lambda: self.note(
            '扫描·lx红包', '历史中奖 %s 条' % len(jget(q(
                'lx', BASE + '/qwhdhub/diy-client/1126037461?A_C_CODE=hL58BH6l2U',
                'POST', '/qwhdhub/api/diyLxRedPacket/diyLottery/queryHisWinRecord'),
                'data', default=[]) or [])))
        sub('lx口令', lambda: self.note(
            '扫描·lx红包', '口令组件：%s' % jget(q(
                'lx', BASE + '/qwhdhub/diy-client/1126037461?A_C_CODE=hL58BH6l2U',
                'POST', '/qwhdhub/api/diyLxRedPacket/diyLottery/queryLxWord',
                {'componentId': ''}), 'msg', default='?')))
        sub('新人礼', lambda: self.note(
            '扫描·新人礼', '可领=%s 奖品 %s 项' % (
                jget(q('newcomer', BASE + '/qwhdhub/diy-client/1126072224?A_C_CODE=3C43AjX2Cz',
                       'GET', '/qwhdhub/api/diyNewcomerGift/getClaimFlag'), 'data', default='?'),
                len(jget(q('newcomer',
                           BASE + '/qwhdhub/diy-client/1126072224?A_C_CODE=3C43AjX2Cz',
                           'POST', '/qwhdhub/api/diyNewcomerGift/queryModulePrize'),
                         'data', default=[]) or []))))
        sub('掼蛋榜', lambda: self.note(
            '扫描·掼蛋', '周榜：%s' % str(jget(q(
                'guandan', BASE + '/qwhdhub/diy-client/1126090514',
                'GET', '/qwhdhub/api/diyAiTryFirst/diyGuandanGame/V2/getUserRankInfo/1'),
                'data'))[:80]))
        team_body = {'componentId': '4DwOvs0-f1qlH03siz5_', 'activityId': 1026080780}
        sub('组队邀请', lambda: self.note(
            '扫描·组队', '邀请链接 %s' % str(jget(q(
                'team', BASE + '/qwhdhub/diy-client/1126082530?A_C_CODE=10hep1ZhkL',
                'POST', '/qwhdhub/diyTeam/getKeyword', team_body), 'data'))[:90]))
        sub('组队进度', lambda: self.note(
            '扫描·组队', '进度=%s' % str(jget(q('mark31', ACTS['mark31']['entry'],
                'POST', '/qwhdhub/diyTeam/progress', team_body), 'data'))[:70]))
        sub('组队成员', lambda: self.note(
            '扫描·组队', '成员 %s 人' % len(jget(q('mark31', ACTS['mark31']['entry'],
                'POST', '/qwhdhub/diyTeam/queryMembers', team_body), 'data', default=[]) or [])))
        sub('组队签到状态', lambda: self.note(
            '扫描·组队签到', str(jget(q('mark31', ACTS['mark31']['entry'],
                'POST', '/qwhdhub/api/mark/groupmark/status'), 'data'))[:70]))
        sub('秒杀券', lambda: self.note(
            '扫描·秒杀券', '%s 张在售（下单不做）' % len(jget(q(
                'video', ACTS['video']['entry'], 'POST',
                '/qwhdhub/diyLottery/querySeckillCoupons',
                {'componentId': '50E3cC0-lD5Gw0gMrrZ9'}), 'data', default=[]) or [])))
        sub('mark31月信息', lambda: self.note(
            '扫描·mark31', '本月：%s' % str(jget(q('mark31', ACTS['mark31']['entry'],
                'POST', '/qwhdhub/api/mark/month/monthInfo'), 'data'))[:70]))
        sub('mark31我的任务', lambda: self.note(
            '扫描·mark31', '我的任务 %s 项' % len(jget(q('mark31', ACTS['mark31']['entry'],
                'POST', '/qwhdhub/api/mark/mark31/myTaskInfo'), 'data', default=[]) or [])))

        # ---- 福利营销：奖品/抽奖记录/楼层/追剧用户 ----
        sub('hlw奖品', lambda: self.note(
            '扫描·福利营销', '奖品 %s 项' % len(jget(q('hlw', ACTS['hlw']['entry'],
            'POST', '/hlwyxhdhub/api/wedrecharge/getPrizeList'), 'data', default=[]) or [])))
        sub('hlw抽奖记录', lambda: self.note(
            '扫描·福利营销', '抽奖记录 %s 条' % len(jget(q('hlw', ACTS['hlw']['entry'],
            'POST', '/hlwyxhdhub/api/wedrecharge/queryDrawPrizeInfo'), 'data', default=[]) or [])))
        sub('hlw追剧用户', lambda: self.note(
            '扫描·福利营销', 'userInfo：%s' % str(jget(q('hlw', ACTS['hlw']['entry'],
            'POST', '/hlwyxhdhub/api/sunvideo/userInfo'), 'data'))[:70]))

        self.coc3_ensure(force=True)
        if self.coc3_token:
            b3 = 'https://dev.coc.10086.cn'
            h3 = self._coc3_headers()
            ck3 = 'COC3_LOGIN_TOKEN=%s' % self.coc3_token

            def c3(path, body=None):
                r = self.http.request('POST', b3 + path, headers=h3, body=body or {}, cookies=ck3)
                return r.get('json') or {}
            sub('coc3签到任务', lambda: self.note(
                '扫描·coc3', 'taskSignInfo：%s' % str(jget(c3(
                    '/coc3/coc3-market-activity/cmpp-manage/task/getTaskSignInfo',
                    {'taskId': 2999}), 'data'))[:80]))
            sub('coc3个性化任务', lambda: self.note(
                '扫描·coc3', 'individuation：%s' % str(jget(c3(
                    '/coc3/coc3-market-activity/cmpp-manage/task/queryIndividuationTask',
                    {'channelCode': COC3_CHANNEL_CODE, 'taskIdList': []}), 'data'))[:80]))
            for aid, tag in ((17451, '剩余次数'), (17452, '当前抽奖'), (17524, '中奖记录'), (17756, '抽奖列表')):
                if tag == '当前抽奖':
                    body = {'activityId': aid, 'scene': 1}
                elif tag == '中奖记录':
                    body = {'activityId': aid}
                elif tag == '抽奖列表':
                    body = {'actIds': [aid], 'showTime': None}
                else:
                    body = {'activityId': aid}
                path = {
                    '剩余次数': '/coc3/coc3-market-activity/cmpp-manage/lottery/residueLotteryCount',
                    '当前抽奖': '/coc3/coc3-market-activity/cmpp-manage/lottery/currentLottery',
                    '中奖记录': '/coc3/coc3-market-activity/cmpp-manage/lottery/getRemainLotteryRecord',
                    '抽奖列表': '/coc3/coc3-market-activity/api/activity/queryLotteryList',
                }[tag]
                sub('coc3%s' % tag, lambda p=path, bd=body, t=tag: self.note(
                    '扫描·coc3', '%s(%s)：%s' % (t, jget(bd, 'activityId', default='?'),
                                                 str(jget(c3(p, bd), 'data'))[:70])))
            sub('coc3会员', lambda: self.note(
                '扫描·coc3', 'myVip：%s' % str(jget(c3(
                    '/coc3/coc3-market-member/api/vip/myVip'),
                    'data'))[:60].replace('\n', ' ')))
            sub('coc3积分', lambda: self.note(
                '扫描·coc3', '权益豆余额：%s' % jget(c3(
                    '/coc3/gr-integral-ability/api/external/score/digIntel/getBalance'),
                    'data')))

        # ---- coc web6：用户信息 / 网龄 ----
        sub('coc用户', lambda: self.note(
            '扫描·coc', 'queryUserInfo：%s' % str(self._coc_call(
                '/coc/activities/common/queryUserInfo', {}))[:70]))
        sub('coc网龄', lambda: self.note(
            '扫描·coc', 'growth：%s' % str(self._coc_call(
                '/coc/activities/growth/query',
                {'touchCode': COC_7DAY_TOUCH_CODE,
                 'provinceCode': self.cfg.cmcc_province_code}))[:70]))

        # ---- 全球通：幸运盒子 / 会员等级 / 首页领取状态 ----
        hm = {'Accept': 'application/json, text/plain, */*', 'login-check': '1',
              'X-Requested-With': APP_PKG,
              'Referer': 'https://qqt.cmicrwx.cn/2016tyjf/xhmqqthy/res/wap/xdrNormal.html?sspId=75714'}
        cm = self._cmic_fresh_cookie() or self.cfg.cmcc_cmic_cookie

        def cmg(path, params=None):
            r = self.http.request('GET', 'https://qqt.cmicrwx.cn' + path, headers=hm,
                                  params=params, cookies=cm)
            return r.get('json') or {}
        sub('cmic幸运盒子', lambda: self.note(
            '扫描·全球通', '幸运盒子 %s 项' % len(jget(cmg(
            '/2016tyjf/xhmqqthy/luckBox/infoList',
            {'channelCode': '75714', 'type': '1'}), 'data', default=[]) or [])))
        sub('cmic会员等级', lambda: self.note(
            '扫描·全球通', 'vip：%s' % str(jget(self.http.request(
            'POST', 'https://qqt.cmicrwx.cn/2016tyjf/xhmqqthy/member/func/queryVipLevel.do',
            headers=hm, form={'sspId': '75714', 'tag': 'qqtindex'},
            cookies=cm).get('json'), 'data'))[:70]))
        sub('cmic首页领取', lambda: self.note(
            '扫描·全球通', 'indexReceive：%s' % str(cmg(
            '/2016tyjf/xhmqqthy/member/func/queryIndexReceive.do'))[:70]))

    def _coc_call(self, path, body):
        """coc web6 明文端点（自动现签 User-Token；复用 coc7 的成果）。"""
        if not self.coc_token:
            ext = self._fresh_ext('056002')
            if not ext or _aes() is None:
                return {'why': '无 ext 或缺 pycryptodome'}
            ib, extra = coc_encrypt({'verifyType': 18, 'ext': ext})
            h = {'Accept': 'application/json, text/plain, */*',
                 'Content-Type': 'application/json;charset=utf-8',
                 'Origin': 'https://dev.coc.10086.cn',
                 'Referer': 'https://dev.coc.10086.cn/coc/web6/dailyLoginEvent/'}
            h.update(extra)
            r = self.http.request('POST', 'https://dev.coc.10086.cn/coc/user/loginCheck',
                                  headers=h, body=ib)
            self.coc_token = (r.get('set_cookies') or {}).get('User-Token') or ''
        if not self.coc_token:
            return {'why': 'User-Token 获取失败'}
        r = self.http.request('POST', 'https://dev.coc.10086.cn' + path,
                              headers={'Accept': 'application/json, text/plain, */*',
                                       'Content-Type': 'application/json;charset=utf-8',
                                       'Origin': 'https://dev.coc.10086.cn',
                                       'Referer': 'https://dev.coc.10086.cn/coc/web6/',
                                       'X-Requested-With': APP_PKG},
                              body=body, cookies='User-Token=%s' % self.coc_token)
        return r.get('json') or {}


# 入口
def notify(title, content):
    try:
        sys.path.insert(0, os.getcwd())
        from notify import send  # type: ignore
        send(title, content)
        return True
    except Exception:  # noqa: BLE001
        return False


def selftest():
    res = []

    def ck(n, c, e=''):
        res.append((n, bool(c), e))
    cfg = Cfg()
    ck('配置区含账号', cfg.cmcc_phone != '' and cfg.cmcc_app_token != '', cfg.cmcc_phone)
    ck('任务开关解析（all）', cfg.want('mark31') and cfg.want('assess'), sorted(cfg.task_set))
    ck('活动常量：mark31=1021122301',
       '1021122301' in ACTS['mark31']['entry'] and len(ACTS) >= 5, '')
    ck('代币组件常量在位', ACTS['saturday']['task_component'] == '50HQAg0-9YaSW4St7MDT', '')
    ck('抽奖窗口常量在位', ACTS['video']['lottery']['weekday'] == 7, '')
    ck('SSO 登录页正则可命中', bool(RE_LOGIN_PATH.search(
        "var loginPath = '/appTokenLogin?sid=abc123';")), '')
    ck('userCheckId = 手机号十六进制',
       all(c in '0123456789abcdef' for c in '%x' % int(cfg.cmcc_phone)),
       '%x' % int(cfg.cmcc_phone))
    if _aes() is not None:
        ck('AES-ECB 往返（趣玩贵州 32 字符密钥）',
           aes_ecb_dec(MIGU_KEY, aes_ecb_enc(MIGU_KEY, '{"a":"中文"}')) == '{"a":"中文"}', '')
        body, hdr = coc_encrypt({'ext': 'YZsidssol-demo'})
        ck('coc 加密体只有 cocEnContent', list(body) == ['cocEnContent'], '')
        ck('coc 三个头齐全', set(hdr) == {'Coc-En-Version', 'coc-aurora', 'coc-aurora-v'}, '')
        ck('AES-CBC（coc3 续期）16 字节对齐',
           len(base64.b64decode(aes_cbc_enc(b'1de23e756aaf3cc2', b'1de23e756aaf3cc2', 'x'))) % 16 == 0, '')
    else:
        ck('pycryptodome 未装（coc/趣玩贵州任务将跳过，属预期）', True, '')
    try:
        n, e = _rsa_modulus()
        ck('coc RSA 公钥（2048 位, e=65537）', n.bit_length() == 2048 and e == 65537, '')
    except Exception as exc:  # noqa: BLE001
        ck('coc RSA 公钥解析', False, str(exc)[:70])
    # 幂等归一逻辑
    ck('HAVE_MARKED/PRIZE_NO_CONFIG 判定字符串在位',
       'HAVE_MARKED' in open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('跳转任务握手（hlw getOneTaskInfo/openFinish）在位',
       hasattr(Runner, '_open_finish_task'), '')
    ck('全量只读扫描任务在位（211 端点收尾）',
       hasattr(Runner, 'task_sweep') and 'querySeckillCoupons' in
       open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('lx 红包专项任务在位（只查不抢）',
       hasattr(Runner, 'task_lxrp') and 'diyLxRedPacket' in
       open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('河南本省签到任务在位（signLottery/init.do + click.do）',
       hasattr(Runner, 'task_henan_sign') and 'signLottery' in
       open(os.path.abspath(__file__), encoding='utf-8').read()
       and Runner.HN_SIGN_CHANNEL == 'channel_YHY', '')
    ck('和彩云任务在位（gp.mcloud.139.com + 任务/福袋端点）',
       hasattr(Runner, 'task_mcloud') and Runner.MCLOUD_GP == 'https://gp.mcloud.139.com'
       and 'toDoTask/taskInfos' in open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('139 云朵中心签到任务在位（tyrzLogin 免登链 + startSignIn）',
       hasattr(Runner, 'task_139sign') and hasattr(Runner, '_mcloud_login')
       and Runner.TYRZ_SALT == 'sekaMdYYLIZfbCfm'
       and 'auth/tyrzLogin' in open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('mcloud 活动全家桶在位（8 活动面 + executeTask/blindbox/rafflecode 兑换链）',
       hasattr(Runner, 'task_mcloud_acts') and hasattr(Runner, '_mcloud_udi')
       and 'toDoTask/executeTask' in open(os.path.abspath(__file__), encoding='utf-8').read()
       and 'blindbox/lottery' in open(os.path.abspath(__file__), encoding='utf-8').read()
       and 'rafflecode/exchange/redeem' in open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('年度权益任务在位（springgift 三步：任务/completeTask/lotteryPrize）',
       hasattr(Runner, 'task_springgift')
       and 'springgift/completeTask' in open(os.path.abspath(__file__), encoding='utf-8').read()
       and 'springgift/lotteryPrize' in open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('会员日 cloudCircle 面在位（playAI register + cmi/vip + mcloudday）',
       hasattr(Runner, 'task_huiyuanri')
       and 'funai/api/page/register' in open(os.path.abspath(__file__), encoding='utf-8').read()
       and 'mcloudday/blindbox/lotteryInfo' in open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('hlw 路由页令牌修复在位（/1024101716 路由页下发 SESSION_TOKEN）',
       'route_page = page.replace' in open(os.path.abspath(__file__), encoding='utf-8').read()
       and '/1024101716?' in open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('网龄成长任务在位（growth/query + submit/evaluate）',
       hasattr(Runner, 'task_agegrowth')
       and 'activities/growth/submit/evaluate' in
       open(os.path.abspath(__file__), encoding='utf-8').read(), '')
    ck('App 原生面已内联（x-token/AES 密钥长度与路径表正确）',
       hasattr(Runner, 'task_wallet') and hasattr(Runner, 'task_native')
       and hasattr(Runner, 'task_discover')
       and len(XTOKEN_KEY) == 16 and len(N_AES_ENC_KEY) == 16
       and len(N_AES_DEC_KEY) == 16 and len(N_AES_IV) == 16 and len(CID_KEY) == 24
       and Native.PATHS.get('50528', '').endswith('getCardVolume')
       and Native.PATHS.get('30077', '').endswith('getSecurityId'), '')
    _src = open(os.path.abspath(__file__), encoding='utf-8').read()
    ck('coc3 抽奖提交端点已还原（/cmpp-manage/lottery/lottery + 成功码 60000/60010）',
       hasattr(Runner, 'task_coc3_complete')
       and '/cmpp-manage/lottery/lottery' in _src
       and Runner.COC3_LOTTERY_OK == ('60000', '60010')
       and 'SCORE_REFUSE' in _src, '')
    ck('coc3 风控指纹配置位在位（CMCC_RISK_TOKEN → gr-risk-p3 头）',
       'CMCC_RISK_TOKEN' in DEFAULTS and 'gr-risk-p3' in _src, '')
    ck('coc3 活动全量扫描任务在位（Accept:application/json 破 XML 陷阱）',
       hasattr(Runner, 'task_coc3_scan') and len(Runner.COC3_REAL_ACTS) >= 10
       and "'Accept'] = 'application/json'" in _src, '')
    ck('qwhdhub 任务面驱动在位（getTaskAward + mark31/taskAward）',
       hasattr(Runner, 'task_qwhd_complete')
       and '/qwhdhub/api/mark/task/getTaskAward' in _src
       and '/qwhdhub/api/mark/mark31/taskAward/' in _src, '')
    ck('qwhdhub 只读面/组队面在位（diyTeam/progress + 已取证端点）',
       hasattr(Runner, 'task_qwhd_readall') and hasattr(Runner, 'task_qwhd_team')
       and '/qwhdhub/diyTeam/progress' in _src
       and '/qwhdhub/api/diyAiTryFirst/diyGuandanGame/V2/getUserRankInfo/1' in _src, '')
    _code = re.sub(r'"""[\s\S]*?"""', '', _src)
    _code = re.sub(r"'''[\s\S]*?'''", '', _code)
    _code = '\n'.join(l.split('#')[0] for l in _code.split('\n'))
    _bad_hlw = ['/hlwyxhdhub/' + 'lottery/config', '/hlwyxhdhub/' + 'videoUser/']
    _bad_any = ['503' + '76', 'toDoTask/' + 'receiveReward', '/ycloud/mcloudday/' + 'task/list']
    ck('hlw 全量面只用已取证端点（queryActivityInfo 族，无臆造路径）',
       hasattr(Runner, 'task_hlw_complete')
       and '/hlwyxhdhub/api/wedrecharge/queryActivityInfo' in _code
       and all(b not in _code for b in _bad_hlw), '')
    ck('★ 纪律：臆造端点已从**代码**清除（臆造编号/臆造路径）',
       all(b not in _code for b in _bad_any), '')
    ck('原生权益任务已改为只读清点（5 个已实测 000000 端点）',
       hasattr(Runner, 'task_native_rights')
       and "'50375', 'VIP权益列表'" in _src, '')
    ck('coc3 每日签到提交链在位（accumulateSign + 渠道码 + 中奖记录）',
       hasattr(Runner, 'task_coc3_daily_checkin')
       and '/cmpp-manage/task/accumulateSign' in _code
       and '/cmpp-manage/task/myAwardRecord' in _code
       and Runner.COC3_SIGN_TASKS == ((2999, '好礼月月领'),)
       and Runner.COC3_SIGN_CHANNEL == 'P00000067264', '')
    ck('coc3 抽奖前有资格预检（checkQualificByActivityId，GET）',
       'checkQualificByActivityId' in _code
       and "'GET', base + '/coc3/coc3-market-activity/arrange/'" in _code, '')
    ck('139 云朵收益闭环在位（receiveV3 体字段 + exchangeList/exchangeV3）',
       '/ycloud/signin/page/receiveV3' in _code
       and "'cloudId'" in _code and "'client': 'out'" in _code
       and '/ycloud/signin/page/exchangeList' in _code
       and '/ycloud/signin/page/exchangeV3' in _code, '')
    ck('139 设备 ID 闸门在位（CMCC_SM_DEVICE_ID → 体 deviceId + 头 appVersion）',
       'CMCC_SM_DEVICE_ID' in DEFAULTS and "'deviceId': dev" in _code
       and "hd2['appVersion']" in _code, '')
    ck('139 兑换滑块闸门如实报（不伪造轨迹）',
       '滑块验证' in _code and '501' in _code, '')
    ck('139 滑块自动求解器在位（NCC 图像匹配 → puzzleOffset）',
       hasattr(Runner, '_mcloud_slide_offset') and hasattr(Runner, '_mcloud_exchange')
       and 'slide/getSlide' in _code and 'puzzleOffset' in _code
       and Runner.MCLOUD_SLIDE_SOLVE is True, '')
    ck('139 兑换体与源码一致（prizeId/client/clientVersion/puzzleOffset/smsCode）',
       "'client': 'out'," in _code and "'clientVersion': '1254'" in _code
       and "'puzzleOffset': off" in _code, '')
    ck('139 JWT 文件缓存 + 强制现签开关在位',
       '.mcloud_jwt_cache.json' in _code and 'CMCC_MCLOUD_NO_CACHE' in DEFAULTS
       and hasattr(Runner, '_mcloud_login_fresh'), '')
    ck('原生票据优先读项目 session.json（60012 轮换票据后不再 420000）',
       '04_脚本' in _code and 'session.json' in _code, '')
    ck('auto_login 已对齐主项目（imei=oaid: 形态 + pageUrl/channelId）',
       "'oaid:%s' % N_DEVICE['oaid']" in _code and "'ZF10000'" in _code
       and "'channelId': '1'" in _code, '')
    ck('默认账号已切到票据健康的账号',
       DEFAULTS['CMCC_PHONE'] == ''
       and DEFAULTS['CMCC_PROVINCE_CODE'] == '371', DEFAULTS['CMCC_PHONE'])
    ck('全球通会话现签链在位（ssoTokenLogin + access_token 新发判据 + 签到 click）',
       hasattr(Runner, '_cmic_fresh_cookie') and 'ssoTokenLogin.action' in _code
       and 'access_token' in _code and 'sign/click' in _code, '')
    ck('60012 票据轮换写回 session.json（读→续→写闭环）',
       "d['cookie'] = self.cookie" in _code and 'session.json' in _code, '')
    ck('AI豆任务 open 链在位（_open_finish_task_v2 + cToken/taskToken 双路 + 直接领奖）',
       hasattr(Runner, '_open_finish_task_v2')
       and "'jtToken': tt" in _code and 'getTaskAward' in _code, '')
    ck('原生面组件 4 键接线（60012→BI2021SA000055 + call 记录 _cur_url_code）',
       "'BI2021SA000055'" in _code and 'self._cur_url_code = url_code' in _code
       and 'componentID=' in _code, '')
    ck('原生面 session.json 优先不被常量覆盖 + 真实属地进信封',
       'not self.native.cookie' in _code
       and '_session_city or self.cfg.cmcc_city_code' in _code, '')
    ck('coc3 wasmParams 自动收割 + 仅提交端点带头（h_submit 分离）',
       hasattr(Runner, '_harvest_wasm_params') and "'h_submit'" in _code
       and "h_submit['wasmParams'] = wpx" in _code
       and 'exp_coc3_wasm_harvest' in _code, '')
    ck('139 数美 deviceId 自动收割在位（_harvest_sm_device_id + exp_mcloud_deviceid）',
       hasattr(Runner, '_harvest_sm_device_id')
       and 'exp_mcloud_deviceid' in _code
       and '_harvest_sm_device_id()' in _code, '')
    ck('sweep 全球通现签（_cmic_fresh_cookie 优先，配置兜底）',
       'self._cmic_fresh_cookie() or self.cfg.cmcc_cmic_cookie' in _code, '')
    ck('sweep coc3 强换票（force=True 防 -516）',
       'self.coc3_ensure(force=True)' in _code, '')
    ck('周二充值日抽奖在位（init/click/share2/prizeList + 河南免登 + 有界停手）',
       hasattr(Runner, 'task_henan_rechargeday')
       and '/tuesDayJt/click' in _code and '/tuesDayJt/share2' in _code
       and '/tuesDayJt/prizeList' in _code and "num')) == '0'" in _code, '')
    ck('AI豆兑换在位（gold：三端点 + 顶格档序，0.5元/加赠券不兑）',
       hasattr(Runner, 'task_gold') and hasattr(Runner, '_gold_pick')
       and GOLD_TIERS[0] == '5元话费券' and GOLD_TIERS[-1] == '1元话费券'
       and '0.5元话费券' not in GOLD_TIERS
       and '/qwhdhub/api/mark/task/exchange' in _code, '')
    ck('gold 选档逻辑离线可测（抢光跳过→次档 / USED 跳过 / 余额不足→None）',
       Runner._gold_pick(Runner, [
           {'name': '5元话费券', 'status': 'WITHOUT', 'stock': 5, 'cost': 500, 'id': 1},
           {'name': '2元话费券', 'status': 'WITHOUT', 'stock': 500, 'cost': 200, 'id': 2},
           {'name': '1元话费券', 'status': 'USED', 'stock': 500, 'cost': 100, 'id': 3}],
           250.0).get('id') == 2
       and Runner._gold_pick(Runner, [], 999) is None
       and Runner._gold_pick(Runner, [
           {'name': '2元话费券', 'status': 'WITHOUT', 'stock': 500, 'cost': 200, 'id': 2}],
           150.0) is None, '')
    ck('AI豆苗在位（seed：slgCredits 三端点 + 签到 3115 + 复用免扫码现签）',
       hasattr(Runner, 'task_seed')
       and 'getSlgCreditsHomePage' in _code and 'getGrowthBubble' in _code
       and 'accumulateSign' in _code and SEED_SIGN_TASK_ID == 3115
       and 'CMCC_COC3_TICKET' not in DEFAULTS, '')
    ck('周三充值日闭环在位（wed：黑名单制 + 抽光 + 集齐领取映射）',
       hasattr(Runner, 'task_wed') and 'drawPicture' in _code and 'receiveNew' in _code
       and 'getJtToken' in _code and 'yqyh' in WED_MANUAL_SKIP
       and WED_PUZZLE_RECEIVE == {1: 'r6', 2: 'r5', 3: 'r4'}, '')
    ck('追剧视频任务在位（videoday：cmlive 017027 现签 + 完成上报 + 余量核实）',
       hasattr(Runner, 'task_videoday') and CMLIVE_SOURCE_ID == '017027'
       and 'cmpassLoginByCmToken' in _code and 'fiveMinuteTaskFinish' in _code
       and '_fresh_ext(CMLIVE_SOURCE_ID)' in _code, '')
    ck('辽宁领券默认关（lnlottery：DEFAULTS=0 + goldeneggs 1025093012 + DO_TASK 分支）',
       hasattr(Runner, 'task_lnlottery')
       and DEFAULTS['CMCC_LNLOTTERY'] == '0'
       and '1025093012' in LN_ENTRY and 'lotterySafely' in _code
       and 'DO_TASK' in _code, '')
    ck('话费余额在位（fee：原生 20016/20008 路径 + 组件键）',
       hasattr(Runner, 'task_fee')
       and Native.PATHS.get('20016', '').endswith('realFeeQuery/getRealFee')
       and Native.PATHS.get('20008', '').endswith('payFeesQuery/getPayFeesQueryInfo')
       and '20008' in N_COMPONENTS, '')
    ck('增量任务已注册调度（main 六键 + 金兑换开关默认开）',
       all(k in _code for k in (
           "('gold', r.task_gold)", "('seed', r.task_seed)", "('wed', r.task_wed)",
           "('videoday', r.task_videoday)", "('lnlottery', r.task_lnlottery)",
           "('fee', r.task_fee)"))
       and DEFAULTS['CMCC_GOLD_EXCHANGE'] == '1', '')
    _old_total = "v.get('totalNum'" + ', 0)'
    ck('coc3 任务进度行 null 安全（completedNum/totalNum=null 不再 TypeError）',
       "(v.get('completedNum') or 0)" in _code
       and "(v.get('totalNum') or 0)" in _code
       and _old_total not in _code, '')
    ck('免短信自续期（60012 默认开 + 脚本本地票据缓存读写 + 三级候选取新）',
       'self._post_login(url, self.jwt, self.cfg.cmcc_app_token)' in _code
       and "'CMCC_AUTO_RENEW': '1'" in _code
       and '.cmcc_cookie_' in _code and 'self.sso.renew_cookie' in _code, '')
    ck('60012 续期闭环（log 先于 Sso 初始化 + 整串 Set-Cookie 吸收 + body 同主项目）',
       'self.log = []' in _code.split('def note')[0]
       and "_raw_sc = (r.get('headers') or {}).get('set-cookie')" in _code
       and "'sendSmsFlag': '0'" in _code, '')
    ck('面板态续期（city/xk 持久化进缓存 + DEFAULTS 属地种对 0371 + 自愈失败可见）',
       "'xk': self.xk or ''" in _code
       and "'CMCC_CITY_CODE': '0371'" in _code
       and '60012 自愈失败' in _code, '')
    ck('coc3 系修复（死任务跳过不白提交 / 预检空清单给 resultCode / 累计 null 安全）',
       "acc if acc is not None else '?'" in _code
       and "if (oos or noq) else" in _code
       and '行为对账型' in _code, '')
    bad = [x for x in res if not x[1]]
    for n, o, e in res:
        print('  %s %s%s' % ('[OK]' if o else '[X] ', n, ('  ' + str(e)) if e else ''))
    print('\n离线自检 %d/%d 通过' % (len(res) - len(bad), len(res)))
    return 1 if bad else 0


def main(argv):
    if '--selftest' in argv:
        return selftest()
    cfg = Cfg()
    if cfg.dry_run or ('--dry-run' in argv):
        cfg.dry_run = True
    # 随机启动延迟（防风控）
    try:
        delay = int(float(cfg.cmcc_delay or 0))
    except ValueError:
        delay = 0
    if delay > 0:
        wait = random.uniform(0, delay)
        print('随机延迟 %.0f 秒后开始…' % wait, flush=True)
        time.sleep(wait)

    print('=' * 64)
    print('中国移动·活动日常任务（青龙版） 账号=%s 省=%s %s'
          % (cfg.cmcc_phone, cfg.cmcc_province_code, '【演练】' if cfg.dry_run else '【正式】'))
    print('HTTP 后端：%s | JWT 缓存：%s' % (Http().backend,
                                            '有' if load_jwt(cfg.cmcc_phone) else '无（将用 App 票据引导）'))
    print('=' * 64)

    r = Runner(cfg)

    def safe(name, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            r.note(name, '异常：%s' % str(e)[:120])

    for key, fn in (('mark31', r.task_mark31),
                    ('saturday', lambda: r._task_sign_act('saturday')),
                    ('video', lambda: r._task_sign_act('video')),
                    ('aitasks', r.task_aitasks),
                    ('coins', r.task_coins),
                    ('lottery', r.task_lottery),
                    ('assess', r.task_assess),
                    ('seckill', r.task_seckill),
                    ('hlw', r.task_hlw),
                    ('coc3', r.task_coc3),
                    ('coc3draw', r.task_coc3_draw),
                    ('coc7', r.task_coc7),
                    ('migu', r.task_migu),
                    ('henan', r.task_henan),
                    ('henan_sign', r.task_henan_sign),
                    ('henan_rechargeday', r.task_henan_rechargeday),
                    ('cmic', r.task_cmic),
                    ('native', r.task_native),
                    ('discover', r.task_discover),
                    ('mcloud', r.task_mcloud),
                    ('139sign', r.task_139sign),
                    ('mcloudacts', r.task_mcloud_acts),
                    ('springgift', r.task_springgift),
                    ('huiyuanri', r.task_huiyuanri),
                    ('agegrowth', r.task_agegrowth),
                    ('wallet', r.task_wallet),
                    ('lxrp', r.task_lxrp),
                    ('qwhd_complete', r.task_qwhd_complete),
                    ('coc3_complete', r.task_coc3_complete),
                    ('coc3_scan', r.task_coc3_scan),
                    ('native_complete', r.task_native_complete),
                    ('hlw_complete', r.task_hlw_complete),
                    ('mcloud_complete', r.task_mcloud_complete),
                    ('native_rights', r.task_native_rights),
                    ('coc3_daily_checkin', r.task_coc3_daily_checkin),
                    ('qwhd_readall', r.task_qwhd_readall),
                    ('qwhd_team', r.task_qwhd_team),
                    ('coc_web6_full', r.task_coc_web6_full),
                    ('gold', r.task_gold),
                    ('seed', r.task_seed),
                    ('wed', r.task_wed),
                    ('videoday', r.task_videoday),
                    ('lnlottery', r.task_lnlottery),
                    ('fee', r.task_fee),
                    ('sweep', r.task_sweep)):
        if cfg.want(key):
            safe(key, fn)

    summary = '\n'.join(r.log)
    print('\n' + '=' * 64 + '\n汇总\n' + '=' * 64 + '\n' + summary)
    if cfg.notify_on:
        ok = notify('移动任务 %s%s' % (cfg.cmcc_phone, '（演练）' if cfg.dry_run else ''), summary)
        print('\n通知推送：%s' % ('已发送' if ok else '未配置 notify（忽略）'))
    return 0


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main(sys.argv[1:]))
