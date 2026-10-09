# QLPublic

面向青龙面板和本地运行的 Python / Node.js 脚本集合，包含 APP 签到、积分任务、网站签到及验证码辅助模块。各脚本独立配置、独立运行，功能是否可用取决于第三方接口、账号状态和活动规则。

本文按仓库当前代码整理（2026-10-09）。仓库共包含 10 个 Python 脚本、3 个 JavaScript 业务脚本和 2 个 JavaScript 辅助模块；没有统一启动入口。

## 脚本目录

### APP

| 脚本 | 主要功能 | 账号环境变量 |
| --- | --- | --- |
| [ZEEHO_极核.py](APP/ZEEHO_极核.py) | 签到、发帖、评论、点赞、分享 | `ZEEHO_TOKEN` |
| [vivo积分任务.py](APP/vivo积分任务.py) | 多业务签到、任务中心、社区互动、商城活动及会员领券 | `vivo_app` |
| [安心记加班.py](APP/安心记加班.py) | 签到、视频、资讯、抽奖、打卡、领奖及红包兑换 | `axjjb` |
| [小时工计帐.py](APP/小时工计帐.py) | 签到、视频、商城浏览、抽奖、领奖及红包兑换 | `xsgjz` |
| [移动云盘.py](APP/移动云盘.py) | 签到、云朵任务、上传 / 分享、抽奖及红包派对 | `yunpan` |
| [移动云盘商品抢兑.py](APP/移动云盘商品抢兑.py) | 查询商品和已有商品，按指定商品 ID 兑换 | `yunpan` |
| [移动16号会员日.py](APP/移动16号会员日.py) | 会员日盲盒抽奖、中奖记录、商品查询及兑换 | `yunpan` |
| [追书神器免费版-狂爆版.py](APP/追书神器免费版-狂爆版.py) | 循环执行金币任务 | `zssq` |

### Web 与辅助模块

| 脚本 | 用途 | 配置入口 |
| --- | --- | --- |
| [品赞签到.py](Web/品赞签到.py) | 品赞 HTTP 代理签到 | `pzhttp` |
| [臻讯云纯算版.js](Web/臻讯云纯算版.js) | 臻讯云登录、签到及已完成任务领奖，本地处理验证码 | `zhenxun` 或 `zhenxun_*` |
| [zhenxun_2captcha.js](Web/zhenxun_2captcha.js) | 臻讯云签到，使用第三方 2Captcha 服务 | `zhenxun`、`TWOCAPTCHA_KEY` |
| [雨云点选v1版.js](Web/雨云点选v1版.js) | 雨云登录、签到及点选验证码处理 | `RAINYUN_ACCOUNTS`，兼容 `rainyun` |
| [基元律动.py](Web/基元律动.py) | 接码注册、测试对话、余额查询及本地保存 API Key | 文件顶部配置常量，见下文 |
| [txcaptcha_slide.js](Tcaptcha/txcaptcha_slide.js) | 验证码与登录辅助模块，导出 `TxCaptcha`、`encryptPassword`、`login` | 调用参数或命令行 `solve` / `login` |
| [click_select_solver_v2.js](Tcaptcha/click_select_solver_v2.js) | 点选验证码辅助模块，导出 `ClickSelectCaptchaSolverV2` | 调用方传入参数 |

`Tcaptcha/` 下的两个文件供代码调用，不需要单独添加为签到定时任务。臻讯云的两个版本建议择一使用，避免重复执行同一账号任务。

`txcaptcha_slide.js` 也提供命令行入口，例如 `node 'Tcaptcha/txcaptcha_slide.js' solve`。其用法提示中的 `txcaptcha_algo.js` 是旧文件名，应替换为当前实际路径；调用 `login` 成功时会在工作目录写入 `jwt_token.json`。

## 运行环境与依赖

建议使用 Python 3.10 或更高版本，以及满足依赖包 `engines` 要求的 Node.js LTS 版本。本次离线检查环境为 Python 3.12、Node.js 24。

仓库目前没有 `requirements.txt`、`package.json` 或依赖锁文件。依赖按实际导入整理如下；只运行个别脚本时，可只安装对应项。

| 依赖 | 使用范围 |
| --- | --- |
| Python：`requests` | 全部 Python 脚本 |
| Python：`pycryptodome` | 移动云盘、移动云盘商品抢兑、追书神器；vivo 部分商城加密功能可选 |
| Python：`httpx` | 小时工计帐 |
| Python：`urllib3` | 安心记加班直接导入，通常随 `requests` 安装 |
| Node.js：`axios` | 两个臻讯云版本、`txcaptcha_slide.js` |
| Node.js：`jsdom`、`sharp` | 臻讯云纯算版、雨云、两个 `Tcaptcha/` 模块 |

