/**
 * 雨云自动签到 - 点选版
 *
 * 环境变量:
 *   RAINYUN_ACCOUNTS - 必填，格式: 账号#密码
 *     多账号用 @ 或换行分隔
 *     亦兼容多个同名环境变量（青龙面板支持）
 *     示例: 用户1#密码1@用户2#密码2
 *
 * 青龙面板添加方式:
 *   1. 环境变量名: RAINYUN_ACCOUNTS
 *   2. 值: 账号#密码（多账号用 @ 分隔，或添加多个同名变量）
 *   3. 定时规则: 0 8 * * *（每天8点）
 *
 * 依赖:
 *   npm install jsdom sharp
 */

const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const { JSDOM } = require('jsdom');
const sharp = require('sharp');

const DATA_FILE = path.join(__dirname, 'rainyun.json');

const RAINYUN_BASE = 'https://api.v2.rainyun.com';
const RAINYUN_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0';
const RAINYUN_HEADERS = {
    'User-Agent': RAINYUN_UA,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Origin': 'https://app.rainyun.com',
    'Referer': 'https://app.rainyun.com/',
    'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
};

const CAPTCHA_DOMAIN = 'turing.captcha.qcloud.com';
const CAPTCHA_APP_ID = '2039519451';
const CAPTCHA_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1 Edg/146.0.0.0';
const CAPTCHA_UA_B64 = Buffer.from(CAPTCHA_UA).toString('base64');

function decompressBody(buffer, encoding) {
    if (encoding === 'gzip' || encoding === 'x-gzip') return zlib.gunzipSync(buffer);
    if (encoding === 'deflate' || encoding === 'x-deflate') return zlib.inflateSync(buffer);
    if (encoding === 'br') return zlib.brotliDecompressSync(buffer);
    return buffer;
}

function httpsGet(url, headers = {}) {
    return new Promise((resolve, reject) => {
        const urlObj = new URL(url);
        const req = https.request({
            hostname: urlObj.hostname,
            path: urlObj.pathname + urlObj.search,
            method: 'GET',
            headers: { 'User-Agent': CAPTCHA_UA, ...headers },
            rejectUnauthorized: false,
        }, (res) => {
            const chunks = [];
            res.on('data', c => chunks.push(c));
            res.on('end', () => {
                const raw = Buffer.concat(chunks);
                const body = decompressBody(raw, res.headers['content-encoding']);
                resolve({ status: res.statusCode, headers: res.headers, body, cookies: res.headers['set-cookie'] || '' });
            });
        });
        req.on('error', reject);
        req.end();
    });
}

function httpsPost(url, body, headers = {}) {
    return new Promise((resolve, reject) => {
        const urlObj = new URL(url);
        const bodyStr = typeof body === 'string' ? body : new URLSearchParams(body).toString();
        const req = https.request({
            hostname: urlObj.hostname,
            path: urlObj.pathname + urlObj.search,
            method: 'POST',
            headers: {
                'User-Agent': CAPTCHA_UA,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': Buffer.byteLength(bodyStr),
                ...headers,
            },
            rejectUnauthorized: false,
        }, (res) => {
            const chunks = [];
            res.on('data', c => chunks.push(c));
            res.on('end', () => {
                const raw = Buffer.concat(chunks);
                const body = decompressBody(raw, res.headers['content-encoding']);
                resolve({ status: res.statusCode, headers: res.headers, body, cookies: res.headers['set-cookie'] || '' });
            });
        });
        req.on('error', reject);
        req.write(bodyStr);
        req.end();
    });
}

