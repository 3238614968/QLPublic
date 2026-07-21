"""
移动云盘 · 16号会员日(National_MCloudDay) 

功能范围:
1. 盲盒自动抽奖  blindbox/lottery  —— 循环抽到无次数为止
2. 中奖记录查询  getUserPrizeLogPage —— 只读展示本期已抽到的奖品
3. 奖品列表查询  gift/list —— 展示可抢兑商品(prizeType==1 需钻石会员)
4. 自动抢兑      getSmsCode -> receive —— 抢兑所有普通奖品(跳过钻石会员专属)

鉴权说明:
  复用主脚本 移动云盘.py 的环境变量 yunpan, 格式与主脚本一致:
      Authorization值#手机号   (多账号用 & 分隔)
依赖:  pip3 install requests
暂无真实响应，数据未作处理，等待后期更新
一键取CK地址：https://ydyp.apisky.cn/

Author: xiaohai
Update: 2026.07.21
"""

import base64
import json
import os
import random
import re
import time
from pathlib import Path

import requests

SCRIPT_VERSION = '1.0.0'

# Token 缓存文件
JS_CACHE_DIR = 'data'
JS_CACHE_FILE = 'yunpan_token_cache.json'
TOKEN_STORAGE_FILENAME = 'yunpan_token_storage.json'
DEVICE_ID_STORAGE_FILENAME = 'yunpan_device_ids.json'

# 会员日活动固定参数
MARKET_NAME = 'National_MCloudDay'
SOURCE_ID = '1000'
BASE_URL = 'https://caiyun.feixin.10086.cn:7071'
REFERER = (f'{BASE_URL}/portal/cloudCircle/index.html'
           f'?path=mCloudDay&sourceid={SOURCE_ID}&enableShare=1')

# 抽奖循环上限
MAX_LOTTERY_TIMES = 30

UA = ('Mozilla/5.0 (Linux; Android 16; V23049RAD8C Build/TKQ1.221114.001; wv) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/108.0.5359.128 '
      'Mobile Safari/537.36 MCloudApp/12.4.0 AppLanguage/zh-CN')


# ============================ 鉴权链路 ============================

def normalize_authorization(token):
    token = (token or '').strip()
    if token and not token.startswith('Basic '):
        return f'Basic {token}'
    return token


def current_millis():
    return int(time.time() * 1000)


def parse_token_key(authorization):
    try:
        token = normalize_authorization(authorization)
        if not token:
            return {'phone': '', 'expireAt': 0}
        if token.startswith('Basic '):
            token = token[6:]
        decoded = base64.b64decode(token).decode('utf-8')
        if not decoded:
            return {'phone': '', 'expireAt': 0}
        phone = decoded.split(':')[1] if len(decoded.split(':')) > 1 else ''
        pipe_parts = decoded.split('|')
        expire_at = int(pipe_parts[3]) if len(pipe_parts) > 3 and pipe_parts[3].isdigit() else 0
        return {'phone': phone, 'expireAt': expire_at}
    except Exception:
        return {'phone': '', 'expireAt': 0}


def _read_json(cache_path):
    try:
        if cache_path.exists():
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def read_cached_authorization(account):
    """按主脚本 get_token_info 的优先级读取缓存 authorization。"""
    base_dir = Path(os.path.abspath(os.path.dirname(__file__)))

    js_cache = _read_json(Path(os.getcwd()) / JS_CACHE_DIR / JS_CACHE_FILE)
    entry = (js_cache.get('accounts') or {}).get(account, {})
    if entry.get('authorization'):
        return normalize_authorization(entry['authorization']), int(entry.get('expireAt') or 0)

    storage = _read_json(base_dir / TOKEN_STORAGE_FILENAME)
    entry = (storage.get('accounts') or {}).get(account, {})
    if entry.get('token'):
        return normalize_authorization(entry['token']), int(entry.get('expiresAt') or 0)

    old = _read_json(base_dir / DEVICE_ID_STORAGE_FILENAME)
    entry = old.get(account, {})
    if entry.get('token'):
        return normalize_authorization(entry['token']), int(entry.get('expiresAt') or 0)

    return '', 0


