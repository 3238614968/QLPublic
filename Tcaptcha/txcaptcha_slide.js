/**
 * 腾讯防水墙纯算法验证码
 *
 * 验证流程:
 * 1. cap_union_prehandle -> 获取 sid, sess, 图片URL, PoW配置, fg_elem_list
 * 2. 下载背景图(sprite)和精灵图
 * 3. 从精灵图裁剪缺口模板，在背景图中模板匹配找缺口位置
 * 4. 计算 PoW nonce
 * 5. 生成人类滑动轨迹
 * 6. TDC 加密 collect
 * 7. cap_union_new_verify (POST) -> 获取 ticket, randstr
 */

// 设置 UTF-8 编码以解决 Windows 终端中文乱码问题
if (process.platform === 'win32') {
    try {
        require('child_process').execSync('chcp 65001 > nul');
    } catch (e) {}
    try {
        process.stdout.setEncoding('utf8');
        process.stderr.setEncoding('utf8');
    } catch (e) {}
}

const axios = require('axios');
const crypto = require('crypto');
const https = require('https');
const http = require('http');
const { JSDOM } = require('jsdom');
const sharp = require('sharp');
const fs = require('fs');

const httpsAgent = new https.Agent({ rejectUnauthorized: false });

// 代理配置
const PROXY_API_URL = '';
let currentProxy = null;
let proxyExpireTime = 0;

// 获取代理
async function getProxy() {
    const now = Date.now();
    if (currentProxy && now < proxyExpireTime) {
        return currentProxy;
    }
    
    try {
        console.log('[代理] 正在获取新代理...');
        const response = await axios.get(PROXY_API_URL, { timeout: 10000 });
        const proxy = response.data.trim();
        if (proxy && proxy.includes(':')) {
            currentProxy = proxy;
            proxyExpireTime = now + 55000; // 55秒后过期（预留5秒缓冲）
            console.log(`[代理] 获取成功: ${proxy}`);
            return proxy;
        }
    } catch (e) {
        console.log('[代理] 获取失败:', e.message);
    }
    return null;
}

// 创建带代理的axios配置
async function createAxiosConfig(baseConfig = {}) {
    const proxy = await getProxy();
    const config = { ...baseConfig };
    
    if (proxy) {
        const [host, port] = proxy.split(':');
        config.proxy = {
            protocol: 'http',
            host: host,
            port: parseInt(port),
        };
    }
    
    return config;
}
const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1 Edg/146.0.0.0';
const UA_B64 = Buffer.from(UA).toString('base64');

class TxCaptcha {
    constructor(appId = '190094633', useProxy = false) {
        this.appId = appId;
        this.domain = 'turing.captcha.qcloud.com';
        this.cookies = {};
        this.tdcCode = null;
        this.useProxy = useProxy;
    }

    // 获取请求配置
    async _getRequestConfig(baseConfig = {}) {
        const config = { ...baseConfig, httpsAgent, timeout: 30000 };
        
        if (this.useProxy) {
            const proxy = await getProxy();
            if (proxy) {
                const [host, port] = proxy.split(':');
                config.proxy = {
                    protocol: 'http',
                    host: host,
                    port: parseInt(port),
                };
                config.timeout = 15000; // 代理模式下缩短超时时间
            }
        }
        
        return config;
    }

    async loadTDC(tdcPath) {
        if (this.tdcCode) return;
        try {
            const url = `https://${this.domain}${tdcPath}`;
            const r = await axios.get(url, {
                headers: { 'User-Agent': UA, 'Referer': 'https://zhenxun.cn/' },
                httpsAgent, timeout: 10000,
            });
            this.tdcCode = r.data;
        } catch (e) {
            this.tdcCode = fs.readFileSync('tec,js', 'utf8');
        }
    }