function extractJson(text) {
    const str = typeof text === 'string' ? text : text.toString();
    const m = str.match(/\{[\s\S]*\}/);
    return m ? JSON.parse(m[0]) : null;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

class RainyunClient {
    constructor(username, password) {
        this.username = username;
        this.password = password;
        this.cookies = {};
        this.csrfToken = '';
    }

    async request(method, urlPath, body = null) {
        return new Promise((resolve, reject) => {
            const url = new URL(RAINYUN_BASE + urlPath);
            const bodyStr = body ? JSON.stringify(body) : null;
            const options = {
                hostname: url.hostname,
                path: url.pathname,
                method: method.toUpperCase(),
                headers: { ...RAINYUN_HEADERS },
                rejectUnauthorized: false,
            };
            if (this.csrfToken) options.headers['x-csrf-token'] = this.csrfToken;
            const cookieStr = Object.entries(this.cookies).map(([k, v]) => `${k}=${v}`).join('; ');
            if (cookieStr) options.headers['Cookie'] = cookieStr;
            if (bodyStr) {
                options.headers['Content-Type'] = 'application/json';
                options.headers['Content-Length'] = Buffer.byteLength(bodyStr);
            }
            const req = https.request(options, (res) => {
                const chunks = [];
                res.on('data', c => chunks.push(c));
                res.on('end', () => {
                    const raw = Buffer.concat(chunks);
                    const respBody = decompressBody(raw, res.headers['content-encoding']);
                    const text = respBody.toString();
                    const setCookies = res.headers['set-cookie'] || [];
                    for (const sc of setCookies) {
                        const m = sc.match(/^([^=]+)=([^;]*)/);
                        if (m) this.cookies[m[1].trim()] = m[2].trim();
                    }
                    try { resolve(JSON.parse(text)); }
                    catch { resolve({ raw: text }); }
                });
            });
            req.on('error', reject);
            if (bodyStr) req.write(bodyStr);
            req.end();
        });
    }

    async login() {
        console.log(`[1] 正在登录账号: ${this.username}`);
        this.csrfToken = 'undefined';
        const data = await this.request('POST', '/user/login', { field: this.username, password: this.password });
        if (data.code !== 200) {
            console.log(`    登录失败: ${JSON.stringify(data)}`);
            return false;
        }
        this.csrfToken = this.cookies['X-CSRF-Token'] || '';
        if (!this.csrfToken) {
            for (const [k, v] of Object.entries(this.cookies)) {
                if (k.toLowerCase().includes('csrf') || k.toLowerCase().includes('token')) {
                    this.csrfToken = v;
                    break;
                }
            }
        }
        console.log(`    登录成功! CSRF Token: ${this.csrfToken ? '已获取' : '未获取'}`);
        return true;
    }

    async getUserInfo() {
        console.log('[信息] 正在获取用户信息...');
        const data = await this.request('GET', '/user/?no_cache=false');
        if (data.code === 200) {
            const u = data.data;
            console.log(`    用户名: ${u.Name}`);
            console.log(`    邮箱: ${u.Email}`);
            console.log(`    手机: ${u.Phone}`);
            console.log(`    积分: ${u.Points}`);
            console.log(`    会员等级: ${u.VIP?.Title || '无'}`);
            return u;
        }
        console.log(`    获取用户信息失败: ${JSON.stringify(data)}`);
        return null;
    }

    async getTasks() {
        console.log('[任务] 正在获取任务列表...');
        const data = await this.request('GET', '/user/reward/tasks');
        if (data.code === 200) {
            const statusMap = { 0: '未完成', 1: '可领取', 2: '已完成' };
            for (const t of data.data) {
                console.log(`      ${t.Name} - ${t.Points}积分 - ${statusMap[t.Status] || '未知'}`);
            }
            return data.data;
        }
        console.log(`    获取任务列表失败: ${JSON.stringify(data)}`);
        return null;
    }

    async submitSign(ticket, randstr) {
        const data = await this.request('POST', '/user/reward/tasks', {
            task_name: '每日签到',
            verifyCode: '',
            vticket: ticket,
            vrandstr: randstr,
        });
        if (data.code === 200) {
            console.log('    签到成功！');
            return true;
        }
        console.log(`    签到失败: ${JSON.stringify(data)}`);
        return false;
    }

    async signWithoutCaptcha() {
        console.log('[签到] 尝试直接签到（无验证码）...');
        const data = await this.request('POST', '/user/reward/tasks', {
            task_name: '每日签到',
            verifyCode: '',
        });
        if (data.code === 200) {
            console.log('    签到成功！');
            return true;
        } else if (data.code === 10004) {
            console.log('    需要验证码，开始自动过验证码...');
            return await this.signWithAutoCaptcha();
        }
        console.log(`    签到失败: ${JSON.stringify(data)}`);
        return false;
    }

    async signWithAutoCaptcha() {
        const maxRetries = 5;
        for (let attempt = 1; attempt <= maxRetries; attempt++) {
            console.log(`    [验证码] 第 ${attempt}/${maxRetries} 次尝试...`);
            if (attempt > 1) await sleep(2000 + Math.random() * 3000);
            const solver = new ClickSelectCaptchaSolver();
            const result = await solver.solve();
            if (result) {
                return await this.submitSign(result.ticket, result.randstr);
            }
            console.log(`    [验证码] 第 ${attempt} 次验证失败`);
        }
        console.log('    [验证码] 所有尝试均失败');
        return false;
    }
}

class ClickSelectCaptchaSolver {
    async solve() {
        console.log('    [验证码] 开始解决腾讯点选验证码...');
        console.log('    [验证码] 1. 获取验证码数据...');
        const preData = await this.prehandle();
        if (!preData) return null;

        const dynShow = preData.data?.dyn_show_info;
        const commCfg = preData.data?.comm_captcha_cfg;
        const powCfg = commCfg?.pow_cfg;
        const bgSize = dynShow?.bg_elem_cfg?.size_2d || [672, 480];
        const insElemCfg = dynShow?.ins_elem_cfg || [];

        console.log(`    sid: ${preData.sid}, subcapclass: ${preData.subcapclass}`);

        console.log('    [验证码] 2. 下载验证码图片...');
        let bgBuf = null, spriteBuf = null;
        if (dynShow?.bg_elem_cfg?.img_url) {
            bgBuf = await this.downloadImage(dynShow.bg_elem_cfg.img_url);
            const meta = await sharp(bgBuf).metadata();
            console.log(`    背景图: ${meta.width}x${meta.height}`);
        }
        if (dynShow?.sprite_url) {
            spriteBuf = await this.downloadImage(dynShow.sprite_url);
            const meta = await sharp(spriteBuf).metadata();
            console.log(`    精灵图: ${meta.width}x${meta.height}`);
        }
        if (!bgBuf || !spriteBuf) {
            console.log('    [验证码] 图片下载失败');
            return null;
        }

        console.log('    [验证码] 3. 识别字符位置...');
        const clickPoints = await this.findCharsInBg(bgBuf, spriteBuf, insElemCfg, bgSize);
        if (!clickPoints || clickPoints.length === 0) {
            console.log('    [验证码] 未识别到字符');
            return null;
        }

        const ans = JSON.stringify(clickPoints.map((p, i) => ({
            elem_id: i + 1,
            type: 'DynAnswerType_POS',
            data: `${p.spriteX},${p.spriteY}`,
        })));
        console.log(`    ans: ${ans}`);

        console.log('    [验证码] 4. 加载 TDC...');
        const tdcPath = commCfg?.tdc_path || '/tdc.js';
        const tdcCode = await this.loadTDC(tdcPath);

        console.log('    [验证码] 5. 解 PoW...');
        let powAnswer = '0', powCalcTime = 0;
        if (powCfg) {
            const r = this.solvePoW(powCfg.prefix, powCfg.md5);
            powAnswer = r.answer;
            powCalcTime = r.calcTime;
        }

        console.log('    [验证码] 6. 生成轨迹 + TDC 加密...');
        const tracks = this.generateClickTracks(clickPoints);
        const tlg = tracks[tracks.length - 1][2];
        const tdcResult = this.runTDC(tdcCode, tracks, preData.sid);
        if (!tdcResult || !tdcResult.collect) {
            console.log('    [验证码] TDC 加密失败');
            return null;
        }
        console.log(`    collect 长度: ${tdcResult.collect.length}`);

        console.log('    [验证码] 7. 提交验证...');
        const verifyResult = await this.verify(preData.sess, ans, tdcResult.collect, tdcResult.eks, powAnswer, powCalcTime, tlg);
        if (verifyResult && verifyResult.errorCode === '0') {
            console.log('    [验证码] 验证成功！');
            return { ticket: verifyResult.ticket, randstr: verifyResult.randstr };
        }
        console.log(`    [验证码] 验证失败: ${verifyResult?.errMessage || JSON.stringify(verifyResult)}`);
        return null;
    }

    async prehandle() {
        const callbackName = '_aq_' + Math.floor(Math.random() * 1000000);
        const params = new URLSearchParams({
            aid: CAPTCHA_APP_ID, protocol: 'https', accver: '1', showtype: 'popup',
            ua: CAPTCHA_UA_B64, noheader: '0', fb: '1', aged: '0',
            enableAged: '0', enableDarkMode: '0', grayscale: '1',
            clientype: '1', cap_cd: '', uid: '', lang: 'zh-cn',
            entry_url: 'https://app.rainyun.com/account/reward/earn#',
            elder_captcha: '0', js: '/tcaptcha-frame.5bae14dd.js',
            login_appid: '', wb: '2', subsid: '1',
            callback: callbackName, sess: '',
        });
        const r = await httpsGet(`https://${CAPTCHA_DOMAIN}/cap_union_prehandle?${params}`, {
            'Referer': 'https://app.rainyun.com/',
            'Accept': '*/*',
            'Sec-Fetch-Dest': 'script',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
        });
        return extractJson(r.body.toString());
    }

    async downloadImage(imgPath) {
        const r = await httpsGet(`https://${CAPTCHA_DOMAIN}${imgPath}`, {
            'Referer': 'https://turing.captcha.gtimg.com/',
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        });
        return r.body;
    }

    async loadTDC(tdcPath) {
        const r = await httpsGet(`https://${CAPTCHA_DOMAIN}${tdcPath}`, {
            'Referer': 'https://turing.captcha.gtimg.com/',
        });
        return r.body.toString();
    }

    solvePoW(prefix, targetMd5) {
        const start = Date.now();
        for (let i = 0; i < 10000000; i++) {
            const hash = crypto.createHash('md5').update(prefix + i.toString()).digest('hex');
            if (hash === targetMd5) return { answer: prefix + i.toString(), calcTime: Date.now() - start };
        }
        return { answer: prefix + '0', calcTime: Date.now() - start };
    }

    runTDC(tdcCode, tracks, sid) {
        const dom = new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>', {
            url: `https://${CAPTCHA_DOMAIN}/`,
            runScripts: 'outside-only',
        });
        const w = dom.window;
        Object.defineProperty(w.navigator, 'userAgent', { value: CAPTCHA_UA, configurable: true });
        Object.defineProperty(w.navigator, 'platform', { value: 'Win32', configurable: true });
        Object.defineProperty(w.screen, 'width', { value: 1920, configurable: true });
        Object.defineProperty(w.screen, 'height', { value: 1080, configurable: true });
        w.console.log = () => {}; w.console.error = () => {}; w.console.warn = () => {};
        new (w.Function)(tdcCode).call(w);
        const TDC = w.TDC;
        if (!TDC) { console.log('    [验证码] TDC 初始化失败'); return null; }
        TDC.setData({
            ft: JSON.stringify({ ua: CAPTCHA_UA, platform: 'Win32', screen: '1920x1080', language: 'zh-CN', colorDepth: 24, timezone: -480 }),
            tracks, sid, t: Date.now(),
        });
        let collect = null;
        for (let i = 0; i < 10; i++) { collect = TDC.getData(true); if (collect && !collect.startsWith('Err')) break; }
        const info = TDC.getInfo ? TDC.getInfo() : {};
        let eks = null;
        for (const k of Object.keys(w)) {
            const v = w[k];
            if (typeof v === 'string' && v.length > 100 && /^[A-Za-z0-9+/=]+$/.test(v)) { eks = v; break; }
        }
        return { collect, eks, tokenid: info.tokenid || '' };
    }

    async verify(sess, ans, collect, eks, powAnswer, powCalcTime, tlg) {
        const r = await httpsPost(`https://${CAPTCHA_DOMAIN}/cap_union_new_verify`, {
            collect, tlg: String(tlg || ''), eks: eks || '',
            sess, ans, pow_answer: powAnswer, pow_calc_time: String(powCalcTime || 0),
        }, {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Origin': 'https://turing.captcha.gtimg.com',
            'Referer': 'https://turing.captcha.gtimg.com/',
        });
        return extractJson(r.body.toString());
    }

    async findCharsInBg(bgBuf, spriteBuf, insElemCfg, bgSize) {
        const spRaw = await sharp(spriteBuf).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
        const sData = spRaw.data;
        const sW = spRaw.info.width, sH = spRaw.info.height;

        const charWidth = Math.floor(sW / 3);
        const spAreas = [];
        for (let i = 0; i < 3; i++) {
            const x0 = i * charWidth;
            const x1 = (i === 2) ? sW : (i + 1) * charWidth;
            const w = x1 - x0;
            let count = 0;
            for (let y = 0; y < sH; y++)
                for (let x = 0; x < w; x++) {
                    const idx = (y * sW + x0 + x) * 4;
                    if (sData[idx] * 0.299 + sData[idx + 1] * 0.587 + sData[idx + 2] * 0.114 < 160) count++;
                }
            spAreas.push({ charIdx: i, area: count });
        }
        spAreas.sort((a, b) => b.area - a.area);
        console.log(`    [OCR] sprite areas: ${spAreas.map(t => `char${t.charIdx}(${t.area})`).join(', ')}`);

        const bgRaw = await sharp(bgBuf).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
        const bgData = bgRaw.data;
        const bgW = bgRaw.info.width, bgH = bgRaw.info.height;
        const bgGray = new Float32Array(bgW * bgH);
        for (let i = 0; i < bgW * bgH; i++)
            bgGray[i] = bgData[i * 4] * 0.299 + bgData[i * 4 + 1] * 0.587 + bgData[i * 4 + 2] * 0.114;

        const allDetections = [];
        for (const thresh of [40, 50, 60, 70, 80, 90, 100, 110]) {
            const bin = new Uint8Array(bgW * bgH);
            for (let i = 0; i < bgW * bgH; i++) bin[i] = bgGray[i] < thresh ? 1 : 0;
            const regions = this._findRegionsFast(bin, bgW, bgH, 100);
            for (const r of regions) {
                const w = r.maxX - r.minX + 1, h = r.maxY - r.minY + 1;
                const ar = w / h;
                if (ar > 0.25 && ar < 3.0 && w >= 12 && w <= 70 && h >= 12 && h <= 70) {
                    allDetections.push({
                        cx: (r.minX + r.maxX) / 2,
                        cy: (r.minY + r.maxY) / 2,
                        area: r.area,
                        w, h,
                        thresh
                    });
                }
            }
        }

        const MERGE_R = 25;
        const clusters = [];
        for (const det of allDetections) {
            let merged = false;
            for (const cl of clusters) {
                if (Math.hypot(det.cx - cl.sumX / cl.count, det.cy - cl.sumY / cl.count) < MERGE_R) {
                    cl.sumX += det.cx; cl.sumY += det.cy;
                    cl.sumArea += det.area;
                    cl.count++;
                    cl.threshSet.add(det.thresh);
                    cl.minX = Math.min(cl.minX, det.cx - det.w / 2);
                    cl.maxX = Math.max(cl.maxX, det.cx + det.w / 2);
                    cl.minY = Math.min(cl.minY, det.cy - det.h / 2);
                    cl.maxY = Math.max(cl.maxY, det.cy + det.h / 2);
                    merged = true;
                    break;
                }
            }
            if (!merged) {
                clusters.push({
                    sumX: det.cx, sumY: det.cy, sumArea: det.area,
                    count: 1, threshSet: new Set([det.thresh]),
                    minX: det.cx - det.w / 2, maxX: det.cx + det.w / 2,
                    minY: det.cy - det.h / 2, maxY: det.cy + det.h / 2,
                });
            }
        }

        for (const cl of clusters) {
            cl.cx = cl.sumX / cl.count;
            cl.cy = cl.sumY / cl.count;
            cl.avgArea = cl.sumArea / cl.count;
            cl.stability = cl.threshSet.size;
        }

        const minSpriteArea = Math.min(...spAreas.map(s => s.area));
        const maxSpriteArea = Math.max(...spAreas.map(s => s.area));
        const filtered = clusters.filter(cl =>
            cl.stability >= 3 && cl.avgArea >= minSpriteArea * 0.3 && cl.avgArea <= maxSpriteArea * 4
        ).sort((a, b) => b.stability - a.stability || b.avgArea - a.avgArea);

        console.log(`    [OCR] clusters: ${filtered.length}, top5: ${filtered.slice(0, 5).map(c => `(${Math.round(c.cx)},${Math.round(c.cy)} stab=${c.stability} area=${Math.round(c.avgArea)})`).join(' ')}`);

        if (filtered.length < 3) {
            console.log('    [OCR] 未找到3个字符区域');
            return [];
        }

        const selected = [];
        for (const cl of filtered) {
            if (selected.length >= 3) break;
            let tooClose = false;
            for (const s of selected) {
                if (Math.hypot(cl.cx - s.cx, cl.cy - s.cy) < 40) { tooClose = true; break; }
            }
            if (!tooClose) selected.push(cl);
        }

        if (selected.length < 3) {
            console.log('    [OCR] 候选区域太近，放宽距离限制');
            selected.length = 0;
            for (const cl of filtered) {
                if (selected.length >= 3) break;
                let tooClose = false;
                for (const s of selected) {
                    if (Math.hypot(cl.cx - s.cx, cl.cy - s.cy) < 20) { tooClose = true; break; }
                }
                if (!tooClose) selected.push(cl);
            }
        }

        if (selected.length < 3) {
            console.log('    [OCR] 无法找到3个分散的区域');
            return [];
        }

        selected.sort((a, b) => b.avgArea - a.avgArea);
        console.log(`    [OCR] selected: ${selected.map((c, i) => `${i}=(${Math.round(c.cx)},${Math.round(c.cy)} area=${Math.round(c.avgArea)} stab=${c.stability})`).join(' ')}`);

        const scaleX = bgSize[0] / bgW;
        const scaleY = bgSize[1] / bgH;
        const clickPoints = new Array(3);
        for (let rank = 0; rank < 3; rank++) {
            const spInfo = spAreas[rank];
            const bgInfo = selected[rank];
            clickPoints[spInfo.charIdx] = {
                imgX: Math.round(bgInfo.cx), imgY: Math.round(bgInfo.cy),
                spriteX: Math.round(bgInfo.cx * scaleX),
                spriteY: Math.round(bgInfo.cy * scaleY),
                confidence: bgInfo.stability / 8,
            };
        }

        for (let i = 0; i < 3; i++) {
            const p = clickPoints[i];
            console.log(`    [OCR] 字符 ${i + 1}: (${p.imgX},${p.imgY}) conf=${p.confidence.toFixed(2)}`);
        }
        return clickPoints;
    }

    _findRegionsFast(binary, w, h, minArea) {
        const visited = new Uint8Array(w * h);
        const regions = [];
        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                if (binary[y * w + x] && !visited[y * w + x]) {
                    const stack = [[x, y]];
                    let minX = x, maxX = x, minY = y, maxY = y, area = 0;
                    while (stack.length > 0) {
                        const [cx, cy] = stack.pop();
                        if (cx < 0 || cx >= w || cy < 0 || cy >= h) continue;
                        const idx = cy * w + cx;
                        if (visited[idx] || !binary[idx]) continue;
                        visited[idx] = 1; area++;
                        if (cx < minX) minX = cx; if (cx > maxX) maxX = cx;
                        if (cy < minY) minY = cy; if (cy > maxY) maxY = cy;
                        stack.push([cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]);
                    }
                    if (area >= minArea) regions.push({ minX, maxX, minY, maxY, area });
                }
            }
        }
        return regions;
    }

    generateClickTracks(points) {
        const tracks = [];
        let t = 0;
        tracks.push([0, 0, t]);
        t += 800 + Math.floor(Math.random() * 700);
        for (const pt of points) {
            const px = pt.imgX, py = pt.imgY;
            const steps = 8 + Math.floor(Math.random() * 8);
            const dx = px / steps, dy = py / steps;
            for (let s = 0; s < steps; s++) {
                const tx = Math.floor(dx * (s + 1) + (Math.random() * 6 - 3));
                const ty = Math.floor(dy * (s + 1) + (Math.random() * 6 - 3));
                t += 15 + Math.floor(Math.random() * 20);
                tracks.push([tx, ty, t]);
            }
            tracks.push([px, py, t + 5 + Math.floor(Math.random() * 10)]);
            t += 100 + Math.floor(Math.random() * 200);
        }
        return tracks;
    }
}