def resolve_authorization(account, env_token, log=print):
    env_token = normalize_authorization(env_token)
    cached_token, cached_expire = read_cached_authorization(account)
    if not cached_token:
        return env_token

    env_expire = parse_token_key(env_token).get('expireAt', 0)
    cache_expire = parse_token_key(cached_token).get('expireAt', 0) or cached_expire
    now = current_millis()

    # env 已过期而缓存未过期 -> 用缓存
    if env_expire and env_expire <= now and (not cache_expire or cache_expire > now):
        log('  -Token缓存命中: env已过期, 使用缓存Token')
        return cached_token
    # 缓存过期时间更晚 -> 用更新的缓存
    if cache_expire > env_expire:
        log('  -Token缓存命中: 缓存Token更新')
        return cached_token
    # 双方都解析不出过期时间 -> 保守用缓存
    if not env_expire and not cache_expire:
        log('  -Token缓存命中: env/cache均无过期信息, 使用缓存Token')
        return cached_token
    return env_token


def query_spec_token(session, authorization, account, source_id='001005'):
    url = 'https://orches.yun.139.com/orchestration/auth-rebuild/token/v1.0/querySpecToken'
    headers = {
        'Authorization': authorization,
        'User-Agent': UA,
        'Content-Type': 'application/json',
        'Accept': '*/*',
        'Host': 'orches.yun.139.com',
    }
    payload = {'account': account, 'toSourceId': source_id}
    try:
        resp = session.post(url, headers=headers, json=payload, timeout=15)
        data = resp.json()
    except Exception as e:
        print(f'  获取specToken异常: {e}')
        return None
    if data.get('success'):
        return data['data']['token']
    print(f"  获取specToken失败: {data.get('message', '未知错误')}")
    return None


def fetch_jwt_token(session, authorization, account):
    sso_token = query_spec_token(session, authorization, account)
    if not sso_token:
        return None
    jwt_url = f'{BASE_URL}/portal/auth/tyrzLogin.action?ssoToken={sso_token}'
    jwt_headers = {
        'User-Agent': UA,
        'Accept': '*/*',
        'Host': 'caiyun.feixin.10086.cn:7071',
    }
    try:
        resp = session.post(jwt_url, headers=jwt_headers, timeout=15)
        data = resp.json()
    except Exception as e:
        print(f'  JWT获取异常: {e}')
        return None
    if data.get('code') != 0:
        print(f"  JWT获取失败: {data.get('msg', '未知错误')}")
        return None
    return data['result']['token']


# ============================ 会员日请求封装 ============================

def build_headers(jwt_token, post=False):
    headers = {
        'Host': 'caiyun.feixin.10086.cn:7071',
        'User-Agent': UA,
        'Accept-Encoding': 'gzip, deflate',
        'jwttoken': jwt_token,
        'x-requested-with': 'com.chinamobile.mcloud',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-mode': 'cors',
        'sec-fetch-dest': 'empty',
        'referer': REFERER,
        'accept-language': 'zh,zh-CN;q=0.9,en-US;q=0.8,en;q=0.7',
    }
    if post:
        headers['content-type'] = 'application/json;charset=UTF-8'
        headers['origin'] = BASE_URL
        headers['showloading'] = 'true'
    return headers


def market_request(session, url, jwt_token, method='GET', params=None, payload=None):
    """统一发起会员日请求并返回解析后的 JSON(失败返回 None)"""
    try:
        if method == 'POST':
            resp = session.post(url, headers=build_headers(jwt_token, post=True),
                                data=json.dumps(payload or {}), timeout=20)
        else:
            resp = session.get(url, headers=build_headers(jwt_token),
                               params=params, timeout=20)
        return resp.json()
    except Exception as e:
        print(f'  请求异常 {url}: {e}')
        return None


# ============================ 功能: 抽奖 / 记录 / 奖品 ============================

def is_success(data):
    """通用成功判断: 兼容 code/status/success 多种字段"""
    if not isinstance(data, dict):
        return False
    if data.get('success') is True:
        return True
    for key in ('code', 'status'):
        if key in data:
            return str(data[key]) in ('0', '200')
    return False


def extract_message(data):
    for key in ('msg', 'message', 'respMsg', 'errMsg', 'desc'):
        if isinstance(data, dict) and data.get(key):
            return str(data[key])
    return ''


def do_lottery(session, jwt_token, logs):
    """盲盒抽奖: 循环抽到无次数为止。首次打印原始响应以便核对字段。"""
    url = f'{BASE_URL}/ycloud/mcloudday/blindbox/lottery'
    logs.append('\n🎁 盲盒抽奖')
    win_count = 0
    for i in range(1, MAX_LOTTERY_TIMES + 1):
        data = market_request(session, url, jwt_token, method='POST', payload={'client': '1'})
        if data is None:
            logs.append(f'-第{i}次抽奖: 接口无响应, 停止')
            break
        if i == 1:
            # 响应结构未知, 首次完整打印(仅控制台, 不进推送日志)
            print(f'  [首次抽奖原始响应] {json.dumps(data, ensure_ascii=False)}')
        if is_success(data):
            result = data.get('result') or data.get('data') or {}
            prize_name = ''
            if isinstance(result, dict):
                prize_name = (result.get('prizeName') or result.get('giftName')
                              or result.get('name') or result.get('desc') or '')
            logs.append(f'-第{i}次: {prize_name or "抽奖成功"}')
            win_count += 1
            time.sleep(random.uniform(1.0, 2.0))
            continue
        # 非成功: 大概率是次数用尽, 停止循环
        msg = extract_message(data) or '无更多次数'
        logs.append(f'-第{i}次: 停止({msg})')
        break
    logs.append(f'-累计抽奖 {win_count} 次')


