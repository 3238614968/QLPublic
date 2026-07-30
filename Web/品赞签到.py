"""
品赞HTTP代理签到脚本
变量名: pzhttp
格式: 账号#密码，多账号用换行隔开
例如:
13800138000#MyPass123
"""

import json
import os
import random
import string
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

LOGIN_URL = "https://service.ipzan.com/users-login"
SIGNIN_URL = "https://service.ipzan.com/home/userWallet-receive"
ACCOUNT_ENV = "pzhttp"
OBFUSCATION_SALT = "QWERIPZAN1290QWER"
REQUEST_TIMEOUT = 10


class Base64Obfuscator:
    def __init__(self):
        self.table = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz"
            "0123456789+/"
        )

    def utf16_to_utf8(self, text: str) -> str:
        result = []
        for char in text:
            code = ord(char)
            if 0 < code <= 127:
                result.append(char)
            elif 128 <= code <= 2047:
                byte1 = chr(192 | (code >> 6) & 31)
                byte2 = chr(128 | code & 63)
                result.extend([byte1, byte2])
            elif 2048 <= code <= 65535:
                byte1 = chr(224 | (code >> 12) & 15)
                byte2 = chr(128 | (code >> 6) & 63)
                byte3 = chr(128 | code & 63)
                result.extend([byte1, byte2, byte3])
        return "".join(result)

    def encode(self, text: str) -> str:
        if not text:
            return ""

        utf8_bytes = self.utf16_to_utf8(text)
        index = 0
        result = []
        length = len(utf8_bytes)

        while index < length:
            first = ord(utf8_bytes[index])
            index += 1
            result.append(self.table[first >> 2])

            if index == length:
                result.append(self.table[(first & 3) << 4])
                result.append("==")
                break

            second = ord(utf8_bytes[index])
            index += 1
            if index == length:
                result.append(self.table[(first & 3) << 4 | (second >> 4) & 15])
                result.append(self.table[(second & 15) << 2])
                result.append("=")
                break

            third = ord(utf8_bytes[index])
            index += 1
            result.append(self.table[(first & 3) << 4 | (second >> 4) & 15])
            result.append(self.table[(second & 15) << 2 | (third & 192) >> 6])
            result.append(self.table[third & 63])

        return "".join(result)


@dataclass
class Account:
    phone: str
    password: str


def build_common_headers(token: Optional[str] = None) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "Origin": "https://www.ipzan.com",
        "Referer": "https://www.ipzan.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Cookie": "locale=en-us",
    }

    if token is None:
        headers["Authorization"] = "Bearer null"
        headers["Content-Type"] = "application/json;charset=UTF-8"
    else:
        headers["Authorization"] = f"Bearer {token}"

    return headers


def generate_noise_hex(length: int = 400) -> str:
    hex_chars = string.hexdigits.lower()
    return "".join(random.choice(hex_chars) for _ in range(length))


def generate_obfuscated_account(phone: str, password: str) -> str:
    concat = phone + OBFUSCATION_SALT + password
    encoded = Base64Obfuscator().encode(concat)

    noise = generate_noise_hex(400)
    # 与旧实现保持一致：将编码字符串分段插入到固定位置。
    return (
        noise[:100]
        + encoded[:8]
        + noise[100:200]
        + encoded[8:20]
        + noise[200:300]
        + encoded[20:]
        + noise[300:400]
    )


def login(phone: str, password: str) -> Optional[str]:
    payload = {
        "account": generate_obfuscated_account(phone, password),
        "source": "ipzan-home-one",
    }
    headers = build_common_headers(token=None)

    try:
        response = requests.post(
            LOGIN_URL,
            data=json.dumps(payload),
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        print(f"登录请求异常: {exc}")
        return None
    except ValueError:
        print("登录响应非JSON格式")
        return None

    if data.get("code") == 0:
        return data.get("data", {}).get("token")

    print(f"登录失败: {data.get('message', '未知错误')}")
    return None


def sign_in(token: str) -> bool:
    headers = build_common_headers(token=token)
    try:
        response = requests.get(SIGNIN_URL, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        print(f"签到请求异常: {exc}")
        return False
    except ValueError:
        print("签到响应非JSON格式")
        return False

    if data.get("code") == 0:
        print("签到成功")
        return True

    print(f"签到失败: {data.get('message', '未知错误')}")
    return False


def parse_accounts(raw_text: str) -> Tuple[List[Account], List[str]]:
    accounts: List[Account] = []
    errors: List[str] = []

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    for idx, line in enumerate(lines, 1):
        if "#" not in line:
            errors.append(f"账号 {idx} 格式错误: {line} (应为 账号#密码)")
            continue

        phone, password = line.split("#", 1)
        phone = phone.strip()
        password = password.strip()

        if not phone or not password:
            errors.append(f"账号 {idx} 格式错误: {line} (账号或密码为空)")
            continue

        accounts.append(Account(phone=phone, password=password))

    return accounts, errors


def run() -> int:
    raw_config = os.environ.get(ACCOUNT_ENV, "").strip()
    if not raw_config:
        print(f"未配置{ACCOUNT_ENV}变量")
        return 1

    accounts, parse_errors = parse_accounts(raw_config)
    for error in parse_errors:
        print(error)

    if not accounts:
        print("没有可用账号，任务结束")
        return 1

    print(f"📱 检测到 {len(accounts)} 个有效账号，开始签到...")
    success_count = 0

    for idx, account in enumerate(accounts, 1):
        print(f"\n--- 账号 {idx}: {account.phone} ---")
        token = login(account.phone, account.password)
        if not token:
            print("跳过签到")
            continue

        if sign_in(token):
            success_count += 1

    print(f"\n🎉 签到流程结束：成功 {success_count}/{len(accounts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
