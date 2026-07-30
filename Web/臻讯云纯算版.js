/**
 * 臻讯云自动登录脚本 - 青龙面板兼容版 (轻量版)
 * 
 * 无需安装 canvas 包，使用 mock 替代
 * 依赖: axios, jsdom, sharp (无需 canvas)
 * 
 * 环境变量:
 *   zhenxun=账号#密码
 *   zhenxun=账号1#密码1@账号2#密码2
 *   或通过多个同名环境变量设置不同账号
 * 
 * 代理配置:
 *   ZHENXUN_PROXY=http://ip:port  # 直接使用指定代理
 * 
 * JWT存储:
 *   统一存储在 zhenxuan.json 文件中，按账号区分
 */

const axios = require('axios');
const crypto = require('crypto');
const https = require('https');
const { JSDOM } = require('jsdom');
const sharp = require('sharp');
const fs = require('fs');
const path = require('path');

const httpsAgent = new https.Agent({ rejectUnauthorized: false });
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36';
const UA_B64 = Buffer.from(UA).toString('base64');

const JWT_FILE = path.join(process.cwd(), 'zhenxuan.json');

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

function createCanvasMock(window) {
    const CanvasFp = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';

    class MockCanvasRenderingContext2D {
        constructor() {
            this.canvas = null;
            this.fillStyle = '#000000';
            this.strokeStyle = '#000000';
            this.font = '10px sans-serif';
            this.textAlign = 'start';
            this.textBaseline = 'alphabetic';
            this.globalAlpha = 1.0;
            this.globalCompositeOperation = 'source-over';
            this.lineWidth = 1;
            this.lineCap = 'butt';
            this.lineJoin = 'miter';
            this.shadowBlur = 0;
            this.shadowColor = 'rgba(0, 0, 0, 0)';
            this.shadowOffsetX = 0;
            this.shadowOffsetY = 0;
            this._stateStack = [];
        }
        fillRect() {}
        strokeRect() {}
        clearRect() {}
        fillText() {}
        strokeText() {}
        measureText(text) { return { width: text.length * 6 }; }
        beginPath() {}
        closePath() {}
        moveTo() {}
        lineTo() {}
        arc() {}
        arcTo() {}
        quadraticCurveTo() {}
        bezierCurveTo() {}
        rect() {}
        fill() {}
        stroke() {}
        clip() {}
        drawImage() {}
        createLinearGradient() { return { addColorStop() {} }; }
        createRadialGradient() { return { addColorStop() {} }; }
        createPattern() { return {}; }
        save() { this._stateStack.push({}); }
        restore() { this._stateStack.pop(); }
        scale() {}
        rotate() {}
        translate() {}
        transform() {}
        setTransform() {}
        resetTransform() {}
        getImageData(sx, sy, sw, sh) {
            return {
                data: new Uint8ClampedArray(sw * sh * 4),
                width: sw,
                height: sh,
            };
        }
        putImageData() {}
        createImageData(w, h) {
            return {
                data: new Uint8ClampedArray(w * h * 4),
                width: w,
                height: h,
            };
        }
        isPointInPath() { return false; }
        isPointInStroke() { return false; }
    }

    class MockCanvasElement extends window.HTMLElement {
        constructor() {
            super();
            this.width = 300;
            this.height = 150;
            this._context2d = null;
        }
        getContext(type) {
            if (type === '2d') {
                if (!this._context2d) {
                    this._context2d = new MockCanvasRenderingContext2D();
                    this._context2d.canvas = this;
                }
                return this._context2d;
            }
            if (type === 'webgl' || type === 'experimental-webgl') {
                return {
                    getParameter: () => '',
                    getExtension: () => null,
                    createBuffer: () => ({}),
                    bindBuffer: () => {},
                    bufferData: () => {},
                    createProgram: () => ({}),
                    createShader: () => ({}),
                    shaderSource: () => {},
                    compileShader: () => {},
                    attachShader: () => {},
                    linkProgram: () => {},
                    getAttribLocation: () => 0,
                    getUniformLocation: () => ({}),
                    enableVertexAttribArray: () => {},
                    vertexAttribPointer: () => {},
                    uniform1f: () => {},
                    drawArrays: () => {},
                    readPixels: (x, y, w, h, fmt, type, pixels) => {
                        if (pixels) pixels.fill(0);
                    },
                    RENDERER: 7937,
                    VENDOR: 7936,
                    VERSION: 7938,
                    RGBA: 6408,
                    UNSIGNED_BYTE: 5121,
                };
            }
            return null;
        }
        toDataURL() { return CanvasFp; }
        toBlob(callback) {
            if (callback) callback(new (window.Blob || Buffer)([]));
        }
    }

    return MockCanvasElement;
}

class TxCaptcha {
    constructor(appId = '190094633', proxyConfig = null) {
        this.appId = appId;
        this.domain = 'turing.captcha.qcloud.com';
        this.cookies = {};
        this.tdcCode = null;
        this.proxyConfig = proxyConfig;
    }