def query_prize_log(session, jwt_token, logs):
    """中奖记录(只读)"""
    url = f'{BASE_URL}/market/prizeApi/checkPrize/getUserPrizeLogPage'
    params = {'marketName': MARKET_NAME, 'currPage': '1', 'pageSize': '1000'}
    data = market_request(session, url, jwt_token, method='GET', params=params)
    logs.append('\n📜 中奖记录')
    if not data or not is_success(data):
        logs.append(f'-查询失败: {extract_message(data) if data else "接口无响应"}')
        return
    result = data.get('result') or data.get('data') or {}
    records = []
    if isinstance(result, dict):
        records = result.get('list') or result.get('records') or result.get('items') or []
    elif isinstance(result, list):
        records = result
    if not records:
        logs.append('-暂无中奖记录')
        return
    for rec in records:
        if not isinstance(rec, dict):
            continue
        name = rec.get('prizeName') or rec.get('giftName') or rec.get('name') or '未知奖品'
        t = rec.get('createTime') or rec.get('time') or rec.get('drawTime') or ''
        logs.append(f'-{name} {t}'.rstrip())


def fetch_gift_list(session, jwt_token):
    """取奖品列表原始数据(纯数据, 不打印); 失败返回 (None, 错误信息)"""
    url = f'{BASE_URL}/ycloud/mcloudday/gift/list'
    data = market_request(session, url, jwt_token, method='GET')
    if not data or not is_success(data):
        return None, (extract_message(data) if data else '接口无响应')
    result = data.get('result') or data.get('data') or []
    gifts = result if isinstance(result, list) else (result.get('list') or result.get('gifts') or [])
    return gifts, ''


def gift_fields(g):
    """从单个奖品字典提取标准字段: (名称, prizeId, 是否需钻石会员)"""
    name = g.get('giftName') or g.get('prizeName') or g.get('name') or '未知'
    pid = g.get('prizeId') or g.get('id') or ''
    need_vip = str(g.get('prizeType', '')) == '1'
    return name, pid, need_vip


def log_gift_list(gifts, logs):
    """展示奖品列表(只读); prizeType==1 表示需钻石会员"""
    logs.append('\n🛒 可抢兑奖品列表')
    if gifts is None:
        logs.append('-查询失败')
        return
    if not gifts:
        logs.append('-暂无可抢兑奖品')
        return
    for g in gifts:
        if not isinstance(g, dict):
            continue
        name, pid, need_vip = gift_fields(g)
        logs.append(f'-[{"钻石会员" if need_vip else "普通"}] {name} (prizeId={pid})')


def redeem_gifts(session, jwt_token, gifts, logs, confirm):
    """抢兑所有普通奖品(prizeType != 1)。
    confirm=False 为测试模式, 只打印将抢兑的目标, 不真正提交。
    验证码自动提取: getSmsCode 响应中的 md5 直接喂给 receive。
    """
    logs.append('\n🎯 抢兑' + ('(测试模式, 未提交)' if not confirm else '(真实提交)'))
    if not gifts:
        logs.append('-无奖品可抢兑')
        return
    targets = []
    for g in gifts:
        if not isinstance(g, dict):
            continue
        _, pid, need_vip = gift_fields(g)
        if need_vip or not pid:
            continue
        targets.append(g)
    if not targets:
        logs.append('-无普通奖品(仅钻石会员专属或缺少prizeId)')
        return
    for g in targets:
        name, pid, _ = gift_fields(g)
        if not confirm:
            logs.append(f'-将抢兑: {name} (prizeId={pid})')
            continue
        redeem_one_gift(session, jwt_token, pid, name, logs)
        time.sleep(random.uniform(1.5, 3.0))


