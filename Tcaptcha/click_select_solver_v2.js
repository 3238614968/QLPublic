/**
 * 腾讯防水墙点选验证码破解器 v2 (ClickSelectCaptchaSolverV2)
 *
 * 基于 v1 (click_select_solver.js) 的改进版
 *
 * 改进 A: 面积一致性评分
 *   - 不只用"被多少阈值检测到"作为稳定性
 *   - 新增 per-threshold 面积记录 + 面积变异系数(CV) + 线性单调性(R²拟合)
 *   - 真实字符: 面积随阈值增大而递增且近似线性 → R²高、CV低
 *   - 噪声: 面积随机波动 → R²低、CV高
 *
 * 改进 D: 多候选投票
 *   - 保留 top5 候选区域（而非 top3）
 *   - 生成 C(5,3)=10 种候选组合，按综合评分排序
 *   - 同一 session 内多次 verify（不重新 prehandle/下载图片）
 *   - 每次尝试用新的轨迹 + TDC 加密
 *
 * 依赖: npm install jsdom sharp
 */

const https = require('https');
const crypto = require('crypto');
const zlib = require('zlib');
const { JSDOM } = require('jsdom');
const sharp = require('sharp');

const CAPTCHA_DOMAIN = 'turing.captcha.qcloud.com';
const CAPTCHA_APP_ID = '2039519451';
const CAPTCHA_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1 Edg/146.0.0.0';
const CAPTCHA_UA_B64 = Buffer.from(CAPTCHA_UA).toString('base64');

const THRESHOLDS = [40, 50, 60, 70, 80, 90, 100, 110];
const MERGE_RADIUS = 25;
const MIN_REGION_SIZE = 100;
const MIN_STABILITY = 3;
const TOP_CANDIDATES = 5;
const MAX_VERIFY_PER_SESSION = 6;

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

function linearR2(xs, ys) {
    const n = xs.length;
    if (n < 2) return 0;
    let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0, sumY2 = 0;
    for (let i = 0; i < n; i++) {
        sumX += xs[i]; sumY += ys[i];
        sumXY += xs[i] * ys[i];
        sumX2 += xs[i] * xs[i];
        sumY2 += ys[i] * ys[i];
    }
    const denom = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
    if (denom === 0) return 0;
    return (n * sumXY - sumX * sumY) / denom;
}

function coefficientOfVariation(values) {
    if (values.length === 0) return 1;
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    if (mean === 0) return 1;
    const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length;
    return Math.sqrt(variance) / mean;
}

function combinations(arr, k) {
    if (k === 0) return [[]];
    if (arr.length < k) return [];
    const [first, ...rest] = arr;
    const withFirst = combinations(rest, k - 1).map(c => [first, ...c]);
    const withoutFirst = combinations(rest, k);
    return [...withFirst, ...withoutFirst];
}

class ClickSelectCaptchaSolverV2 {
    async solve() {
        console.log('[1/6] 获取验证码数据...');
        const preData = await this.prehandle();
        if (!preData) return null;

        const dynShow = preData.data?.dyn_show_info;
        const commCfg = preData.data?.comm_captcha_cfg;
        const powCfg = commCfg?.pow_cfg;
        const bgSize = dynShow?.bg_elem_cfg?.size_2d || [672, 480];

        console.log(`    sid: ${preData.sid}, subcapclass: ${preData.subcapclass}`);

        console.log('[2/6] 下载验证码图片...');
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
        if (!bgBuf || !spriteBuf) { console.log('图片下载失败'); return null; }

        console.log('[3/6] 多阈值检测 + 面积一致性评分...');
        const { candidates, spAreas, bgW, bgH } = await this.findCandidates(bgBuf, spriteBuf, bgSize);
        if (candidates.length < 3) { console.log('候选区域不足3个'); return null; }

        const combos = combinations(candidates, 3);
        const scoredCombos = combos.map(combo => {
            const totalScore = combo.reduce((s, c) => s + c.score, 0);
            const minSep = this._minSeparation(combo);
            return { combo, score: totalScore + minSep * 0.01 };
        }).sort((a, b) => b.score - a.score);

        console.log(`    候选区域: ${candidates.length}, 组合数: ${scoredCombos.length}`);
        console.log(`    Top3组合评分: ${scoredCombos.slice(0, 3).map(s => s.score.toFixed(1)).join(', ')}`);

        console.log('[4/6] 加载 TDC + 解 PoW...');
        const tdcPath = commCfg?.tdc_path || '/tdc.js';
        const tdcCode = await this.loadTDC(tdcPath);
        let powAnswer = '0', powCalcTime = 0;
        if (powCfg) {
            const r = this.solvePoW(powCfg.prefix, powCfg.md5);
            powAnswer = r.answer;
            powCalcTime = r.calcTime;
        }

        console.log('[5/6] 多候选投票验证...');
        const maxAttempts = Math.min(MAX_VERIFY_PER_SESSION, scoredCombos.length);
        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            const { combo, score } = scoredCombos[attempt];
            const clickPoints = this._buildClickPoints(combo, spAreas, bgSize, bgW, bgH);
            console.log(`  尝试 ${attempt + 1}/${maxAttempts} (评分=${score.toFixed(1)})`);

            console.log('[6/6] 生成轨迹 + TDC 加密 + 提交验证...');
            const tracks = this.generateClickTracks(clickPoints);
            const tlg = tracks[tracks.length - 1][2];
            const tdcResult = this.runTDC(tdcCode, tracks, preData.sid);
            if (!tdcResult || !tdcResult.collect) {
                console.log('    TDC 加密失败，跳过');
                continue;
            }

            const ans = JSON.stringify(clickPoints.map((p, i) => ({
                elem_id: i + 1,
                type: 'DynAnswerType_POS',
                data: `${p.spriteX},${p.spriteY}`,
            })));
            console.log(`    ans: ${ans}`);

            const verifyResult = await this.verify(preData.sess, ans, tdcResult.collect, tdcResult.eks, powAnswer, powCalcTime, tlg);
            if (verifyResult && verifyResult.errorCode === '0') {
                console.log(`    验证成功! (第${attempt + 1}次尝试, 评分=${score.toFixed(1)})`);
                return { ticket: verifyResult.ticket, randstr: verifyResult.randstr };
            }
            console.log(`    失败: ${verifyResult?.errMessage || 'unknown'}`);
            await sleep(800);
        }

