/**
 * 臻讯云自动登录脚本 - 2Captcha 打码版
 * @author xiaohai
 * @date 2026-03-29 
 * @version 1.0.0
 * @description 自动签到获取积分，自动领取已完成任务奖励
 * 使用 2Captcha 服务解决腾讯防水墙验证码，无需本地算法
 * 依赖: axios jsdom sharp crypto
 * 
 * 环境变量:
 *   zhenxun=账号#密码
 *   zhenxun=账号1#密码1@账号2#密码2
 *   TWOCAPTCHA_KEY=你的2Captcha API Key
 * 
 * 代理配置:
 *   ZHENXUN_PROXY=http://ip:port
 */

const axios = require('axios');
const crypto = require('crypto');
const https = require('https');
const fs = require('fs');
const path = require('path');

const httpsAgent = new https.Agent({ rejectUnauthorized: false });
const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1 Edg/146.0.0.0';
const TCAPTCHA_APP_ID = '190094633';
const WEBSITE_URL = 'https://zhenxun.cn/login.htm';

const JWT_FILE = path.join(process.cwd(), 'zhenxuan.json');
const CAPTCHA_API = 'https://api.2captcha.com';

async function sendNotify(title, content) {
    try {
        const { sendNotify: qlNotify } = require('./sendNotify.js');
        await qlNotify(title, content);
    } catch (e) {
        console.log(`\n【通知】${title}`);
        console.log(content);
    }
}

function readJwtStore() {
    try {
        if (fs.existsSync(JWT_FILE)) {
            const data = fs.readFileSync(JWT_FILE, 'utf8');
            return JSON.parse(data);
        }
    } catch (e) {
        console.log('💾 读取存储文件失败:', e.message);
    }
    return {};
}

function saveJwtStore(store) {
    try {
        fs.writeFileSync(JWT_FILE, JSON.stringify(store, null, 2));
        return true;
    } catch (e) {
        console.log('💾 保存存储文件失败:', e.message);
        return false;
    }
}

function getJwt(account) {
    const store = readJwtStore();
    return store[account] || null;
}

function setJwt(account, jwt, expireTime = null) {
    const store = readJwtStore();
    store[account] = {
        jwt,
        loginTime: new Date().toISOString(),
        expireTime,
    };
    return saveJwtStore(store);
}

function isJwtValid(jwtData) {
    if (!jwtData || !jwtData.jwt) return false;
    if (jwtData.expireTime) {
        const expire = new Date(jwtData.expireTime).getTime();
        if (Date.now() > expire) return false;
    }
    const loginTime = new Date(jwtData.loginTime || 0).getTime();
    const maxAge = 6 * 24 * 60 * 60 * 1000;
    if (Date.now() - loginTime > maxAge) return false;
    return true;
}

async function getProxyConfig() {
    const proxyUrl = process.env.ZHENXUN_PROXY;
    if (!proxyUrl) return null;
    try {
        const url = proxyUrl.trim();
        const isApiUrl = url.includes('getapi') ||
                         url.includes('daili') ||
                         url.includes('api.') ||
                         url.includes('?') && (url.includes('qty=') || url.includes('port='));
        if (isApiUrl) {
            return await fetchProxyFromApi(url);
        }
        let proxyAddr = url;
        if (!proxyAddr.startsWith('http')) {
            proxyAddr = 'http://' + proxyAddr;
        }
        const parsed = new URL(proxyAddr);
        return {
            protocol: parsed.protocol.replace(':', ''),
            host: parsed.hostname,
            port: parseInt(parsed.port) || 80,
        };
    } catch (e) {
        console.log('❌ 解析代理地址失败:', e.message);
        return null;
    }
}