let notify;

function loadNotify() {
    try {
        const notifyPath = path.join(process.env.QL_DIR || '/ql/data', 'scripts', 'sendNotify.js');
        if (fs.existsSync(notifyPath)) {
            delete require.cache[require.resolve(notifyPath)];
            notify = require(notifyPath);
            return;
        }
    } catch (e) {}
    try {
        notify = require('./sendNotify');
        return;
    } catch (e) {}
    notify = null;
}

async function sendNotifyMsg(title, content) {
    if (notify && typeof notify.sendNotify === 'function') {
        try { await notify.sendNotify(title, content); } catch (e) {}
    }
}

function loadAccountData() {
    try {
        if (fs.existsSync(DATA_FILE)) {
            return JSON.parse(fs.readFileSync(DATA_FILE, 'utf-8'));
        }
    } catch (e) {}
    return {};
}

function saveAccountData(data) {
    try {
        fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2), 'utf-8');
    } catch (e) {}
}

function parseAccounts() {
    const raw = process.env.RAINYUN_ACCOUNTS || process.env.rainyun || '';
    if (!raw.trim()) return [];

    const accounts = [];
    const parts = raw.split(/[@\n]/).map(s => s.trim()).filter(Boolean);

    for (const part of parts) {
        const sepIndex = part.indexOf('#');
        if (sepIndex === -1) continue;
        const username = part.substring(0, sepIndex).trim();
        const password = part.substring(sepIndex + 1).trim();
        if (username && password) {
            accounts.push({ username, password });
        }
    }

    return accounts;
}