`Crypto` 由 `pycryptodome` 提供。`crypto`、`fs`、`path`、`https`、`zlib` 等为 Node.js 内置模块，无需另外安装；当前代码不要求安装 `canvas`。

### 青龙面板

1. 在「订阅管理」添加公开仓库：`https://github.com/3238614968/QLPublic.git`，分支 `main`，拉取后确认脚本所在目录。
2. 在「依赖管理」的 Python3 分类按需安装 `requests`、`pycryptodome`、`httpx`；NodeJS 分类按需安装 `axios`、`jsdom`、`sharp`。
3. 在「环境变量」中添加下表对应变量。名称区分大小写，变量值填账号内容，不包含 `变量名=`。
4. 按需创建或启用定时任务，命令使用脚本在面板中的实际路径，先手动运行单个账号确认配置与日志。

拉取目录可能带仓库前缀，任务命令应以面板实际路径为准。不要自动启用全部文件：辅助模块无需定时运行，注册和兑换脚本需要单独检查配置与行为。

### 本地运行

```bash
git clone https://github.com/3238614968/QLPublic.git
cd QLPublic

python3 -m venv .venv
source .venv/bin/activate
python -m pip install requests pycryptodome httpx

# 需要运行 JavaScript 脚本时安装
npm install --no-save axios jsdom sharp
```

以下示例在仓库根目录执行，账号内容均为占位符，需要替换为自己的有效凭据：

```bash
export vivo_app='YOUR_OPENID#YOUR_VIVOTOKEN'
python 'APP/vivo积分任务.py'

export RAINYUN_ACCOUNTS='YOUR_USERNAME#YOUR_PASSWORD'
node 'Web/雨云点选v1版.js'
```

`vivo` 使用真实文件名 `APP/vivo积分任务.py`，文件注释中的 `vivo_app.py` 不在当前仓库中。Windows 用户应使用对应的虚拟环境激活与环境变量设置命令。

## 账号配置

各脚本的字段顺序、分隔符不同，请按表配置。多账号建议放在一个变量值内，使用对应脚本明确支持的分隔符；面板对多个同名变量的注入方式可能随版本变化。

| 变量名 | 单账号格式 | 多账号分隔符 |
| --- | --- | --- |
| `ZEEHO_TOKEN` | `Bearer ACCESS_TOKEN`；也支持带刷新信息的格式，见下文 | `@` 或换行 |
| `vivo_app` | `openid#vivotoken#imei#oaid#unionid#phone`，至少填写前两项 | `@` 或换行 |
| `axjjb` | `token`、`token#android_id` 或 `device_id#android_id#token` | `@` 或换行 |
| `xsgjz` | `token` 或 `device_id#android_id#token` | `@` |
| `zssq` | `token`、`token#android_id` 或 `device_id#android_id#token` | `@` 或换行 |
| `yunpan` | `Authorization值#手机号`；商品抢兑可追加 `#商品ID1,商品ID2` | 三个云盘脚本共用时使用 `&` |
| `pzhttp` | `账号#密码` | 换行 |
| `zhenxun` / `zhenxun_*` | `账号#密码` | `@` 或换行，同一变量值内选一种；也可用 `zhenxun_2` 等独立变量 |
| `RAINYUN_ACCOUNTS` | `账号#密码` | `@` 或换行 |

例如，云盘多账号值为 `AUTHORIZATION_1#PHONE_1&AUTHORIZATION_2#PHONE_2`。商品抢兑额外支持 `@` 和换行，但主脚本及会员日脚本只按 `&` 切分。

品赞只按换行分隔账号；小时工只按 `@` 分隔单个变量中的账号。臻讯云密码目前不能包含 `#`，`@` 会被用作账号分隔符，且不要在同一变量值中混用 `@` 和换行。

### ZEEHO

支持以下单账号形式；`expires_at` 为 Unix 时间戳，单位秒：

```text
Bearer ACCESS_TOKEN
Bearer ACCESS_TOKEN|REFRESH_TOKEN|EXPIRES_AT
{"access_token":"ACCESS_TOKEN","refresh_token":"REFRESH_TOKEN","expires_at":EXPIRES_AT}
```