async function fetchProxyFromApi(apiUrl) {
    try {
        console.log('🌐 正在获取代理...');
        const response = await axios.get(apiUrl, {
            timeout: 10000,
            httpsAgent,
        });
        let proxyText = '';
        if (typeof response.data === 'string') {
            proxyText = response.data.trim();
        } else if (response.data) {
            proxyText = String(response.data).trim();
        }
        const match = proxyText.match(/(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)/);
        if (match) {
            console.log(`✅ 代理获取成功: ${match[1]}:${match[2]}`);
            return {
                protocol: 'http',
                host: match[1],
                port: parseInt(match[2]),
            };
        }
        console.log('⚠️ 代理API返回格式无效:', proxyText.substring(0, 100));
        return null;
    } catch (e) {
        console.log('❌ 获取代理失败:', e.message);
        return null;
    }
}

function encryptPassword(password) {
    const key = 'idcsmart.finance';
    const iv = '9311019310287172';
    const cipher = crypto.createCipheriv('aes-128-cbc', key, iv);
    let encrypted = cipher.update(password, 'utf8', 'base64');
    encrypted += cipher.final('base64');
    return encrypted;
}

function getRequestConfig(jwt, proxyConfig, extraHeaders = {}) {
    const config = {
        headers: {
            'User-Agent': UA,
            'Accept': 'application/json, text/plain, */*',
            'Authorization': `Bearer ${jwt}`,
            'Referer': 'https://zhenxun.cn/plugin/109/checkin.htm',
            ...extraHeaders,
        },
        httpsAgent,
        timeout: 10000,
    };
    if (proxyConfig) {
        config.proxy = proxyConfig;
        config.timeout = 15000;
    }
    return config;
}

async function solveTencentCaptcha(proxyConfig) {
    const apiKey = process.env.TWOCAPTCHA_KEY;
    if (!apiKey) {
        throw new Error('未设置 TWOCAPTCHA_KEY 环境变量');
    }

    console.log('📤 提交腾讯验证码任务到 2Captcha...');
    const taskType = proxyConfig ? 'TencentTask' : 'TencentTaskProxyless';

    const createPayload = {
        clientKey: apiKey,
        task: {
            type: taskType,
            appId: TCAPTCHA_APP_ID,
            websiteURL: WEBSITE_URL,
        },
    };

    if (proxyConfig && taskType === 'TencentTask') {
        createPayload.task.proxyType = 'http';
        createPayload.task.proxyAddress = proxyConfig.host;
        createPayload.task.proxyPort = proxyConfig.port;
    }

    const createR = await axios.post(`${CAPTCHA_API}/createTask`, createPayload, {
        headers: { 'Content-Type': 'application/json' },
        timeout: 30000,
    });

    if (createR.data.errorId && createR.data.errorId !== 0) {
        throw new Error(`2Captcha 创建任务失败: ${createR.data.errorDescription || createR.data.errorCode}`);
    }

    const taskId = createR.data.taskId;
    if (!taskId) {
        throw new Error('2Captcha 未返回任务 ID');
    }
    console.log(`📋 任务 ID: ${taskId}`);

    console.log('⏳ 等待人工打码...');
    const maxWait = 120;
    const pollInterval = 5;
    let elapsed = 0;

    while (elapsed < maxWait) {
        await new Promise(r => setTimeout(r, pollInterval * 1000));
        elapsed += pollInterval;
        process.stdout.write(`\r⏳ 已等待 ${elapsed}s ...`);

        const resultR = await axios.post(`${CAPTCHA_API}/getTaskResult`, {
            clientKey: apiKey,
            taskId: taskId,
        }, {
            headers: { 'Content-Type': 'application/json' },
            timeout: 15000,
        });

        if (resultR.data.errorId && resultR.data.errorId !== 0) {
            throw new Error(`2Captcha 查询失败: ${resultR.data.errorDescription || resultR.data.errorCode}`);
        }

        if (resultR.data.status === 'ready') {
            console.log(`\n✅ 打码完成! (耗时 ${elapsed}s)`);
            const solution = resultR.data.solution;
            return {
                ticket: solution.ticket,
                randstr: solution.randstr || '@' + Math.random().toString(36).substring(2, 6),
            };
        }
    }

    throw new Error(`2Captcha 超时 (${maxWait}s)`);
}