    runTDC(tracks, sid) {
        const dom = new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>', {
            url: `https://${this.domain}/`,
            runScripts: 'outside-only',
        });
        const w = dom.window;
        Object.defineProperty(w.navigator, 'userAgent', { value: UA, configurable: true });
        Object.defineProperty(w.navigator, 'platform', { value: 'Win32', configurable: true });
        Object.defineProperty(w.screen, 'width', { value: 1920, configurable: true });
        Object.defineProperty(w.screen, 'height', { value: 1080, configurable: true });
        w.console.log = () => {};
        w.console.error = () => {};
        w.console.warn = () => {};

        new (w.Function)(this.tdcCode).call(w);

        const TDC = w.TDC;
        if (!TDC) throw new Error('TDC init failed');

        TDC.setData({
            ft: JSON.stringify({ ua: UA, platform: 'Win32', screen: '1920x1080' }),
            tracks: tracks,
            sid: sid,
        });

        let collect = null;
        for (let i = 0; i < 10; i++) {
            collect = TDC.getData(true);
            if (collect && !collect.startsWith('Err')) break;
        }
        const info = TDC.getInfo ? TDC.getInfo() : {};
        let eks = null;
        for (const k of Object.keys(w)) {
            const v = w[k];
            if (typeof v === 'string' && v.length > 100 && /^[A-Za-z0-9+/=]+$/.test(v)) {
                eks = v;
                break;
            }
        }
        return { collect, eks, tokenid: info.tokenid || '' };
    }

    async prehandle() {
        const url = `https://${this.domain}/cap_union_prehandle`;
        const callbackName = '_aq_' + Math.floor(Math.random() * 1000000);
        const params = {
            aid: this.appId,
            protocol: 'https',
            accver: '1',
            showtype: 'popup',
            ua: UA_B64,
            noheader: '1',
            fb: '1',
            aged: '0',
            enableAged: '0',
            enableDarkMode: '0',
            grayscale: '1',
            clientype: '2',
            cap_cd: '',
            uid: '',
            lang: 'zh-cn',
            entry_url: 'https://zhenxun.cn/login.htm',
            elder_captcha: '0',
            js: '/tcaptcha-frame.5bae14dd.js',
            login_appid: '',
            wb: '1',
            subsid: '1',
            callback: callbackName,
            sess: '',
        };

        const config = await this._getRequestConfig({
            params,
            headers: { 'User-Agent': UA, 'Referer': 'https://zhenxun.cn/', 'Accept': '*/*' },
        });
        const r = await axios.get(url, config);
        this._saveCookies(r);

        const text = typeof r.data === 'string' ? r.data : JSON.stringify(r.data);
        const jsonMatch = text.match(new RegExp(callbackName + '\\((\\{[\\s\\S]*\\})\\)')) ||
                          text.match(/\{[\s\S]*\}/);
        if (!jsonMatch) throw new Error('prehandle: invalid response: ' + text.substring(0, 200));
        return JSON.parse(jsonMatch[1] || jsonMatch[0]);
    }

    async downloadImage(imgPath) {
        const url = `https://${this.domain}${imgPath}`;
        const config = await this._getRequestConfig({
            headers: {
                'User-Agent': UA,
                'Referer': 'https://turing.captcha.gtimg.com/',
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            },
            responseType: 'arraybuffer',
        });
        const r = await axios.get(url, config);
        return Buffer.from(r.data);
    }

    solvePoW(prefix, targetMd5) {
        const start = Date.now();
        for (let i = 0; i < 10000000; i++) {
            const nonce = i.toString();
            const hash = crypto.createHash('md5').update(prefix + nonce).digest('hex');
            if (hash === targetMd5) {
                return { answer: prefix + nonce, calcTime: Date.now() - start };
            }
        }
        return { answer: prefix + '0', calcTime: Date.now() - start };
    }

    generateTracks(distance) {
        const tracks = [];
        let current = 0;
        let time = 0;
        tracks.push([0, 0, 0]);

        for (let i = 0; i < 3; i++) {
            time += Math.floor(Math.random() * 15 + 10);
            current += Math.floor(Math.random() * 2 + 1);
            tracks.push([current, Math.floor(Math.random() * 2 - 1), time]);
        }
        while (current < distance * 0.55) {
            time += Math.floor(Math.random() * 20 + 15);
            current += Math.floor(Math.random() * 6 + 4);
            if (current > distance) current = distance;
            tracks.push([current, Math.floor(Math.random() * 3 - 1), time]);
        }
        while (current < distance * 0.85) {
            time += Math.floor(Math.random() * 25 + 15);
            current += Math.floor(Math.random() * 4 + 2);
            if (current > distance) current = distance;
            tracks.push([current, Math.floor(Math.random() * 3 - 1), time]);
        }
        while (current < distance) {
            time += Math.floor(Math.random() * 40 + 25);
            current += Math.floor(Math.random() * 2 + 1);
            if (current > distance) current = distance;
            tracks.push([current, Math.floor(Math.random() * 2 - 1), time]);
        }
        time += Math.floor(Math.random() * 50 + 30);
        tracks.push([distance, 0, time]);
        return tracks;
    }

    async verify(sess, ans, collect, eks, powAnswer, powCalcTime, tlg) {
        const url = `https://${this.domain}/cap_union_new_verify`;
        const payload = {
            collect: collect,
            tlg: tlg || '1688',
            eks: eks || '',
            sess: sess,
            ans: ans,
            pow_answer: powAnswer,
            pow_calc_time: powCalcTime.toString(),
        };

        const config = await this._getRequestConfig({
            headers: {
                'User-Agent': UA,
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://turing.captcha.gtimg.com',
                'Referer': 'https://turing.captcha.gtimg.com/',
            },
        });
        const r = await axios.post(url, new URLSearchParams(payload).toString(), config);

        const text = typeof r.data === 'string' ? r.data : JSON.stringify(r.data);
        console.log('    verify 响应:', text.substring(0, 300));
        try {
            return JSON.parse(text.match(/\{[\s\S]*\}/)?.[0] || text);
        } catch {
            return null;
        }
    }

    computeGradientMap(data, w, h, channels) {
        const grad = new Float32Array(w * h);
        for (let y = 1; y < h - 1; y++) {
            for (let x = 1; x < w - 1; x++) {
                const idx = (y * w + x) * channels;
                const idxL = (y * w + x - 1) * channels;
                const idxR = (y * w + x + 1) * channels;
                const idxU = ((y - 1) * w + x) * channels;
                const idxD = ((y + 1) * w + x) * channels;
                let gx = 0, gy = 0;
                for (let c = 0; c < channels; c++) {
                    gx += Math.abs(data[idxR + c] - data[idxL + c]);
                    gy += Math.abs(data[idxD + c] - data[idxU + c]);
                }
                gx /= channels;
                gy /= channels;
                grad[y * w + x] = Math.sqrt(gx * gx + gy * gy);
            }
        }
        return grad;
    }

    computeAlphaEdgeMask(tData, tW, tH) {
        const mask = new Float32Array(tW * tH);
        for (let y = 1; y < tH - 1; y++) {
            for (let x = 1; x < tW - 1; x++) {
                const a = tData[(y * tW + x) * 4 + 3];
                const aL = tData[(y * tW + x - 1) * 4 + 3];
                const aR = tData[(y * tW + x + 1) * 4 + 3];
                const aU = tData[((y - 1) * tW + x) * 4 + 3];
                const aD = tData[((y + 1) * tW + x) * 4 + 3];
                if (a > 128 && (aL < 80 || aR < 80 || aU < 80 || aD < 80)) {
                    mask[y * tW + x] = 1.0;
                }
            }
        }
        return mask;
    }

    scoreEdgeMatch(edgeMask, tW, tH, bgGrad, bgW, bgH, x, y) {
        let sum = 0;
        let count = 0;
        const step = 2;
        for (let ty = 1; ty < tH - 1; ty += step) {
            for (let tx = 1; tx < tW - 1; tx += step) {
                if (edgeMask[ty * tW + tx] > 0.5) {
                    const by = y + ty;
                    const bx = x + tx;
                    if (bx >= 0 && bx < bgW && by >= 0 && by < bgH) {
                        sum += bgGrad[by * bgW + bx];
                        count++;
                    }
                }
            }
        }
        return count > 5 ? sum / count : 0;
    }

    calculateMatchScore(tData, tW, tH, bgData, bgW, bgH, x, y) {
        let sumT = 0, sumB = 0;
        let sumT2 = 0, sumB2 = 0;
        let sumTB = 0;
        let sadSum = 0;
        let count = 0;
        const step = 2;

        for (let ty = 0; ty < tH; ty += step) {
            for (let tx = 0; tx < tW; tx += step) {
                const tIdx = (ty * tW + tx) * 4;
                const alpha = tData[tIdx + 3];
                if (alpha > 80) {
                    const bgIdx = ((y + ty) * bgW + (x + tx)) * 4;
                    if (bgIdx >= 0 && bgIdx + 3 < bgData.length) {
                        const tGray = tData[tIdx] * 0.299 + tData[tIdx + 1] * 0.587 + tData[tIdx + 2] * 0.114;
                        const bGray = bgData[bgIdx] * 0.299 + bgData[bgIdx + 1] * 0.587 + bgData[bgIdx + 2] * 0.114;
                        sumT += tGray;
                        sumB += bGray;
                        sumT2 += tGray * tGray;
                        sumB2 += bGray * bGray;
                        sumTB += tGray * bGray;
                        sadSum += Math.abs(tGray - bGray);
                        count++;
                    }
                }
            }
        }

        if (count < 10) return { ncc: -1, sad: Infinity };

        const meanT = sumT / count;
        const meanB = sumB / count;
        const varT = sumT2 / count - meanT * meanT;
        const varB = sumB2 / count - meanB * meanB;

        let ncc = -1;
        if (varT >= 1 && varB >= 1) {
            const cov = sumTB / count - meanT * meanB;
            ncc = cov / (Math.sqrt(varT) * Math.sqrt(varB));
        }

        return { ncc, sad: sadSum / count };
    }

    async detectGap(bgBuf, spriteBuf, fgElem) {
        if (!fgElem || !fgElem.sprite_pos || !fgElem.size_2d) {
            console.log('    [!] no fg_elem, fallback to edge detection');
            return await this.detectGapByEdge(bgBuf);
        }

        const [sx, sy] = fgElem.sprite_pos;
        const [tw, th] = fgElem.size_2d;

        try {
            const templateBuf = await sharp(spriteBuf)
                .extract({ left: sx, top: sy, width: tw, height: th })
                .ensureAlpha()
                .raw()
                .toBuffer({ resolveWithObject: true });

            const tData = templateBuf.data;
            const tW = templateBuf.info.width;
            const tH = templateBuf.info.height;

            const bg = await sharp(bgBuf).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
            const bgData = bg.data;
            const bgW = bg.info.width;
            const bgH = bg.info.height;

            const searchStartX = Math.floor(bgW * 0.25);
            const searchEndX = Math.floor(bgW * 0.85);
            const searchStartY = Math.floor(bgH * 0.1);
            const searchEndY = Math.floor(bgH * 0.9);

            console.log(`    template: ${tW}x${tH}, bg: ${bgW}x${bgH}`);
            console.log(`    search: X=${searchStartX}-${searchEndX}, Y=${searchStartY}-${searchEndY}`);

            let bestX = searchStartX, bestY = searchStartY;
            let bestScore = -Infinity;
            const step1 = 3;

            for (let y = searchStartY; y <= searchEndY - tH; y += step1) {
                for (let x = searchStartX; x <= searchEndX - tW; x += step1) {
                    const { ncc, sad } = this.calculateMatchScore(tData, tW, tH, bgData, bgW, bgH, x, y);
                    if (ncc > bestScore) {
                        bestScore = ncc;
                        bestX = x;
                        bestY = y;
                    }
                }
            }

            console.log(`    coarse: X=${bestX}, Y=${bestY}, NCC=${bestScore.toFixed(4)}`);

            const refineRange = 8;
            const startX = Math.max(searchStartX, bestX - refineRange);
            const endX = Math.min(searchEndX - tW, bestX + refineRange);
            const startY = Math.max(searchStartY, bestY - refineRange);
            const endY = Math.min(searchEndY - tH, bestY + refineRange);

            for (let y = startY; y <= endY; y++) {
                for (let x = startX; x <= endX; x++) {
                    const { ncc, sad } = this.calculateMatchScore(tData, tW, tH, bgData, bgW, bgH, x, y);
                    if (ncc > bestScore) {
                        bestScore = ncc;
                        bestX = x;
                        bestY = y;
                    }
                }
            }

            console.log(`    refine: X=${bestX}, Y=${bestY}, NCC=${bestScore.toFixed(4)}`);

            if (bestScore < 0.2) {
                console.log('    [!] NCC too low, fallback to edge detection');
                return await this.detectGapByEdge(bgBuf, fgElem);
            }

            return { x: bestX, y: bestY, score: bestScore };

        } catch (e) {
            console.log('    template match failed:', e.message);
            return await this.detectGapByEdge(bgBuf);
        }
    }

    async detectGapByEdge(bgBuf, fgElem) {
        const bg = await sharp(bgBuf).raw().toBuffer({ resolveWithObject: true });
        const data = bg.data;
        const w = bg.info.width;
        const h = bg.info.height;

        const searchStartX = Math.floor(w * 0.25);
        const searchEndX = Math.floor(w * 0.85);
        const startY = Math.floor(h * 0.1);
        const endY = Math.floor(h * 0.9);
        const gapSize = fgElem?.size_2d ? fgElem.size_2d[0] : 110;

        let bestX = searchStartX;
        let bestY = Math.floor(h * 0.3);
        let maxEdgeSum = 0;

        for (let x = searchStartX; x < searchEndX - gapSize; x += 2) {
            for (let y = startY; y < endY - gapSize; y += 5) {
                let edgeSum = 0;
                // 检测垂直边缘
                for (let dy = 0; dy < gapSize; dy += 3) {
                    const yy = y + dy;
                    const idx1 = (yy * w + x - 2) * 3;
                    const idx2 = (yy * w + x + 2) * 3;
                    const idx3 = (yy * w + x + gapSize - 2) * 3;
                    const idx4 = (yy * w + x + gapSize + 2) * 3;
                    if (idx1 >= 0 && idx4 + 2 < data.length) {
                        edgeSum += Math.abs(data[idx1] - data[idx2]) +
                                   Math.abs(data[idx1 + 1] - data[idx2 + 1]) +
                                   Math.abs(data[idx1 + 2] - data[idx2 + 2]);
                        edgeSum += Math.abs(data[idx3] - data[idx4]) +
                                   Math.abs(data[idx3 + 1] - data[idx4 + 1]) +
                                   Math.abs(data[idx3 + 2] - data[idx4 + 2]);
                    }
                }
                if (edgeSum > maxEdgeSum) {
                    maxEdgeSum = edgeSum;
                    bestX = x;
                    bestY = y;
                }
            }
        }
        console.log(`    边缘检测: X=${bestX}, Y=${bestY}, 分数=${maxEdgeSum}`);
        return { x: bestX, y: bestY, score: maxEdgeSum };
    }

    /**
     * 计算滑动距离和 ans 参数
     * ans 格式: [{"elem_id":1,"type":"DynAnswerType_POS","data":"spriteX,spriteY"}]
     *
     * 注意: bgSize 是背景图在 sprite 坐标系中的尺寸 (如 672x480)
     *       gapX, gapY 是在实际图片像素坐标系中的位置
     */
    calculateAns(gapX, gapY, fgElem, bgSize, imgSize) {
        if (!fgElem) {
            return JSON.stringify([{ elem_id: 1, type: 'DynAnswerType_POS', data: `${gapX},230` }]);
        }

        // 滑块初始位置 (sprite坐标)
        const initX = fgElem.init_pos ? fgElem.init_pos[0] : 50;
        const initY = fgElem.init_pos ? fgElem.init_pos[1] : 230;

        // 计算缩放比例 (sprite坐标 / 图片像素坐标)
        const scaleX = bgSize[0] / imgSize.width;
        const scaleY = bgSize[1] / imgSize.height;

        // 将图片像素坐标转换为 sprite 坐标
        const gapSpriteX = Math.round(gapX * scaleX);
        const gapSpriteY = Math.round(gapY * scaleY);

        // 滑动距离(sprite) = 缺口位置 - 滑块初始位置
        const slideDistance = gapSpriteX - initX;

        // ans 的 data 是目标 sprite 坐标
        const targetX = initX + slideDistance;
        const targetY = initY;

        console.log(`    图片尺寸: ${imgSize.width}x${imgSize.height}, sprite尺寸: ${bgSize[0]}x${bgSize[1]}`);
        console.log(`    缺口图片坐标: (${gapX},${gapY}) -> sprite坐标: (${gapSpriteX},${gapSpriteY})`);
        console.log(`    滑块初始: (${initX},${initY}), 滑动距离: ${slideDistance}`);
        console.log(`    目标坐标: (${targetX},${targetY})`);

        return {
            ans: JSON.stringify([{
                elem_id: fgElem.id,
                type: 'DynAnswerType_POS',
                data: `${targetX},${targetY}`,
            }]),
            distance: Math.abs(slideDistance),
        };
    }

    async solve(targetDistance) {
        console.log('=== 腾讯防水墙纯算法验证 ===\n');

        // Step 1: prehandle
        console.log('[1/7] prehandle...');
        const preData = await this.prehandle();
        console.log('    sid:', preData.sid);
        console.log('    sess:', preData.sess.substring(0, 30) + '...');
        const dynShow = preData.data?.dyn_show_info;
        const commCfg = preData.data?.comm_captcha_cfg;
        const powCfg = commCfg?.pow_cfg;

        const fgList = dynShow?.fg_elem_list || [];
        const movableElem = fgList.find(f => f.move_cfg);
        console.log('    可移动元素:', movableElem ? `id=${movableElem.id}, sprite_pos=${JSON.stringify(movableElem.sprite_pos)}, init_pos=${JSON.stringify(movableElem.init_pos)}` : '未找到');

        // Step 2: 下载图片
        console.log('\n[2/7] 下载验证码图片...');
        let bgBuf = null;
        let bgImgSize = null;  // 实际图片尺寸
        const bgSize = dynShow?.bg_elem_cfg?.size_2d || [672, 480];  // sprite坐标系尺寸
        if (dynShow?.bg_elem_cfg?.img_url) {
            bgBuf = await this.downloadImage(dynShow.bg_elem_cfg.img_url);
            // 获取实际图片尺寸
            const bgMeta = await sharp(bgBuf).metadata();
            bgImgSize = { width: bgMeta.width, height: bgMeta.height };
            console.log('    背景图大小:', bgBuf.length, '字节, 尺寸:', bgImgSize.width, 'x', bgImgSize.height);
            console.log('    sprite尺寸:', bgSize[0], 'x', bgSize[1]);
        }
        let spriteBuf = null;
        if (dynShow?.sprite_url) {
            spriteBuf = await this.downloadImage(dynShow.sprite_url);
            console.log('    精灵图大小:', spriteBuf.length, '字节');
        }

        // Step 3: 缺口检测
        console.log('\n[3/7] 缺口检测...');
        let gapResult = null;
        if (bgBuf && spriteBuf && movableElem) {
            gapResult = await this.detectGap(bgBuf, spriteBuf, movableElem);
        } else if (bgBuf) {
            gapResult = await this.detectGapByEdge(bgBuf);
        }

        // Step 4: 加载 TDC
        console.log('\n[4/7] 加载 TDC...');
        const tdcPath = commCfg?.tdc_path || '/tdc.js';
        await this.loadTDC(tdcPath);
        console.log('    TDC 就绪');

        // Step 5: 计算 PoW
        console.log('\n[5/7] 计算 PoW...');
        let powAnswer = '0';
        let powCalcTime = 0;
        if (powCfg) {
            const powResult = this.solvePoW(powCfg.prefix, powCfg.md5);
            powAnswer = powResult.answer;
            powCalcTime = powResult.calcTime;
            console.log('    pow_answer:', powAnswer);
            console.log('    计算耗时:', powCalcTime, 'ms');
        }

        // Step 6: 生成轨迹并计算 ans
        console.log('\n[6/7] 生成轨迹...');
        let ans, dist;
        if (gapResult && movableElem && bgImgSize) {
            const result = this.calculateAns(gapResult.x, gapResult.y, movableElem, bgSize, bgImgSize);
            ans = result.ans;
            dist = result.distance;
        } else if (gapResult && movableElem) {
            // 如果没有获取到图片尺寸，使用默认缩放比例 2
            const defaultSize = { width: bgSize[0] / 2, height: bgSize[1] / 2 };
            const result = this.calculateAns(gapResult.x, gapResult.y, movableElem, bgSize, defaultSize);
            ans = result.ans;
            dist = result.distance;
        } else {
            dist = targetDistance || 120;
            ans = JSON.stringify([{ elem_id: movableElem?.id || 1, type: 'DynAnswerType_POS', data: `${(targetDistance || 120) * 2},${movableElem?.init_pos?.[1] || 230}` }]);
        }

        // NCC 置信度检查
        if (gapResult && gapResult.score < 0.55) {
            console.log(`    [!] NCC 置信度过低 (${gapResult.score.toFixed(4)} < 0.55)，跳过提交`);
            return {
                success: false,
                errorCode: '50',
                reason: `NCC score too low: ${gapResult.score.toFixed(4)}`,
            };
        }

        const tracks = this.generateTracks(dist);
        const tlg = tracks[tracks.length - 1][2];
        console.log(`    距离: ${dist}, 轨迹点: ${tracks.length}, 总时长: ${tlg}ms`);
        console.log('    ans:', ans);

        // TDC 加密
        const tdcResult = this.runTDC(tracks, preData.sid);
        console.log('    collect 长度:', tdcResult.collect ? tdcResult.collect.length : 0);

        // Step 7: 提交验证
        console.log('\n[7/7] 提交验证...');
        const result = await this.verify(
            preData.sess,
            ans,
            tdcResult.collect,
            tdcResult.eks,
            powAnswer,
            powCalcTime,
            tlg.toString()
        );

        if (result) {
            console.log('\n    errorCode:', result.errorCode);
            if (result.ticket) {
                console.log('    ticket:', result.ticket.substring(0, 50) + '...');
                console.log('    randstr:', result.randstr);
            }
        }

        return {
            success: result && result.errorCode === '0',
            ticket: result?.ticket,
            randstr: result?.randstr,
            errorCode: result?.errorCode,
        };
    }

    _saveCookies(r) {
        const sc = r.headers?.['set-cookie'];
        if (sc) sc.forEach(c => { const m = c.match(/^([^=]+)=([^;]+)/); if (m) this.cookies[m[1]] = m[2]; });
    }
}