JSON 示例中的 `EXPIRES_AT` 应替换为数字。刷新结果仅供本次运行使用，当前代码不会自动回写环境变量或持久化新 token。该脚本会自动执行发帖、评论等社区操作。

### vivo

前两项 `openid`、`vivotoken` 必填；后续字段可省略，补充字段时保持位置，例如 `openid#vivotoken###unionid#phone`。也支持 `openid=YOUR_OPENID&vivotoken=YOUR_TOKEN` 等命名字段形式；这里的 `&` 是字段分隔符。

| 可选变量 | 默认值 / 作用 |
| --- | --- |
| `VIVO_GAP` | `0.6`，请求最小间隔秒，填写有效非负数值 |
| `VIVO_TASKS` | 默认启用任务中心；设为 `0` 关闭任务中心，其他签到和活动流程仍会执行 |
| `VIVO_PKGS` | 逗号分隔的客户端包名；未配置时使用代码中的默认身份列表 |

`VIVO_TASKS=0` 不会关闭商城抽奖等活动，也不是只读模式。

### 移动云盘、商品抢兑与会员日

三个脚本共用 `yunpan`。Authorization 使用自己的有效请求头值；主脚本与会员日会补齐 `Basic ` 前缀，商品抢兑脚本应填写完整请求头值。

| 可选变量 | 使用范围与作用 |
| --- | --- |
| `yunpan_device_id` / `YDYP_DEVICE_ID` | 主脚本优先使用的 deviceId |
| `yunpan_storage_dir` / `YDYP_STORAGE_DIR` | 主脚本 deviceId 缓存目录，默认脚本所在目录；不改变工作目录下的 token 缓存路径 |
| `solve_api` | 商品抢兑脚本的外部设备信息服务地址，可覆盖代码中的默认地址 |
| `mcloudday_confirm` | 会员日兑换开关；`1`、`true`、`True` 启用真实兑换 |

- **商品抢兑**：未填写第三段商品 ID 时查询列表和已有商品；填写商品 ID 后会尝试实际兑换，没有会员日的确认开关。
- **会员日**：默认只展示将兑换的目标；盲盒抽奖仍会真实执行。开启兑换后会尝试所有符合条件的普通、有库存奖品，不按 `yunpan` 第三段的商品 ID 筛选。
- **缓存共享**：主脚本会保存并读取 token；会员日会尝试读取相关缓存。商品抢兑当前没有接入主脚本的 token 缓存，需保证环境变量里的 Authorization 有效。
- **执行方式**：当前三个脚本均逐账号顺序执行，文件头部的“多账号并发”描述不适用于当前实现。

### 青龙 OpenAPI（可选）

安心记加班、小时工计帐、追书神器支持通过 OpenAPI 读取多个同名账号变量。配置以下变量后优先使用 API，读取失败或没有值时回退到进程环境变量：

| 变量 | 说明 |
| --- | --- |
| `QL_API_URL` | 青龙地址，默认 `http://127.0.0.1:5700` |
| `QL_CLIENT_ID` | 青龙应用 Client ID |
| `QL_CLIENT_SECRET` | 青龙应用 Client Secret |

应用需要读取环境变量的权限。本地运行可不配置；跨容器部署时应填写实际可达的青龙地址。

当前 API 读取逻辑只筛选变量名，没有过滤停用状态，停用变量可能仍被读取。依赖面板停用账号时，应先修复这一点，或保持 OpenAPI 未配置并明确控制注入的账号值。

### 代理与注册脚本

- 臻讯云两个版本支持 `ZHENXUN_PROXY`，可填写 `http://IP:PORT`；部分代理提取 API 地址也会被识别。
- 小时工读取 `PROXY_API_URL`，但当前 `ProxyManager` 未接入 HTTP 客户端，设置该变量不能保证请求通过该代理。
- 安心记加班、追书神器和 `txcaptcha_slide.js` 的代理地址为代码常量，当前没有读取同名环境变量。
- 基元律动使用文件顶部的 `HZ_USER`、`HZ_PASS`、`HZ_SID`、`INVITE_CODES`、`AUTO_PHONE`、`PROXY_ENABLED`、`IPZAN_URL` 等常量，不从账号环境变量加载。脚本包含接码、注册和测试对话，可能产生费用；失败时会进入手动输入流程，不适合直接作为无人值守签到任务启用。

基元律动源码当前含有代理提取链接的密钥参数。实际使用前应更换为自己的配置；公开过的有效密钥应在服务端轮换，不要继续复用。

## 定时任务建议