async function testJwt(account, jwt, proxyConfig) {
    try {
        console.log('🔑 正在验证令牌...');
        const config = getRequestConfig(jwt, proxyConfig);
        const r = await axios.get('https://zhenxun.cn/console/v1/account', config);
        if (r.data.status === 200) {
            const userInfo = r.data.data?.account;
            console.log('✅ 令牌有效，欢迎回来:', userInfo?.username || account);
            return { valid: true, userInfo };
        }
    } catch (e) {
        if (e.response?.status === 401) {
            console.log('⏰ 令牌已过期，需要重新登录');
        } else {
            console.log('⚠️ 验证失败:', e.message);
        }
    }
    return { valid: false };
}

async function getCheckinStatus(jwt, proxyConfig) {
    try {
        const config = getRequestConfig(jwt, proxyConfig);
        const r = await axios.get('https://zhenxun.cn/console/v1/plugin/cany_points_mall/checkin/status', config);
        if (r.data.status === 200) {
            return {
                success: true,
                enabled: r.data.data?.enabled,
                hasCheckinToday: r.data.data?.has_checkin_today,
                continuousDays: r.data.data?.continuous_days,
                totalPoints: r.data.data?.total_points,
                todayPoints: r.data.data?.today_points,
                monthDays: r.data.data?.month_days,
                totalDays: r.data.data?.total_days,
            };
        }
        return { success: false, error: r.data.msg };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

async function doCheckin(jwt, proxyConfig) {
    try {
        const config = getRequestConfig(jwt, proxyConfig, {
            'Content-Type': 'application/json',
            'Origin': 'https://zhenxun.cn',
        });
        const r = await axios.post('https://zhenxun.cn/console/v1/plugin/cany_points_mall/checkin', {}, config);
        if (r.data.status === 200) {
            return {
                success: true,
                basePoints: r.data.data?.base_points,
                bonusPoints: r.data.data?.bonus_points,
                bonusName: r.data.data?.bonus_name,
                totalPoints: r.data.data?.total_points,
                continuousDays: r.data.data?.continuous_days,
                msg: r.data.msg,
            };
        }
        return { success: false, error: r.data.msg };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

async function checkinTask(account, jwt, proxyConfig) {
    console.log('\n📋 正在查询签到状态...');
    const status = await getCheckinStatus(jwt, proxyConfig);
    if (!status.success) {
        console.log('❌ 查询失败:', status.error);
        return { checkinSuccess: false, error: status.error };
    }
    if (!status.enabled) {
        console.log('⚠️ 签到功能未启用');
        return { checkinSuccess: false, error: '签到功能未启用' };
    }
    console.log(`💎 当前积分: ${status.totalPoints} | 🔥 连续签到: ${status.continuousDays}天`);
    if (status.hasCheckinToday) {
        console.log('✨ 今日已签到，明天再来吧~');
        return {
            checkinSuccess: true,
            alreadyCheckin: true,
            continuousDays: status.continuousDays,
            totalPoints: status.totalPoints,
        };
    }
    console.log('🎯 正在执行签到...');
    const result = await doCheckin(jwt, proxyConfig);
    if (result.success) {
        console.log(`🎉 签到成功! 获得 ${result.basePoints} 积分`);
        if (result.bonusPoints > 0) {
            console.log(`🎁 额外奖励: ${result.bonusPoints} 积分 (${result.bonusName})`);
        }
        console.log(`💰 总积分: ${result.totalPoints} | 🔥 连续签到: ${result.continuousDays}天`);
        return {
            checkinSuccess: true,
            basePoints: result.basePoints,
            bonusPoints: result.bonusPoints,
            bonusName: result.bonusName,
            totalPoints: result.totalPoints,
            continuousDays: result.continuousDays,
        };
    } else {
        console.log('😢 签到失败:', result.error);
        return { checkinSuccess: false, error: result.error };
    }
}

async function getCheckinCalendar(jwt, proxyConfig, year, month) {
    try {
        const config = getRequestConfig(jwt, proxyConfig);
        const r = await axios.get(`https://zhenxun.cn/console/v1/plugin/cany_points_mall/checkin/calendar?year=${year}&month=${month}`, config);
        if (r.data.status === 200) {
            return {
                success: true,
                year: r.data.data?.year,
                month: r.data.data?.month,
                daysInMonth: r.data.data?.days_in_month,
                checkinDays: r.data.data?.checkin_days,
                calendar: r.data.data?.calendar,
            };
        }
        return { success: false, error: r.data.msg };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

async function getTaskList(jwt, proxyConfig) {
    try {
        const config = getRequestConfig(jwt, proxyConfig);
        const r = await axios.get('https://zhenxun.cn/console/v1/plugin/cany_points_mall/task', config);
        if (r.data.status === 200) {
            return { success: true, tasks: r.data.data || [] };
        }
        return { success: false, error: r.data.msg };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

async function claimTask(taskId, jwt, proxyConfig) {
    try {
        const config = getRequestConfig(jwt, proxyConfig, {
            'Content-Type': 'application/json',
            'Origin': 'https://zhenxun.cn',
        });
        const r = await axios.post(`https://zhenxun.cn/console/v1/plugin/cany_points_mall/task/${taskId}/claim`, {}, config);
        if (r.data.status === 200) {
            return { success: true, points: r.data.data?.points, msg: r.data.msg };
        }
        return { success: false, error: r.data.msg };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

async function claimCompletedTasks(tasks, jwt, proxyConfig) {
    const completedTasks = tasks.filter(t => t.progress?.status === 1);
    if (completedTasks.length === 0) return { claimedPoints: 0 };
    console.log(`🎁 发现 ${completedTasks.length} 个可领取奖励的任务\n`);
    let totalClaimed = 0;
    for (const task of completedTasks) {
        console.log(`   🎯 正在领取「${task.name}」奖励...`);
        const result = await claimTask(task.id, jwt, proxyConfig);
        if (result.success) {
            console.log(`   ✅ 领取成功! +${result.points}积分`);
            totalClaimed += result.points || 0;
        } else {
            console.log(`   ❌ 领取失败: ${result.error}`);
        }
    }
    if (totalClaimed > 0) console.log(`\n💰 共领取 ${totalClaimed} 积分!`);
    return { claimedPoints: totalClaimed };
}

async function showTaskList(jwt, proxyConfig) {
    console.log('\n📜 正在查询任务列表...');
    const result = await getTaskList(jwt, proxyConfig);
    if (!result.success) {
        console.log('❌ 查询任务失败:', result.error);
        return { claimedPoints: 0 };
    }
    const tasks = result.tasks;
    if (tasks.length === 0) {
        console.log('📭 暂无任务');
        return { claimedPoints: 0 };
    }
    console.log(`\n🎯 发现 ${tasks.length} 个任务:\n`);
    const newbieTasks = tasks.filter(t => t.category === 'newbie');
    const growthTasks = tasks.filter(t => t.category === 'growth');
    if (newbieTasks.length > 0) {
        console.log('🌟 新手任务:');
        for (const task of newbieTasks) {
            const status = task.progress?.status === 1 ? '✅' : '⏳';
            const progress = task.progress ? `${task.progress.current_value}/${task.progress.target_value}` : '';
            console.log(`   ${status} ${task.name} (+${task.points}积分) ${progress}`);
        }
        console.log('');
    }
    if (growthTasks.length > 0) {
        console.log('📈 成长任务:');
        for (const task of growthTasks) {
            const status = task.progress?.status === 1 ? '✅' : '⏳';
            const progress = task.progress ? `${task.progress.current_value}/${task.progress.target_value}` : '';
            const percent = task.progress?.percent ? `(${task.progress.percent}%)` : '';
            console.log(`   ${status} ${task.name} (+${task.points}积分) ${progress} ${percent}`);
        }
        console.log('');
    }
    return await claimCompletedTasks(tasks, jwt, proxyConfig);
}

async function getPointsAccount(jwt, proxyConfig) {
    try {
        const config = getRequestConfig(jwt, proxyConfig);
        const r = await axios.get('https://zhenxun.cn/console/v1/plugin/cany_points_mall/account', config);
        if (r.data.status === 200) {
            return {
                success: true,
                totalPoints: r.data.data?.total_points,
                availablePoints: r.data.data?.available_points,
                usedPoints: r.data.data?.used_points,
                level: r.data.data?.level,
                continuousDays: r.data.data?.continuous_checkin_days,
                lastCheckinDate: r.data.data?.last_checkin_date,
            };
        }
        return { success: false, error: r.data.msg };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

async function showPointsAccount(jwt, proxyConfig) {
    console.log('\n💳 正在查询积分账户...');
    const result = await getPointsAccount(jwt, proxyConfig);
    if (!result.success) {
        console.log('❌ 查询失败:', result.error);
        return;
    }
    console.log('\n📊 积分账户概览:');
    console.log(`   💎 总积分: ${result.totalPoints}`);
    console.log(`   ✅ 可用积分: ${result.availablePoints}`);
    if (result.usedPoints > 0) {
        console.log(`   📤 已使用: ${result.usedPoints}`);
    }
    console.log(`   🏆 等级: Lv.${result.level}`);
    if (result.continuousDays > 0) {
        console.log(`   🔥 连续签到: ${result.continuousDays}天`);
    }
}

async function doLogin(account, password, proxyConfig) {
    console.log(`\n========== 账号: ${account} ==========\n`);
    const maxRetries = 3;
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        console.log(`🔄 第 ${attempt}/${maxRetries} 次尝试`);
        if (attempt > 1) {
            const delay = 15000 + Math.random() * 15000;
            console.log(`⏳ 等待 ${(delay / 1000).toFixed(1)}s ...`);
            await new Promise(r => setTimeout(r, delay));
        }
        try {
            console.log('🧩 正在通过 2Captcha 破解验证码...');
            const captchaResult = await solveTencentCaptcha(proxyConfig);
            console.log(`🎫 ticket: ${captchaResult.ticket.substring(0, 40)}...`);
            console.log(`🎲 randstr: ${captchaResult.randstr}`);

            console.log('🔐 正在登录...');
            const cookies = {};
            let captchaConfig = {
                headers: { 'User-Agent': UA, 'Accept': 'application/json', 'Referer': 'https://zhenxun.cn/login.htm' },
                httpsAgent,
                timeout: 30000,
            };
            if (proxyConfig) {
                captchaConfig.proxy = proxyConfig;
                captchaConfig.timeout = 15000;
            }
            const captchaR = await axios.get('https://zhenxun.cn/console/v1/captcha', captchaConfig);
            if (captchaR.headers?.['set-cookie']) {
                captchaR.headers['set-cookie'].forEach(c => {
                    const m = c.match(/^([^=]+)=([^;]+)/);
                    if (m) cookies[m[1]] = m[2];
                });
            }

            const cookieStr = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; ');
            let loginConfig = {
                headers: {
                    'User-Agent': UA,
                    'Accept': 'application/json, text/plain, */*',
                    'Content-Type': 'application/json',
                    'Origin': 'https://zhenxun.cn',
                    'Referer': 'https://zhenxun.cn/login.htm',
                    'Cookie': cookieStr,
                },
                httpsAgent,
                timeout: 30000,
            };
            if (proxyConfig) {
                loginConfig.proxy = proxyConfig;
                loginConfig.timeout = 15000;
            }
            const r = await axios.post('https://zhenxun.cn/console/v1/login', {
                type: 'password',
                account: account,
                phone_code: '86',
                code: '',
                password: encryptPassword(password),
                remember_password: '0',
                captcha: captchaResult.randstr,
                token: captchaResult.ticket,
                client_operate_password: '',
            }, loginConfig);
            if (r.data.status === 200) {
                console.log('🎉 登录成功!');
                setJwt(account, r.data.data.jwt, r.data.data.expire_time);
                console.log(`💾 令牌已保存`);
                return { success: true, jwt: r.data.data.jwt, account };
            } else {
                console.log('😢 登录失败:', r.data.msg);
                if (r.data.msg && r.data.msg.includes('密码')) {
                    return { success: false, error: r.data.msg };
                }
                if (attempt < maxRetries) continue;
                return { success: false, error: r.data.msg };
            }
        } catch (e) {
            console.log('❌ 出错了:', e.message);
            if (attempt < maxRetries) continue;
            return { success: false, error: e.message };
        }
    }
    return { success: false, error: '超过最大重试次数' };
}

async function processAccount(account, password, proxyConfig) {
    console.log(`\n🌟 正在处理账号: ${account}\n`);
    let jwt = null;
    let fromCache = false;
    const jwtData = getJwt(account);
    if (jwtData && isJwtValid(jwtData)) {
        console.log('🔍 发现已有令牌，正在验证...');
        const testResult = await testJwt(account, jwtData.jwt, proxyConfig);
        if (testResult.valid) {
            console.log('✨ 令牌有效，直接开始签到~');
            jwt = jwtData.jwt;
            fromCache = true;
        } else {
            console.log('🔄 令牌已失效，准备重新登录...');
        }
    } else if (jwtData) {
        console.log('⏰ 令牌已过期，准备重新登录...');
    } else {
        console.log('🆕 首次登录，开始验证...');
    }
    if (!jwt) {
        const loginResult = await doLogin(account, password, proxyConfig);
        if (!loginResult.success) return loginResult;
        jwt = loginResult.jwt;
    }
    const checkinResult = await checkinTask(account, jwt, proxyConfig);
    const now = new Date();
    const calendarResult = await getCheckinCalendar(jwt, proxyConfig, now.getFullYear(), now.getMonth() + 1);
    if (calendarResult.success) {
        console.log(`📅 ${calendarResult.year}年${calendarResult.month}月 已累计签到 ${calendarResult.checkinDays} 天`);
    }
    const taskResult = await showTaskList(jwt, proxyConfig);
    await showPointsAccount(jwt, proxyConfig);
    return {
        success: true,
        jwt,
        account,
        fromCache,
        checkin: checkinResult,
        claimedPoints: taskResult?.claimedPoints || 0,
    };
}

function parseAccounts() {
    const accounts = [];
    for (const [key, value] of Object.entries(process.env)) {
        if (key === 'zhenxun' || key.startsWith('zhenxun_')) {
            if (!value) continue;
            const separators = value.includes('@') ? '@' : '\n';
            const items = value.split(separators);
            for (const item of items) {
                const trimmed = item.trim();
                if (!trimmed) continue;
                const parts = trimmed.split('#');
                if (parts.length === 2) {
                    accounts.push({ account: parts[0].trim(), password: parts[1].trim() });
                }
            }
        }
    }
    return accounts;
}

async function main() {
    console.log('🎉 臻讯云自动签到 (2Captcha 打码版) 🎉');
    console.log('⏰ 时间:', new Date().toLocaleString());
    console.log('');

    const apiKey = process.env.TWOCAPTCHA_KEY;
    if (!apiKey) {
        console.error('❌ 未设置 TWOCAPTCHA_KEY 环境变量');
        console.error('💡 请在 2captcha.com 注册并获取 API Key');
        console.error('💡 设置方式: export TWOCAPTCHA_KEY=你的API_KEY');
        process.exit(1);
    }
    console.log(`🔑 2Captcha Key: ${apiKey.substring(0, 8)}...`);

    const accounts = parseAccounts();
    if (accounts.length === 0) {
        console.error('❌ 未找到有效账号');
        console.error('💡 格式: zhenxun=账号#密码');
        console.error('💡 多账号: zhenxun=账号1#密码1@账号2#密码2');
        process.exit(1);
    }

    const proxyConfig = await getProxyConfig();
    if (proxyConfig) {
        console.log(`🌐 代理: ${proxyConfig.host}:${proxyConfig.port}`);
    }
    console.log(`👤 账号数量: ${accounts.length}`);
    console.log(`💾 令牌文件: ${JWT_FILE}`);
    console.log('');

    const results = [];
    for (const { account, password } of accounts) {
        const result = await processAccount(account, password, proxyConfig);
        results.push({ account, ...result });
        console.log('');
    }

    console.log('📊 执行结果汇总 📊');
    let successCount = 0;
    for (const r of results) {
        if (r.success) {
            const cacheMark = r.fromCache ? ' ⚡缓存' : '';
            let checkinInfo = '';
            if (r.checkin) {
                if (r.checkin.checkinSuccess) {
                    if (r.checkin.alreadyCheckin) {
                        checkinInfo = ` | ✅已签到 | 💎${r.checkin.totalPoints}积分`;
                    } else {
                        checkinInfo = ` | 🎉签到+${r.checkin.basePoints}积分`;
                        if (r.checkin.bonusPoints > 0) {
                            checkinInfo += `(🎁+${r.checkin.bonusPoints})`;
                        }
                        checkinInfo += ` | 💎${r.checkin.totalPoints}积分`;
                    }
                } else {
                    checkinInfo = ` | 😢签到失败:${r.checkin.error}`;
                }
            }
            let taskInfo = '';
            if (r.claimedPoints > 0) {
                taskInfo = ` | 🎁任务+${r.claimedPoints}积分`;
            }
            console.log(`✅ ${r.account}${cacheMark}${checkinInfo}${taskInfo}`);
            successCount++;
        } else {
            console.log(`❌ ${r.account}: ${r.error || '失败'}`);
        }
    }
    console.log('');
    console.log(`🎯 成功率: ${successCount}/${accounts.length}`);

    const notifyTitle = '臻讯云自动签到';
    const notifyContent = results.map(r => {
        const cacheMark = r.fromCache ? ' [缓存]' : '';
        if (r.success && r.checkin) {
            if (r.checkin.checkinSuccess) {
                if (r.checkin.alreadyCheckin) {
                    let info = `${r.account}: ✓ 已签到 | 积分:${r.checkin.totalPoints}`;
                    if (r.claimedPoints > 0) info += ` 任务+${r.claimedPoints}`;
                    info += `${cacheMark}`;
                    return info;
                } else {
                    let info = `${r.account}: ✓ 签到+${r.checkin.basePoints}积分`;
                    if (r.checkin.bonusPoints > 0) info += ` 奖励+${r.checkin.bonusPoints}`;
                    if (r.claimedPoints > 0) info += ` 任务+${r.claimedPoints}`;
                    info += ` | 总积分:${r.checkin.totalPoints}${cacheMark}`;
                    return info;
                }
            } else {
                return `${r.account}: ✓ 登录成功 | 签到失败:${r.checkin.error}${cacheMark}`;
            }
        }
        return `${r.account}: ${r.success ? '✓ 成功' + cacheMark : '✗ ' + (r.error || '失败')}`;
    }).join('\n');
    await sendNotify(notifyTitle, notifyContent);

    process.exit(successCount === accounts.length ? 0 : 1);
}

main().catch(e => {
    console.error('程序错误:', e);
    process.exit(1);
});