async function processAccount(account, index, total) {
    const logs = [];
    const log = (msg) => { console.log(msg); logs.push(msg); };

    log(`\n${'='.repeat(50)}`);
    log(`[账号 ${index + 1}/${total}] 开始处理: ${account.username}`);
    log('='.repeat(50));

    const allData = loadAccountData();
    const accountKey = account.username;

    const client = new RainyunClient(account.username, account.password);
    if (!await client.login()) {
        allData[accountKey] = { ...(allData[accountKey] || {}), lastResult: '登录失败', lastTime: new Date().toISOString() };
        saveAccountData(allData);
        log(`[账号 ${account.username}] 登录失败`);
        return { success: false, username: account.username, signResult: '登录失败', logs };
    }

    log();
    const userInfo = await client.getUserInfo();
    log();

    const tasks = await client.getTasks();
    log();

    let signResult = '未处理';
    let pointsBefore = userInfo?.Points || null;
    let pointsAfter = null;

    let dailyTask = null;
    if (tasks) {
        for (const t of tasks) {
            if (t.Name === '每日签到') { dailyTask = t; break; }
        }
    }

    if (dailyTask) {
        if (dailyTask.Status === 2) {
            signResult = '今日已签到';
            log('[签到] 今日已签到，无需重复签到');
        } else {
            const ok = await client.signWithoutCaptcha();
            signResult = ok ? '签到成功' : '签到失败';
        }
    } else {
        signResult = '未找到签到任务';
        log('[签到] 未找到每日签到任务');
    }

    log();
    const userInfoAfter = await client.getUserInfo();
    if (userInfoAfter) pointsAfter = userInfoAfter.Points;

    allData[accountKey] = {
        username: account.username,
        email: userInfo?.Email || '',
        phone: userInfo?.Phone || '',
        vipLevel: userInfo?.VIP?.Title || '无',
        points: pointsAfter,
        pointsBefore,
        signResult,
        lastTime: new Date().toISOString(),
        lastLoginArea: userInfo?.LastLoginArea || '',
        vipLevelNum: userInfo?.VipLevel || 0,
    };
    saveAccountData(allData);

    log(`\n[账号 ${account.username}] 处理完成: ${signResult}`);
    if (pointsBefore !== null && pointsAfter !== null) {
        const diff = pointsAfter - pointsBefore;
        if (diff > 0) log(`[积分变化] ${pointsBefore} -> ${pointsAfter} (+${diff})`);
        else log(`[积分变化] ${pointsAfter}`);
    }

    return {
        success: signResult === '签到成功' || signResult === '今日已签到',
        username: account.username,
        signResult,
        points: pointsAfter,
        pointsBefore,
        email: userInfo?.Email || '',
        phone: userInfo?.Phone || '',
        vipLevel: userInfo?.VIP?.Title || '无',
        lastLoginArea: userInfo?.LastLoginArea || '',
        logs,
    };
}