    _getRequestConfig(baseConfig = {}) {
        const config = { ...baseConfig, httpsAgent, timeout: 30000 };
        if (this.proxyConfig) {
            config.proxy = {
                protocol: this.proxyConfig.protocol || 'http',
                host: this.proxyConfig.host,
                port: this.proxyConfig.port,
            };
            config.timeout = 15000;
        }
        return config;
    }

    async loadTDC(tdcPath) {
        if (this.tdcCode) return;
        try {
            const url = `https://${this.domain}${tdcPath}`;
            const config = this._getRequestConfig({
                headers: { 'User-Agent': UA, 'Referer': 'https://zhenxun.cn/' },
            });
            const r = await axios.get(url, config);
            this.tdcCode = r.data;
        } catch (e) {
            console.log('❌ TDC加载失败:', e.message);
            throw e;
        }
    }

    runTDC(tracks, sid) {
        const dom = new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>', {
            url: `https://${this.domain}/`,
            runScripts: 'outside-only',
        });
        const w = dom.window;

        const MockCanvasElement = createCanvasMock(w);
        Object.defineProperty(w, 'HTMLCanvasElement', {
            value: MockCanvasElement,
            configurable: true,
            writable: true,
        });
        Object.defineProperty(w.document, 'createElement', {
            value: function(tagName) {
                if (tagName.toLowerCase() === 'canvas') {
                    return new MockCanvasElement();
                }
                return w.document.__proto__.createElement.call(w.document, tagName);
            },
            configurable: true,
        });

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
        const config = this._getRequestConfig({
            params,
            headers: { 'User-Agent': UA, 'Referer': 'https://zhenxun.cn/', 'Accept': '*/*' },
        });
        const r = await axios.get(url, config);
        this._saveCookies(r);
        const text = typeof r.data === 'string' ? r.data : JSON.stringify(r.data);
        const jsonMatch = text.match(new RegExp(callbackName + '\\((\\{[\\s\\S]*\\})\\)')) ||
                          text.match(/\{[\s\S]*\}/);
        if (!jsonMatch) throw new Error('prehandle: invalid response');
        return JSON.parse(jsonMatch[1] || jsonMatch[0]);
    }

    async downloadImage(imgPath) {
        const url = `https://${this.domain}${imgPath}`;
        const config = this._getRequestConfig({
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
        const config = this._getRequestConfig({
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
        try {
            return JSON.parse(text.match(/\{[\s\S]*\}/)?.[0] || text);
        } catch {
            return null;
        }
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
        if (!fgElem || !fgElem.sprite_pos || !fgElem.size_2d) return null;
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
            let bestX = searchStartX, bestY = searchStartY;
            let bestScore = -Infinity;
            const step1 = 3;
            for (let y = searchStartY; y <= searchEndY - tH; y += step1) {
                for (let x = searchStartX; x <= searchEndX - tW; x += step1) {
                    const { ncc } = this.calculateMatchScore(tData, tW, tH, bgData, bgW, bgH, x, y);
                    if (ncc > bestScore) {
                        bestScore = ncc;
                        bestX = x;
                        bestY = y;
                    }
                }
            }
            const refineRange = 8;
            const startX = Math.max(searchStartX, bestX - refineRange);
            const endX = Math.min(searchEndX - tW, bestX + refineRange);
            const startY = Math.max(searchStartY, bestY - refineRange);
            const endY = Math.min(searchEndY - tH, bestY + refineRange);
            for (let y = startY; y <= endY; y++) {
                for (let x = startX; x <= endX; x++) {
                    const { ncc } = this.calculateMatchScore(tData, tW, tH, bgData, bgW, bgH, x, y);
                    if (ncc > bestScore) {
                        bestScore = ncc;
                        bestX = x;
                        bestY = y;
                    }
                }
            }
            if (bestScore < 0.2) return null;
            return { x: bestX, y: bestY, score: bestScore };
        } catch (e) {
            return null;
        }
    }

    calculateAns(gapX, gapY, fgElem, bgSize, imgSize) {
        if (!fgElem) {
            return JSON.stringify([{ elem_id: 1, type: 'DynAnswerType_POS', data: `${gapX},230` }]);
        }
        const initX = fgElem.init_pos ? fgElem.init_pos[0] : 50;
        const initY = fgElem.init_pos ? fgElem.init_pos[1] : 230;
        const scaleX = bgSize[0] / imgSize.width;
        const gapSpriteX = Math.round(gapX * scaleX);
        const slideDistance = gapSpriteX - initX;
        const targetX = initX + slideDistance;
        return {
            ans: JSON.stringify([{
                elem_id: fgElem.id,
                type: 'DynAnswerType_POS',
                data: `${targetX},${initY}`,
            }]),
            distance: Math.abs(slideDistance),
        };
    }

    async solve() {
        console.log('=== 腾讯防水墙纯算法验证 ===\n');

        console.log('🔍 步骤 1/7: 初始化验证...');
        const preData = await this.prehandle();
        console.log('    sid:', preData.sid);
        const dynShow = preData.data?.dyn_show_info;
        const commCfg = preData.data?.comm_captcha_cfg;
        const powCfg = commCfg?.pow_cfg;
        const fgList = dynShow?.fg_elem_list || [];
        const movableElem = fgList.find(f => f.move_cfg);

        console.log('\n📥 步骤 2/7: 下载验证码图片...');
        let bgBuf = null;
        let bgImgSize = null;
        const bgSize = dynShow?.bg_elem_cfg?.size_2d || [672, 480];
        if (dynShow?.bg_elem_cfg?.img_url) {
            bgBuf = await this.downloadImage(dynShow.bg_elem_cfg.img_url);
            const bgMeta = await sharp(bgBuf).metadata();
            bgImgSize = { width: bgMeta.width, height: bgMeta.height };
            console.log('    背景图尺寸:', bgImgSize.width, 'x', bgImgSize.height);
        }
        let spriteBuf = null;
        if (dynShow?.sprite_url) {
            spriteBuf = await this.downloadImage(dynShow.sprite_url);
        }

        console.log('\n🎯 步骤 3/7: 缺口检测...');
        let gapResult = null;
        if (bgBuf && spriteBuf && movableElem) {
            gapResult = await this.detectGap(bgBuf, spriteBuf, movableElem);
            if (gapResult) {
                console.log(`    缺口位置: X=${gapResult.x}, Y=${gapResult.y}, NCC=${gapResult.score.toFixed(4)}`);
            }
        }
        if (gapResult && gapResult.score < 0.55) {
            console.log(`    [!] NCC 置信度过低 (${gapResult.score.toFixed(4)} < 0.55)`);
            return { success: false, errorCode: '50' };
        }

        console.log('\n⚙️ 步骤 4/7: 加载 TDC...');
        const tdcPath = commCfg?.tdc_path || '/tdc.js';
        await this.loadTDC(tdcPath);
        console.log('    TDC 就绪');

        console.log('\n🔢 步骤 5/7: 计算 PoW...');
        let powAnswer = '0';
        let powCalcTime = 0;
        if (powCfg) {
            const powResult = this.solvePoW(powCfg.prefix, powCfg.md5);
            powAnswer = powResult.answer;
            powCalcTime = powResult.calcTime;
            console.log('    计算耗时:', powCalcTime, 'ms');
        }

        console.log('\n✏️ 步骤 6/7: 生成轨迹...');
        let ans, dist;
        if (gapResult && movableElem && bgImgSize) {
            const result = this.calculateAns(gapResult.x, gapResult.y, movableElem, bgSize, bgImgSize);
            ans = result.ans;
            dist = result.distance;
        } else if (gapResult) {
            ans = JSON.stringify([{ elem_id: 1, type: 'DynAnswerType_POS', data: `${gapResult.x},230` }]);
            dist = gapResult.x;
        } else {
            const fallbackX = 200 + Math.floor(Math.random() * 100);
            ans = JSON.stringify([{ elem_id: 1, type: 'DynAnswerType_POS', data: `${fallbackX},230` }]);
            dist = fallbackX;
        }

        const tracks = this.generateTracks(dist);
        console.log(`    距离: ${dist}, 轨迹点: ${tracks.length}, 总时长: ${tracks[tracks.length - 1][2]}ms`);

        console.log('\n📤 步骤 7/7: 提交验证...');
        const tdcResult = this.runTDC(tracks, preData.sid);
        const verifyResult = await this.verify(
            preData.sess, ans,
            tdcResult.collect, tdcResult.eks,
            powAnswer, powCalcTime,
            commCfg?.tlg
        );

        if (verifyResult && verifyResult.errorCode === '0') {
            console.log('✅ 验证码通过!');
            return {
                success: true,
                ticket: verifyResult.ticket,
                randstr: verifyResult.randstr,
            };
        }

        const errCode = verifyResult?.errorCode || 'unknown';
        console.log(`❌ 验证失败: ${errCode}`);
        return { success: false, errorCode: errCode };
    }

    _saveCookies(response) {
        const setCookies = response.headers?.['set-cookie'];
        if (setCookies) {
            setCookies.forEach(c => {
                const m = c.match(/^([^=]+)=([^;]+)/);
                if (m) this.cookies[m[1]] = m[2];
            });
        }
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
            console.log('🍪 正在获取初始 cookies...');
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
            console.log('🧩 正在破解验证码...');
            const solver = new TxCaptcha('190094633', proxyConfig);
            const captchaResult = await solver.solve();
            if (!captchaResult.success) {
                console.log(`😵 验证码破解失败 (尝试 ${attempt}/${maxRetries})`);
                if (attempt < maxRetries) continue;
                return { success: false, error: '验证码失败' };
            }
            console.log('🔐 正在登录...');
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
    console.log('🎉 臻讯云自动签到 (轻量版) 🎉');
    console.log('⏰ 时间:', new Date().toLocaleString());
    console.log('');

    const accounts = parseAccounts();
    if (accounts.length === 0) {
        console.error('❌ 未找到有效账号');
        console.error('💡 格式: zhenxun=账号#密码');
        console.error('💡 多账号: zhenxun=账号1#密码1@账号2#密码2');
        console.error('💡 或多行: zhenxun=账号1#密码1');
        console.error('          zhenxun_2=账号2#密码2');
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