        console.log(`    所有 ${maxAttempts} 次尝试均失败`);
        return null;
    }

    async findCandidates(bgBuf, spriteBuf, bgSize) {
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
        console.log(`    sprite areas: ${spAreas.map(t => `char${t.charIdx}(${t.area})`).join(', ')}`);

        const bgRaw = await sharp(bgBuf).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
        const bgData = bgRaw.data;
        const bgW = bgRaw.info.width, bgH = bgRaw.info.height;
        const bgGray = new Float32Array(bgW * bgH);
        for (let i = 0; i < bgW * bgH; i++)
            bgGray[i] = bgData[i * 4] * 0.299 + bgData[i * 4 + 1] * 0.587 + bgData[i * 4 + 2] * 0.114;

        const perThreshRegions = {};
        for (const thresh of THRESHOLDS) {
            const bin = new Uint8Array(bgW * bgH);
            for (let i = 0; i < bgW * bgH; i++) bin[i] = bgGray[i] < thresh ? 1 : 0;
            perThreshRegions[thresh] = this._findRegionsFast(bin, bgW, bgH, MIN_REGION_SIZE);
        }

        const allDetections = [];
        for (const thresh of THRESHOLDS) {
            for (const r of perThreshRegions[thresh]) {
                const w = r.maxX - r.minX + 1, h = r.maxY - r.minY + 1;
                const ar = w / h;
                if (ar > 0.2 && ar < 4.0 && w >= 10 && w <= 80 && h >= 10 && h <= 80) {
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

        const clusters = [];
        for (const det of allDetections) {
            let merged = false;
            for (const cl of clusters) {
                if (Math.hypot(det.cx - cl.sumX / cl.count, det.cy - cl.sumY / cl.count) < MERGE_RADIUS) {
                    cl.sumX += det.cx; cl.sumY += det.cy;
                    cl.sumArea += det.area;
                    cl.count++;
                    cl.threshSet.add(det.thresh);
                    cl.areasByThresh[det.thresh] = (cl.areasByThresh[det.thresh] || 0) + det.area;
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
                    areasByThresh: { [det.thresh]: det.area },
                    minX: det.cx - det.w / 2, maxX: det.cx + det.w / 2,
                    minY: det.cy - det.h / 2, maxY: det.cy + det.h / 2,
                });
            }
        }

        const minSpriteArea = Math.min(...spAreas.map(s => s.area));
        const maxSpriteArea = Math.max(...spAreas.map(s => s.area));

        for (const cl of clusters) {
            cl.cx = cl.sumX / cl.count;
            cl.cy = cl.sumY / cl.count;
            cl.avgArea = cl.sumArea / cl.count;
            cl.stability = cl.threshSet.size;

            const sortedThresh = [...cl.threshSet].sort((a, b) => a - b);
            const areas = sortedThresh.map(t => cl.areasByThresh[t]);
            const cv = coefficientOfVariation(areas);
            const r2 = linearR2(sortedThresh, areas);

            cl.cv = cv;
            cl.r2 = r2;
            cl.areaConsistency = cl.stability >= 3 ? (r2 * 0.5 + (1 - cv) * 0.5) : 0;

            const areaRatio = cl.avgArea / ((minSpriteArea + maxSpriteArea) / 2);
            const areaBonus = (areaRatio >= 0.4 && areaRatio <= 2.5) ? 1.0 : (areaRatio >= 0.2 && areaRatio <= 4.0) ? 0.3 : -2.0;

            cl.score = cl.stability * 3 + cl.areaConsistency * 10 + areaBonus;
        }

        const filtered = clusters.filter(cl =>
            cl.stability >= MIN_STABILITY && cl.avgArea >= minSpriteArea * 0.2 && cl.avgArea <= maxSpriteArea * 5
        ).sort((a, b) => b.score - a.score);

        const candidates = filtered.slice(0, TOP_CANDIDATES);
        console.log(`    总聚类: ${clusters.length}, 过滤后: ${filtered.length}, 取top${TOP_CANDIDATES}`);
        for (let i = 0; i < candidates.length; i++) {
            const c = candidates[i];
            console.log(`    [${i}] (${Math.round(c.cx)},${Math.round(c.cy)}) stab=${c.stability} R²=${c.r2.toFixed(2)} CV=${c.cv.toFixed(2)} area=${Math.round(c.avgArea)} score=${c.score.toFixed(1)}`);
        }

        return { candidates, spAreas, bgW, bgH };
    }

    _minSeparation(combo) {
        let minSep = Infinity;
        for (let i = 0; i < combo.length; i++)
            for (let j = i + 1; j < combo.length; j++)
                minSep = Math.min(minSep, Math.hypot(combo[i].cx - combo[j].cx, combo[i].cy - combo[j].cy));
        return minSep;
    }

    _buildClickPoints(combo, spAreas, bgSize, bgW, bgH) {
        combo.sort((a, b) => b.avgArea - a.avgArea);
        const scaleX = bgSize[0] / bgW;
        const scaleY = bgSize[1] / bgH;
        const clickPoints = new Array(3);
        for (let rank = 0; rank < 3; rank++) {
            const bgInfo = combo[rank];
            clickPoints[rank] = {
                imgX: Math.round(bgInfo.cx),
                imgY: Math.round(bgInfo.cy),
                spriteX: Math.round(bgInfo.cx * scaleX),
                spriteY: Math.round(bgInfo.cy * scaleY),
                confidence: bgInfo.score / 40,
            };
        }
        return clickPoints;
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
        if (!TDC) { console.log('    TDC 初始化失败'); return null; }
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

async function test() {
    console.log('=== 腾讯点选验证码 v2 独立测试 ===\n');
    const solver = new ClickSelectCaptchaSolverV2();
    const maxRetries = 3;
    for (let i = 1; i <= maxRetries; i++) {
        console.log(`\n${'='.repeat(50)}`);
        console.log(`第 ${i}/${maxRetries} 次完整会话`);
        console.log('='.repeat(50));
        const start = Date.now();
        const result = await solver.solve();
        const elapsed = ((Date.now() - start) / 1000).toFixed(1);
        if (result) {
            console.log(`\n成功! 耗时=${elapsed}s, ticket: ${result.ticket.substring(0, 30)}...`);
            return { success: true, attempt: i, time: elapsed };
        }
        console.log(`\n会话失败, 耗时=${elapsed}s`);
        if (i < maxRetries) await sleep(3000);
    }
    console.log('\n所有会话均失败');
    return { success: false };
}

async function benchmark(rounds = 10) {
    console.log(`=== 腾讯点选验证码 v2 压力测试 (${rounds}轮) ===\n`);
    const solver = new ClickSelectCaptchaSolverV2();
    let successes = 0;
    const results = [];
    for (let i = 1; i <= rounds; i++) {
        console.log(`\n${'#'.repeat(50)}`);
        console.log(`轮次 ${i}/${rounds}`);
        console.log('#'.repeat(50));
        const start = Date.now();
        const result = await solver.solve();
        const elapsed = ((Date.now() - start) / 1000).toFixed(1);
        if (result) {
            successes++;
            console.log(`\n>>> 成功! 耗时=${elapsed}s`);
            results.push({ round: i, ok: true, time: elapsed });
        } else {
            console.log(`\n>>> 失败, 耗时=${elapsed}s`);
            results.push({ round: i, ok: false, time: elapsed });
        }
        if (i < rounds) await sleep(3000);
    }
    console.log(`\n${'='.repeat(50)}`);
    console.log(`测试完成: ${successes}/${rounds} 成功 (${(successes / rounds * 100).toFixed(0)}%)`);
    for (const r of results) {
        console.log(`  轮次${r.round}: ${r.ok ? '成功' : '失败'} (${r.time}s)`);
    }
    return { success: successes, total: rounds, rate: successes / rounds };
}

if (require.main === module) {
    const mode = process.argv[2] || 'test';
    if (mode === 'bench' || mode === 'benchmark') {
        const rounds = parseInt(process.argv[3]) || 10;
        benchmark(rounds).catch(e => console.error('错误:', e));
    } else {
        test().catch(e => console.error('错误:', e));
    }
}

module.exports = { ClickSelectCaptchaSolverV2 };