async function main() {
    console.log('========================================');
    console.log('  雨云自动签到 - 青龙面板版');
    console.log('========================================');
    console.log(`运行时间: ${new Date().toLocaleString('zh-CN')}`);

    loadNotify();

    const accounts = parseAccounts();
    if (accounts.length === 0) {
        const msg = '未找到任何账号配置\n\n环境变量: RAINYUN_ACCOUNTS\n格式: 账号#密码\n多账号用 @ 或换行分隔\n示例: 用户1#密码1@用户2#密码2';
        console.log(`\n[错误] ${msg}`);
        await sendNotifyMsg('雨云签到 - 配置错误', msg);
        return;
    }

    console.log(`\n[配置] 共检测到 ${accounts.length} 个账号`);

    const results = [];
    for (let i = 0; i < accounts.length; i++) {
        if (i > 0) await sleep(3000 + Math.random() * 5000);
        try {
            const result = await processAccount(accounts[i], i, accounts.length);
            results.push(result);
        } catch (e) {
            console.log(`[错误] 账号 ${accounts[i].username} 处理异常: ${e.message}`);
            results.push({ success: false, username: accounts[i].username, signResult: '异常: ' + e.message, logs: [`[错误] ${e.message}`] });
        }
    }

    let summary = '';
    for (let i = 0; i < results.length; i++) {
        const r = results[i];
        const icon = r.success ? '✅' : '❌';
        summary += `【账号 ${i + 1}】${icon} ${r.username}\n`;
        summary += `  签到: ${r.signResult}\n`;
        if (r.vipLevel) summary += `  会员: ${r.vipLevel}\n`;
        if (r.points !== null) {
            summary += `  总积分: ${r.points}`;
            if (r.pointsBefore !== null) {
                const diff = r.points - r.pointsBefore;
                if (diff > 0) summary += ` (+${diff})`;
            }
            summary += '\n';
        }
        if (r.email) summary += `  邮箱: ${r.email}\n`;
        if (r.phone) summary += `  手机: ${r.phone}\n`;
        if (r.lastLoginArea) summary += `  登录地: ${r.lastLoginArea}\n`;
        summary += '\n';
        console.log(`${icon} ${r.username}: ${r.signResult}${r.points !== null ? ` (积分: ${r.points})` : ''}`);
    }
    const successCount = results.filter(r => r.success).length;
    const tail = `总计: ${results.length} 个账号, 成功: ${successCount}, 失败: ${results.length - successCount}`;
    summary += tail;
    console.log(tail);

    await sendNotifyMsg('雨云签到', summary);
}

main().catch(e => console.error('[错误] 致命错误:', e));