以下为标准五字段 Cron（分、时、日、月、星期），执行时间以青龙 / 定时器配置的时区为准。按需选择任务并避免重复运行重叠。

| 场景 | Cron 示例 | 说明 |
| --- | --- | --- |
| 普通每日任务 | `0 8 * * *` | 每天 08:00 |
| 云盘每日多次任务 | `0 8,16,20 * * *` | 每天 08:00、16:00、20:00 |
| 商品抢兑 | `0 0,10,16 * * *` | 每天 00:00、10:00、16:00，具体以商品开放时间为准 |
| 16 号会员日 | `0 8 16 * *` | 每月 16 日 08:00，具体以活动规则为准 |

小时字段范围为 `0–23`，午夜写 `0`；商品抢兑脚本头部的 `24` 不应直接使用。部分脚本注释采用六字段格式，使用前应确认定时器是否支持秒字段。

本地命令示例（仓库根目录运行）：

```bash
python3 'APP/移动云盘.py'
python3 'APP/移动云盘商品抢兑.py'
python3 'APP/移动16号会员日.py'
node 'Web/臻讯云纯算版.js'
```

## 通知与缓存

仓库没有附带 `notify.py` 或 `sendNotify.js`，如需推送，需自行配置兼容的通知模块及其凭据。

| 脚本 | 通知加载方式 |
| --- | --- |
| 移动云盘、商品抢兑 | 检查脚本同目录的 `notify.py`，导入 `send` |
| 会员日、ZEEHO | 从 Python 模块搜索路径导入 `notify.send` |
| 两个臻讯云版本 | 从脚本同目录导入 `./sendNotify.js` |
| 雨云 | 优先检查 `${QL_DIR 或 /ql/data}/scripts/sendNotify.js`，再尝试同目录模块 |

其他脚本当前没有接入上述通知推送。缺少通知模块时，可通过任务日志查看结果。

运行目录会影响缓存位置：

| 缓存 | 位置 / 内容 |
| --- | --- |
| `yunpan_device_ids.json` | 主脚本目录或指定存储目录，保存账号与设备信息 |
| `data/yunpan_token_cache.json` | 运行工作目录下，保存云盘授权及相关 token |
| `zhenxuan.json` | 运行工作目录下，两个臻讯云版本使用的 JWT 缓存 |
| `rainyun.json` | 雨云脚本同目录，保存账号登录信息 |
| `account_keys.json`、`hz_token_cache.json`、`hz_blacklist.json` | 基元律动脚本同目录，保存账号 API Key、接码 token 和号码记录 |

会员日还兼容读取 `APP/yunpan_token_storage.json` 等旧缓存；主脚本当前主要使用工作目录下的 token 缓存。修改主脚本的存储目录后，会员日的 deviceId 缓存读取不会自动跟随。部署时保持工作目录稳定，并保留需要的缓存。

## 常见问题与当前限制

- **`No module named Crypto`**：在任务实际使用的 Python 环境中安装 `pycryptodome`；不要安装名为 `Crypto` 的包替代。
- **`Cannot find module` / `sharp` 安装失败**：确认依赖安装到任务可访问的位置，并检查 Node.js 版本、平台及依赖的 `engines` 要求。安装较新 `jsdom` 时可能需要较新的 Node.js；本次检查的 `jsdom` 30.1.2 要求 Node.js `^22.22.2`、`^24.15.0` 或 `>=26.0.0`。
- **未找到账号 / 账号数不符**：检查变量名大小写、字段顺序、分隔符及面板注入方式，不要假设所有脚本都支持换行或多个同名变量。
- **token 失效 / 登录失败**：先检查凭据与服务端响应，再检查缓存和验证码流程；代码存在刷新逻辑并不代表每次刷新都能成功。
- **验证码或积分任务失败**：第三方规则、活动开放时间和接口可能变化，本地语法与依赖检查不能证明线上任务可用。
- **HTTPS 校验**：部分现有脚本关闭了 TLS 证书验证，包括安心记加班、小时工、臻讯云、雨云和验证码模块；正式使用前建议恢复验证并配置可信证书。
- **凭据与日志**：不要公开账号密码、完整 token、OpenAPI 密钥、通知配置或运行缓存。部分脚本会打印凭据片段、API Key 或错误账号原文，分享日志前应检查并脱敏。仓库当前没有 `.gitignore`，添加本地缓存和依赖目录前请检查 Git 变更。

脚本仅供参考。当前仓库没有统一测试或 CI 配置；上述文档描述代码行为，不承诺第三方接口、验证码识别或收益结果。