def get_sms_code(session, jwt_token, prize_id):
    """获取抢兑验证码(md5); 返回验证码字符串或 None"""
    url = f'{BASE_URL}/ycloud/mcloudday/gift/getSmsCode'
    data = market_request(session, url, jwt_token, method='POST', payload={'prizeId': prize_id})
    if not data:
        return None, '接口无响应'
    if not is_success(data):
        return None, extract_message(data) or '获取验证码失败'
    result = data.get('result') or data.get('data')
    if isinstance(result, str) and result:
        return result, ''
    if isinstance(result, dict):
        code = result.get('smsCode') or result.get('code') or result.get('msgCode')
        if code:
            return str(code), ''
    for key in ('smsCode', 'msgCode'):
        if data.get(key):
            return str(data[key]), ''
    return None, f'验证码字段未识别: {json.dumps(data, ensure_ascii=False)}'


def redeem_one_gift(session, jwt_token, prize_id, name, logs):
    """单个奖品抢兑: getSmsCode -> receive"""
    sms_code, err = get_sms_code(session, jwt_token, prize_id)
    if not sms_code:
        logs.append(f'-{name}: 取验证码失败({err})')
        return
    time.sleep(random.uniform(0.8, 1.5))
    url = f'{BASE_URL}/ycloud/mcloudday/gift/receive'
    data = market_request(session, url, jwt_token, method='POST',
                          payload={'prizeId': prize_id, 'smsCode': sms_code})
    if not data:
        logs.append(f'-{name}: 抢兑接口无响应')
        return
    if is_success(data):
        logs.append(f'-{name}: ✅抢兑成功')
    else:
        logs.append(f'-{name}: 抢兑失败({extract_message(data) or "未知"})')


# ============================ 账号编排 ============================

def run_account(index, account_info, confirm):
    """单账号执行: 鉴权 -> 抽奖 -> 记录 -> 奖品列表(展示+抢兑)"""
    logs = []
    parts = account_info.split('#')
    if len(parts) < 2:
        print(f'⚠️ 第{index}个账号格式错误(需 Authorization#手机号): {account_info}')
        return None, None
    account = parts[1].strip()
    masked = account[:3] + '****' + account[-4:] if len(account) >= 11 else account

    print(f'\n======== ▷ 第 {index} 个账号 [{masked}] ◁ ========')
    authorization = resolve_authorization(account, parts[0], log=print)
    session = requests.Session()

    jwt_token = fetch_jwt_token(session, authorization, account)
    if not jwt_token:
        print('  ⛔️ 鉴权失败, ck可能已失效, 跳过')
        return masked, None

    logs.append(f'账号: {masked}')
    do_lottery(session, jwt_token, logs)
    query_prize_log(session, jwt_token, logs)

    gifts, err = fetch_gift_list(session, jwt_token)
    if gifts is None:
        logs.append(f'\n🛒 可抢兑奖品列表\n-查询失败: {err}')
    else:
        log_gift_list(gifts, logs)
        redeem_gifts(session, jwt_token, gifts, logs, confirm)

    log_text = '\n'.join(logs)
    print(log_text)
    return masked, log_text


def load_send():
    """兼容青龙 notify.py 推送, 未安装则返回 None"""
    try:
        from notify import send
        return send
    except Exception:
        return None


if __name__ == '__main__':
    print(f'移动云盘·16号会员日 v{SCRIPT_VERSION}')
    env_value = os.getenv('yunpan')
    if not env_value:
        print('⛔️ 未获取到变量 yunpan, 请检查环境变量(格式: Authorization#手机号, 多账号 & 分隔)')
        raise SystemExit(0)

    accounts = re.split(r'[&]', env_value)
    print(f'共 {len(accounts)} 个账号')

    # 抢兑安全开关: 默认演练(只打印将抢兑的奖品), mcloudday_confirm=1 才真正提交
    confirm = str(os.getenv('mcloudday_confirm', '')).strip() in ('1', 'true', 'True')
    print(f'抢兑模式: {"真实提交" if confirm else "测试模式(仅打印, 设 mcloudday_confirm=1 启用真实抢兑)"}')

    all_logs = ''
    err_accounts = ''
    for i, info in enumerate(accounts, start=1):
        info = info.strip()
        if not info:
            continue
        masked, log_text = run_account(i, info, confirm)
        if log_text:
            all_logs += f'{log_text}\n\n'
        elif masked:
            err_accounts += f'{masked}\n'
        time.sleep(random.uniform(1, 3))

    msg = ''
    if err_accounts:
        msg += f'失效账号:\n{err_accounts}\n'
    msg += f'任务详情:\n{all_logs}'

    print('\n================ 运行总结 ================')
    if err_accounts:
        print(f'❌ 失效账号:\n{err_accounts}')

    send = load_send()
    if send:
        send('移动云盘·16号会员日', msg)