function encryptPassword(password) {
    const key = Buffer.from('idcsmart.finance');
    const iv = Buffer.from('9311019310287172');
    const cipher = crypto.createCipheriv('aes-128-cbc', key, iv);
    return cipher.update(password, 'utf8', 'base64') + cipher.final('base64');
}

async function login(account, password, distance, useProxy = false) {
    console.log('=== 臻讯云纯算法登录 ===\n');
    if (useProxy) {
        console.log('[代理模式] 已启用\n');
    }

    const maxRetries = 3;
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        console.log(`\n--- 第 ${attempt}/${maxRetries} 次尝试 ---\n`);

        if (attempt > 1) {
            const delay = 15000 + Math.random() * 15000;
            console.log(`    等待 ${(delay / 1000).toFixed(1)}s ...`);
            await new Promise(r => setTimeout(r, delay));
        }

        console.log('[1/3] 获取初始 cookies...');
        const cookies = {};
        
        // 获取代理配置
        let captchaConfig = {
            headers: { 'User-Agent': UA, 'Accept': 'application/json', 'Referer': 'https://zhenxun.cn/login.htm' },
            httpsAgent,
            timeout: 30000,
        };
        
        if (useProxy) {
            const proxy = await getProxy();
            if (proxy) {
                const [host, port] = proxy.split(':');
                captchaConfig.proxy = {
                    protocol: 'http',
                    host: host,
                    port: parseInt(port),
                };
                captchaConfig.timeout = 15000;
            }
        }
        
        const captchaR = await axios.get('https://zhenxun.cn/console/v1/captcha', captchaConfig);
        if (captchaR.headers?.['set-cookie']) {
            captchaR.headers['set-cookie'].forEach(c => {
                const m = c.match(/^([^=]+)=([^;]+)/);
                if (m) cookies[m[1]] = m[2];
            });
        }
        console.log('    cookies:', Object.keys(cookies).join(', '));

        console.log('\n[2/3] 解决验证码...');
        const solver = new TxCaptcha('190094633', useProxy);
        const captchaResult = await solver.solve(distance);

        if (!captchaResult.success) {
            console.log(`\n[✗] 验证码失败 (尝试 ${attempt}/${maxRetries}), errorCode: ${captchaResult.errorCode}`);
            if (attempt < maxRetries) {
                continue;
            }
            return { success: false, errorCode: captchaResult.errorCode };
        }
        console.log('    ticket:', captchaResult.ticket.substring(0, 50) + '...');
        console.log('    randstr:', captchaResult.randstr);

        console.log('\n[3/3] 登录...');
        const cookieStr = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; ');
        
        // 登录请求配置
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
        
        if (useProxy) {
            const proxy = await getProxy();
            if (proxy) {
                const [host, port] = proxy.split(':');
                loginConfig.proxy = {
                    protocol: 'http',
                    host: host,
                    port: parseInt(port),
                };
                loginConfig.timeout = 15000;
            }
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
            console.log('\n[✓] 登录成功!');
            console.log('    JWT:', r.data.data.jwt.substring(0, 50) + '...');
            
            // 保存JWT到本地文件
            const jwtData = {
                account: account,
                jwt: r.data.data.jwt,
                loginTime: new Date().toISOString(),
                expireTime: r.data.data.expire_time || null,
            };
            fs.writeFileSync('jwt_token.json', JSON.stringify(jwtData, null, 2));
            console.log('    JWT已保存到: jwt_token.json');
            
            return { success: true, jwt: r.data.data.jwt };
        } else {
            console.log('\n[✗] 登录失败:', r.data.msg);
            if (r.data.msg && r.data.msg.includes('密码')) {
                return { success: false, error: r.data.msg };
            }
            if (attempt < maxRetries) {
                continue;
            }
            return { success: false, error: r.data.msg };
        }
    }
    return { success: false, error: 'exceeded max retries' };
}

const args = process.argv.slice(2);
if (args[0] === 'solve') {
    const useProxy = args.includes('--proxy');
    new TxCaptcha('190094633', useProxy).solve(parseInt(args[1]) || 0);
} else if (args[0] === 'login') {
    const account = args[1];
    const password = args[2];
    const dist = parseInt(args[3]) || 0;
    const useProxy = args.includes('--proxy');
    if (!account || !password) {
        console.error('用法: node txcaptcha_algo.js login <账号> <密码> [滑块距离] [--proxy]');
        process.exit(1);
    }
    login(account, password, dist, useProxy);
} else {
    console.log('用法:');
    console.log('  node txcaptcha_algo.js solve [距离] [--proxy]     - 测试验证码');
    console.log('  node txcaptcha_algo.js login <账号> <密码> [距离] [--proxy]  - 登录');
    console.log('');
    console.log('选项:');
    console.log('  --proxy  启用代理模式');
}

module.exports = { TxCaptcha, encryptPassword, login };
