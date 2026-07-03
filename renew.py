#!/usr/bin/env python3
"""
ACLClouds 自动续期脚本 (Playwright 全程浏览器版 · 多账号)
支持最多 4 个账号，通过编号 Secret 区分：
  ACCOUNT1_EMAIL / ACCOUNT1_PASSWORD
  ACCOUNT2_EMAIL / ACCOUNT2_PASSWORD
  ACCOUNT3_EMAIL / ACCOUNT3_PASSWORD（可选）
  ACCOUNT4_EMAIL / ACCOUNT4_PASSWORD（可选）
"""

import os
import re
import sys
import json
import time
import random
import traceback
from urllib.request import Request, urlopen

# ── 代理配置 ──────────────────────────────────────────────
PROXY_SERVER = "socks5://127.0.0.1:10808"

# ── 录屏开关：true=开启录屏，false=关闭录屏 ──────────────
ENABLE_VIDEO = os.environ.get("ENABLE_VIDEO", "false").strip().lower() == "true"

# ── 推送凭据（全局共用） ──────────────────────────────────
TG_BOT_TOKEN      = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID        = os.environ.get("TG_CHAT_ID", "").strip()
WXPUSHER_APPTOKEN = os.environ.get("WXPUSHER_APPTOKEN", "").strip()
WXPUSHER_UID      = os.environ.get("WXPUSHER_UID", "").strip()

RENEW_THRESHOLD_DAYS = 2
BASE_URL  = "https://dash.aclclouds.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

# ── 脱敏工具 ──────────────────────────────────────────────
def mask_email(email: str) -> str:
    """abc@example.com → a**@e******.com"""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    local_m  = local[0] + "**" if len(local) > 1 else "**"
    parts    = domain.split(".")
    domain_m = parts[0][0] + "*" * (len(parts[0]) - 1) if parts[0] else "***"
    suffix   = "." + ".".join(parts[1:]) if len(parts) > 1 else ""
    return f"{local_m}@{domain_m}{suffix}"

def mask_ip(ip: str) -> str:
    """208.77.246.23 → 208.77.*.*"""
    parts = ip.strip().split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return "***"

# ── 读取多账号列表 ────────────────────────────────────────
def load_accounts():
    accounts = []
    for i in range(1, 5):   # 支持 1~4 个账号
        email    = os.environ.get(f"ACCOUNT{i}_EMAIL", "").strip()
        password = os.environ.get(f"ACCOUNT{i}_PASSWORD", "").strip()
        if email and password:
            accounts.append({"index": i, "email": email, "password": password,
                             "email_masked": mask_email(email)})
    return accounts

# ── 日志 ─────────────────────────────────────────────────
def log(msg):       print(f"[INFO] {msg}", flush=True)
def log_warn(msg):  print(f"[WARN] {msg}", flush=True)
def log_error(msg): print(f"[ERROR] {msg}", flush=True)

def get_outbound_ip():
    try:
        data = urlopen("https://cloudflare.com/cdn-cgi/trace", timeout=5).read().decode()
        for line in data.splitlines():
            if line.startswith("ip="):
                raw = line.strip().replace("ip=", "")
                return f"ip={mask_ip(raw)}"
    except Exception as e:
        return f"ip=获取失败({e})"
    return "ip=未知"

def get_proxy_ip():
    try:
        import subprocess
        result = subprocess.run(
            ["curl", "-s", "--max-time", "5", "--socks5", "127.0.0.1:10808", "ifconfig.me"],
            capture_output=True, text=True, timeout=10
        )
        raw = result.stdout.strip()
        return mask_ip(raw) if result.returncode == 0 else "获取失败"
    except Exception as e:
        return f"获取失败({e})"

# ── 推送函数 ──────────────────────────────────────────────
def send_tg(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        body = json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
        req = Request(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                      data=body, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=15)
        log("TG 推送成功")
    except Exception as e:
        log_warn(f"TG 推送失败: {e}")

def send_wxpusher(text: str):
    if not WXPUSHER_APPTOKEN or not WXPUSHER_UID:
        return
    try:
        payload = {"appToken": WXPUSHER_APPTOKEN, "content": text,
                   "summary": "ACLClouds 续期通知", "contentType": 1, "uids": [WXPUSHER_UID]}
        req = Request("https://wxpusher.zjiecode.com/api/send/message",
                      data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        result = json.loads(urlopen(req, timeout=15).read().decode())
        if result.get("code") == 1000:
            log("wxpusher 推送成功")
        else:
            log_warn(f"wxpusher 返回错误: {result}")
    except Exception as e:
        log_warn(f"wxpusher 推送失败: {e}")

def send_all_push(text: str):
    send_tg(text)
    send_wxpusher(text)

# ── 解析剩余时间 ──────────────────────────────────────────
def parse_expires(text):
    if text is None:
        return None
    s = str(text).strip()
    if re.search(r'\d{4}-\d{2}-\d{2}', s):
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return (dt - datetime.now(timezone.utc)).total_seconds() / 86400
        except Exception:
            pass
    try:
        return float(s) / 86400
    except Exception:
        pass
    sl = s.lower()
    days = hours = minutes = 0.0
    m = re.search(r'(\d+(?:\.\d+)?)\s*[dj]', sl)
    if m: days = float(m.group(1))
    m = re.search(r'(\d+(?:\.\d+)?)\s*h', sl)
    if m: hours = float(m.group(1))
    m = re.search(r'(\d+(?:\.\d+)?)\s*m(?!o)', sl)
    if m: minutes = float(m.group(1))
    total = days + hours / 24 + minutes / 1440
    return total if total > 0 else None

# ── 重新查询指定项目的剩余时间（用于续期后二次核实）───────────
def fetch_project_remaining(page, identifier: str):
    """重新调用 /api/client（只读，不会触发限流），
    找到 identifier 匹配的项目，返回当前剩余天数；查不到返回 None。"""
    try:
        result = page.evaluate("""async () => {
            const r = await fetch('/api/client', {headers: {'Accept': 'application/json'}});
            return {status: r.status, body: await r.text()};
        }""")
        if result['status'] != 200:
            return None
        data = json.loads(result['body'])
        for item in data.get('data', []):
            attrs = item.get('attributes') or {}
            if attrs.get('identifier') == identifier:
                return parse_expires(attrs.get('expires_at'))
        return None
    except Exception:
        return None

# ── 截图工具（涂抹敏感区域）────────────────────────────────
def screenshot(page, name: str):
    os.makedirs("screenshots", exist_ok=True)
    path = f"screenshots/{name}.png"
    try:
        page.evaluate("""() => {
            const blur = el => { el.style.filter = 'blur(8px)'; };

            // ── 1. input 值（登录表单）──────────────────
            document.querySelectorAll('input').forEach(blur);

            // ── 2. header 右侧整个用户区域 ───────────────
            const headerSelectors = [
                'header button', 'header [role="button"]',
                'nav button',    'nav [role="button"]',
                'span.username', '[class*="username"]', '[class*="user-name"]',
                '[class*="UserName"]', '[class*="userName"]',
                '.user-info', '.header-user', '.navbar .user',
                '.account-name', '.text-sm.font-medium',
                '[class*="avatar"] + *', '[class*="Avatar"] + *',
            ];
            headerSelectors.forEach(sel => {
                try { document.querySelectorAll(sel).forEach(blur); } catch(e) {}
            });

            // ── 3. 顶栏整体兜底 ──────────────────────────
            ['header', 'nav', '.topbar', '.top-bar', '#header', '#nav'].forEach(sel => {
                try {
                    document.querySelectorAll(sel).forEach(el => {
                        el.querySelectorAll('span, p, a, button, div').forEach(child => {
                            if (child.children.length === 0 && child.textContent.trim()) {
                                blur(child);
                            }
                        });
                    });
                } catch(e) {}
            });

            // ── 4. 项目/服务器列表中的敏感列 ───────────────
            document.querySelectorAll('table td, table th').forEach(td => {
                if (td.tagName !== 'TH' && /[0-9]/.test(td.textContent)) { blur(td); }
            });
            document.querySelectorAll(
                '[class*="service"] [class*="name"], [class*="server"] [class*="name"],'
                + '[class*="project"] [class*="name"], [class*="node"], [class*="identifier"],'
                + '[class*="expire"], [class*="renew"], [class*="date"]'
            ).forEach(blur);

            // ── 5. IP 地址区域 ───────────────────────────
            document.querySelectorAll('[class*="address"], [class*="ip"], [class*="host"]').forEach(blur);

            // ── 6. Welcome 欢迎语中的用户名 ─────────────
            document.querySelectorAll('h1, h2, h3').forEach(el => {
                el.querySelectorAll('span, strong, b').forEach(blur);
            });
        }""")
    except Exception:
        pass
    try:
        page.screenshot(path=path, full_page=True)
        log(f"截图已保存: {path}")
    except Exception as e:
        log_warn(f"截图失败 {path}: {e}")

# ── Tesseract OCR：识别图块验证码（免费，无需 API key）──────
def ocr_image_bytes(img_bytes: bytes, debug_path: str = None) -> str:
    """
    用 Tesseract 识别图片中的文字。
    对验证码图片做预处理（放大 + 灰度 + 二值化）提升准确率。
    debug_path: 若传入路径，把预处理后的图片也保存下来方便调试。
    """
    import io
    try:
        from PIL import Image, ImageOps
        import pytesseract
    except ImportError as e:
        log_warn(f"[OCR] 依赖未安装: {e}")
        return ""

    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        # 白底合并（去掉透明通道）
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg.convert("RGB")
        log(f"[OCR] 原始尺寸: {img.size}")
        # 放大 3 倍，再灰度 + 二值化，让 Tesseract 更容易识别
        img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
        img = ImageOps.grayscale(img)
        img = img.point(lambda x: 0 if x < 160 else 255)
        if debug_path:
            img.save(debug_path)
            log(f"[OCR] 预处理图已保存: {debug_path}")
        # psm 8 = 单个单词模式
        raw = pytesseract.image_to_string(
            img,
            config="--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        )
        text = raw.strip()
        log(f"[OCR] Tesseract 原始输出: {repr(raw)}  → 清理后: 「{text}」")
        return text
    except Exception as e:
        log_warn(f"[OCR] 识别出错: {e}")
        return ""


def solve_image_challenge(page, dialog, tag: str, name: str, idx: int, identifier: str) -> bool:
    """
    处理二级图块挑战（div.auth-captcha-challenge）：
      1. 读取题目关键词（div.auth-captcha-prompt strong 的文字）
      2. 通过 page.evaluate fetch 在浏览器上下文内拉取图片并转 base64
         （带登录 cookie，不需要外部下载，不需要 API key）
      3. 用本地 Tesseract OCR 识别每张图的文字
      4. 点击与关键词匹配的那个按钮
    返回 True 表示已点击，False 表示未触发挑战（正常一级验证直接过）。
    """
    import base64, io

    challenge = dialog.locator("div.auth-captcha-challenge").first
    try:
        challenge.wait_for(state="visible", timeout=5000)
    except Exception:
        return False  # 没有二级挑战

    # 读取题目关键词
    try:
        keyword = challenge.locator("div.auth-captcha-prompt strong").first.inner_text(timeout=3000).strip()
    except Exception:
        keyword = ""
    log(f"[{tag}] [{name}] 图块挑战关键词: 「{keyword}」")
    screenshot(page, f"acct{idx}_renew_{identifier}_challenge")

    if not keyword:
        log_warn(f"[{tag}] [{name}] 无法读取关键词，跳过图块挑战处理")
        return False

    # 收集四个按钮
    option_btns = challenge.locator("button.auth-captcha-option").all()
    log(f"[{tag}] [{name}] 找到 {len(option_btns)} 个图块选项")

    clicked = False
    for btn_idx, btn in enumerate(option_btns):
        try:
            img_el  = btn.locator("img.auth-captcha-option-img").first
            img_src = img_el.get_attribute("src") or ""
            if not img_src:
                continue

            # 在浏览器上下文里用带 cookie 的 fetch 拉取图片 → base64
            b64 = page.evaluate("""async (src) => {
                try {
                    const r = await fetch(src, {credentials: 'include'});
                    if (!r.ok) return null;
                    const buf = await r.arrayBuffer();
                    let bin = '';
                    const bytes = new Uint8Array(buf);
                    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                    return btoa(bin);
                } catch(e) { return null; }
            }""", img_src)

            if not b64:
                log_warn(f"[{tag}] [{name}] 选项{btn_idx+1} 图片拉取失败，跳过")
                continue

            img_bytes = base64.b64decode(b64)

            # 保存图片到 screenshots 方便调试
            os.makedirs("screenshots", exist_ok=True)
            img_path = f"screenshots/acct{idx}_renew_{identifier}_opt{btn_idx+1}.png"
            with open(img_path, "wb") as f:
                f.write(img_bytes)

            # Tesseract OCR 识别，同时保存预处理后的图片用于调试
            debug_path = f"screenshots/acct{idx}_renew_{identifier}_opt{btn_idx+1}_processed.png"
            recognized = ocr_image_bytes(img_bytes, debug_path=debug_path)
            log(f"[{tag}] [{name}] 选项{btn_idx+1} OCR结果: 「{recognized}」")

            if keyword.lower() in recognized.lower() or recognized.lower() in keyword.lower():
                log(f"[{tag}] [{name}] ✅ 匹配！点击选项 {btn_idx+1}")
                btn.click(timeout=5000)
                clicked = True
                time.sleep(1)
                break

        except Exception as e:
            log_warn(f"[{tag}] [{name}] 选项{btn_idx+1} 处理出错: {e}")
            continue

    if not clicked:
        log_warn(f"[{tag}] [{name}] 未找到匹配选项，关键词=「{keyword}」")
    return clicked


# ── 单个项目续期（真实 UI 点击 + 人机验证，让前端完成续期）──
def renew_via_ui(page, tag: str, name: str, identifier: str, idx: int):
    """
    在 /projects 页面：
      1. 找到该项目卡片里的 Renew 按钮并点击 → 弹出 Anti-bot confirmation 弹窗
      2. 点击 div.auth-captcha-inner 触发验证码请求
         - 若直接通过（一级）：前端自动提交续期请求，弹窗消失
         - 若触发二级图块挑战：OCR 识别并点击正确图块，前端提交续期请求，弹窗消失
      3. 监听前端发出的 /upgrade/renew 请求结果，或等弹窗消失来判断成功
    """
    from playwright.sync_api import TimeoutError as PWTimeout

    card = page.locator("div.client-card", has_text=name).first

    # 注意：点击 Renew 按钮弹窗出现的瞬间，前端会自动先打一次
    # /upgrade/renew（此时验证码还没做，必然失败/被拒绝）。
    # 之前的版本从一开始就监听响应，结果永远抓到的是这条"注定失败"的
    # 请求，后面验证码做完、真正成功的那次反而被忽略了。
    # 现在改成：监听器延后到验证码流程走完之后才注册，
    # 这样根本不会捕获到那条必败的早期请求。
    renew_result_holder = {"status": None, "body": None}

    def _on_response(res):
        try:
            if "/upgrade/renew" not in res.url:
                return
            ctype = res.headers.get("content-type") or ""
            body_text = None
            if "application/json" in ctype:
                try:
                    body_text = res.text()
                except Exception:
                    body_text = None
            # 持续覆盖，保留最新一次匹配到的结果
            renew_result_holder["status"] = res.status
            renew_result_holder["body"] = body_text
            log(f"[{tag}] [{name}] 捕获到续期请求响应: HTTP {res.status}（来源: {res.url}）")
        except Exception as e:
            log_warn(f"[{tag}] [{name}] 响应监听出错: {e}")

    try:
        # ── 1. 点击 Renew 按钮 ─────────────────────────────
        log(f"[{tag}] [{name}] 查找并点击 Renew 按钮...")
        renew_btn = card.locator("div.projects-card-expiry button.client-btn").first
        try:
            renew_btn.wait_for(state="visible", timeout=10000)
        except PWTimeout:
            raise RuntimeError("找不到该项目的续期按钮（div.projects-card-expiry 内无可见按钮，"
                                "可能已续期过或结构变了）")
        renew_btn.click()

        # ── 2. 等待人机验证弹窗出现 ──────────────────────────
        dialog = page.locator("div[role='dialog'][aria-labelledby='renew-captcha-title']").first
        try:
            dialog.wait_for(state="visible", timeout=10000)
        except PWTimeout:
            raise RuntimeError("续期确认弹窗未出现")
        screenshot(page, f"acct{idx}_renew_{identifier}_a_dialog")

        # ── 2b. 模拟真人停顿 ──────────────────────────────────
        # 前端会把"弹窗出现到点击验证码"这段耗时（elapsed）连同签名
        # 一起发给服务器（payload 里的 context: renewal_gate）。
        # 弹窗一出现就立刻点，elapsed 会是几十/几百毫秒，跟真人行为
        # （通常几秒到十几秒）差距明显，容易被判定为可疑。这里加一段
        # 随机延迟，顺便小幅移动一下鼠标，让这个耗时特征更自然。
        human_delay = random.uniform(3.0, 8.0)
        log(f"[{tag}] [{name}] 模拟真人停顿 {human_delay:.1f}s...")
        try:
            box = dialog.bounding_box()
            if box:
                page.mouse.move(
                    box["x"] + box["width"] * random.uniform(0.3, 0.7),
                    box["y"] + box["height"] * random.uniform(0.2, 0.5),
                    steps=random.randint(5, 15),
                )
        except Exception:
            pass
        time.sleep(human_delay)

        # ── 3. 点击验证码复选框 ───────────────────────────────
        log(f"[{tag}] [{name}] 点击续期弹窗 captcha 复选框...")
        dialog.locator("div.auth-captcha-inner").click(timeout=10000)
        time.sleep(1)

        # ── 4. 检测并处理二级图块挑战 ────────────────────────
        solve_image_challenge(page, dialog, tag, name, idx, identifier)

        screenshot(page, f"acct{idx}_renew_{identifier}_b_after_captcha")

        # ── 4b. 验证码流程走完后才开始监听 ───────────────────
        # 关键：不能在点 Renew 按钮之前就监听，因为弹窗刚出现时前端
        # 会自动先打一次 /upgrade/renew（验证码还没做，必然失败），
        # 提前监听只会抓到这条必败请求。这里验证码流程结束后才注册，
        # 保证只捕获验证通过后真正提交的那次请求结果。
        page.on("response", _on_response)

        # ── 5. 被动等待前端自己发起 /upgrade/renew 请求（最多 30s）──
        # 验证码通过后，前端 JS 会自动提交续期请求，我们不用（也不该）
        # 自己去拼这个请求——手动拼的请求会被服务器判 429，真实点击
        # 触发的不会，说明这里必须让浏览器按正常流程走。
        log(f"[{tag}] [{name}] 等待前端自动提交续期请求（最多30s）...")
        for _ in range(30):
            if renew_result_holder["status"] is not None:
                break
            time.sleep(1)

        screenshot(page, f"acct{idx}_renew_{identifier}_c_result")

        if renew_result_holder["status"] is None:
            # 兜底：没抓到网络请求，看弹窗是否已经消失（可能是前端没走
            # fetch 而是别的方式提交，或者响应类型不是 json 被我们漏检了）
            try:
                dialog.wait_for(state="hidden", timeout=5000)
                log(f"[{tag}] [{name}] 未捕获到续期响应，但弹窗已消失，视为成功")
                return None
            except Exception:
                raise RuntimeError("30s 内未捕获到续期请求，也未见弹窗消失，图块验证可能未通过")

        status = renew_result_holder["status"]
        body_text = renew_result_holder["body"] or ""
        log(f"[{tag}] [{name}] 续期响应 HTTP {status}: {body_text[:300]}")

        if status == 429:
            raise RuntimeError(f"续期被限流（429 Too Many Attempts）: {body_text[:200]}")
        if status != 200:
            raise RuntimeError(f"续期请求失败 HTTP {status}: {body_text[:200]}")

        try:
            data = json.loads(body_text)
        except Exception:
            # 拿到200但解析不了body，弹窗一般也会自己消失，按成功处理
            log_warn(f"[{tag}] [{name}] 续期响应200但无法解析JSON，按成功处理: {body_text[:200]}")
            return None

        if not data.get("success"):
            raise RuntimeError(f"续期返回失败: {data.get('message', '未知错误')}")

        log(f"[{tag}] [{name}] 续期成功 ✅ {data.get('message', '')}")
        if data.get("expires_at"):
            return parse_expires(data["expires_at"])
        return None

    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass


# ── 反检测：隐藏 Playwright/Chromium 自动化特征 ──────────────
STEALTH_INIT_SCRIPT = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' }))
    });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    window.chrome = window.chrome || { runtime: {} };
    const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (originalQuery) {
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );
    }
    delete navigator.__proto__.webdriver;
}
"""

def run_account(account: dict):
    """对单个账号执行续期，返回 (renewed_list, skipped_list, failed_list)"""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    idx          = account["index"]
    email        = account["email"]
    password     = account["password"]
    email_masked = account["email_masked"]
    tag          = f"账号{idx}({email_masked})"   # 日志用脱敏邮箱

    log(f"\n{'='*50}")
    log(f"开始处理 {tag}")
    log(f"{'='*50}")

    renewed_list, skipped_list, failed_list = [], [], []

    with sync_playwright() as p:
        os.makedirs("screenshots", exist_ok=True)
        browser = p.chromium.launch(
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
            proxy={"server": PROXY_SERVER},
        )

        # 录屏开关
        ctx_kwargs = dict(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/148.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        if ENABLE_VIDEO:
            ctx_kwargs["record_video_dir"]  = "screenshots/"
            ctx_kwargs["record_video_size"] = {"width": 1280, "height": 800}
            log(f"[{tag}] 录屏已开启")

        ctx  = browser.new_context(**ctx_kwargs)
        ctx.add_init_script(STEALTH_INIT_SCRIPT)
        page = ctx.new_page()

        try:
            # ── 1. 打开登录页 ─────────────────────────────
            log(f"[{tag}] 导航到登录页: {LOGIN_URL}")
            page.goto(LOGIN_URL, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=30000)
            screenshot(page, f"acct{idx}_01_login_page")

            # ── 2. 填写登录表单 ───────────────────────────
            log(f"[{tag}] 填写登录表单...")
            email_selectors = [
                "input[type='email']", "input[name='user']", "input[name='email']",
                "input[placeholder*='mail']", "input[placeholder*='Email']", "input:first-of-type",
            ]
            email_filled = False
            for sel in email_selectors:
                try:
                    page.wait_for_selector(sel, timeout=3000)
                    page.fill(sel, email)
                    log(f"  邮箱字段使用选择器: {sel}")
                    email_filled = True
                    break
                except Exception:
                    continue

            if not email_filled:
                screenshot(page, f"acct{idx}_02_no_email_field")
                raise RuntimeError("找不到邮箱输入框")

            for sel in ["input[type='password']", "input[name='password']"]:
                try:
                    page.wait_for_selector(sel, timeout=3000)
                    page.fill(sel, password)
                    break
                except Exception:
                    continue

            screenshot(page, f"acct{idx}_02_form_filled")

            # ── 2b. 模拟真人停顿（同续期弹窗的道理，填完表单不会立刻点验证码）──
            human_delay = random.uniform(2.0, 6.0)
            log(f"[{tag}] 模拟真人停顿 {human_delay:.1f}s...")
            time.sleep(human_delay)

            # ── 3. captcha ────────────────────────────────
            log(f"[{tag}] 点击 captcha 复选框...")
            page.click("div.auth-captcha-inner", timeout=10000)
            captcha_verified = False
            # 第一轮：等待 10 秒（网络快时通常够用）
            try:
                page.wait_for_selector(
                    "div.auth-captcha-box.verified, div.auth-captcha-inner[aria-checked='true']",
                    timeout=10000)
                captcha_verified = True
                log(f"[{tag}] captcha 验证通过 ✅")
            except Exception:
                log_warn(f"[{tag}] captcha 10s 内未验证，继续等待最多 20s...")
                # 第二轮：网络慢时额外轮询 20 秒
                for _ in range(20):
                    time.sleep(1)
                    try:
                        page.wait_for_selector(
                            "div.auth-captcha-box.verified, div.auth-captcha-inner[aria-checked='true']",
                            timeout=1000)
                        captcha_verified = True
                        log(f"[{tag}] captcha 验证通过 ✅（延迟通过）")
                        break
                    except Exception:
                        continue
                if not captcha_verified:
                    log_warn(f"[{tag}] captcha 30s 后仍未验证，强行提交（可能失败）")
            screenshot(page, f"acct{idx}_02b_captcha")

            # ── 4. 提交登录 ───────────────────────────────
            for sel in ["button[type='submit']", "button:has-text('Login')",
                        "button:has-text('登录')", "button:has-text('Sign in')",
                        "input[type='submit']"]:
                try:
                    page.click(sel, timeout=3000)
                    break
                except Exception:
                    continue

            page.wait_for_load_state("networkidle", timeout=30000)
            screenshot(page, f"acct{idx}_03_after_submit")

            try:
                page.wait_for_url(lambda url: "login" not in url, timeout=20000)
                log(f"[{tag}] 登录成功 ✅，URL: {page.url}")
            except PWTimeout:
                screenshot(page, f"acct{idx}_03_login_timeout")
                raise RuntimeError(f"登录超时，仍在: {page.url}")

            screenshot(page, f"acct{idx}_04_after_login")

            # ── 5. 等待页面JS初始化完成 ───────────────────
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            time.sleep(3)

            # ── 6. 获取项目列表 ───────────────────────────
            result = page.evaluate("""async () => {
                const r = await fetch('/api/client', {headers: {'Accept': 'application/json'}});
                return {status: r.status, body: await r.text()};
            }""")
            if result['status'] != 200:
                raise RuntimeError(f"获取项目列表失败 HTTP {result['status']}")

            data = json.loads(result['body'])
            projects = [item['attributes'] for item in data.get('data', []) if item.get('attributes')]
            log(f"[{tag}] 找到 {len(projects)} 个项目")

            if not projects:
                log_warn(f"[{tag}] 项目列表为空")
                if ENABLE_VIDEO:
                    try:
                        page.video.save_as(f"screenshots/acct{idx}_video.webm")
                    except Exception:
                        pass
                ctx.close(); browser.close()
                return renewed_list, skipped_list, failed_list

            # ── 6b. 跳转到项目页（续费按钮 / 验证码弹窗都在这个页面上渲染）──
            try:
                page.goto(f"{BASE_URL}/projects", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception as e:
                log_warn(f"[{tag}] 跳转 /projects 失败: {e}")

            # ── 7. 逐项目续期（走真实 UI 点击，携带人机验证）──────
            for project in projects:
                name        = project.get("name", "未知项目")
                identifier  = project.get("identifier", "")
                raw_expires = project.get("expires_at")
                remaining   = parse_expires(raw_expires)

                if remaining is None:
                    failed_list.append(f"{tag} · {name}（无法解析过期时间）")
                    continue

                log(f"[{tag}] [{name}] 剩余 {remaining:.2f} 天")

                if remaining >= RENEW_THRESHOLD_DAYS:
                    skipped_list.append(f"{tag} · {name}（剩余 {remaining:.1f} 天）")
                    continue

                try:
                    # 续期动作本身返回的结果（网络监听/弹窗消失）只是参考，
                    # 不完全可信——真正靠谱的做法是续期前后各读一次真实的
                    # 过期时间，用差值来判断是否真的续期成功。
                    renew_via_ui(page, tag, name, identifier, idx)

                    time.sleep(2)  # 给服务器一点时间落库
                    fresh_remaining = fetch_project_remaining(page, identifier)

                    if fresh_remaining is None:
                        log_warn(f"[{tag}] [{name}] 续期后无法重新查询过期时间，无法确认是否成功")
                        failed_list.append(f"{tag} · {name}（续期后无法确认结果）")
                    elif fresh_remaining > remaining + 0.05:
                        log(f"[{tag}] [{name}] 续期成功 ✅ {remaining:.2f}天 → {fresh_remaining:.2f}天")
                        renewed_list.append(
                            f"{tag} · {name}（{remaining:.1f}天 → {fresh_remaining:.1f}天）")
                    else:
                        log_warn(f"[{tag}] [{name}] 续期前后剩余时间几乎没变化"
                                  f"（{remaining:.2f}天 → {fresh_remaining:.2f}天），判定为未成功")
                        failed_list.append(
                            f"{tag} · {name}（续期未生效，剩余仍为 {fresh_remaining:.1f}天）")

                except Exception as e:
                    log_error(f"[{tag}][{name}] 续期异常: {e}")
                    failed_list.append(f"{tag} · {name}（{str(e)[:80]}）")

            try:
                screenshot(page, f"acct{idx}_05_final")
            except Exception:
                pass

        except Exception as e:
            try:
                screenshot(page, f"acct{idx}_99_error")
            except Exception:
                pass
            if ENABLE_VIDEO:
                try:
                    page.video.save_as(f"screenshots/acct{idx}_error_video.webm")
                except Exception:
                    pass
            ctx.close(); browser.close()
            failed_list.append(f"{tag} · 账号级异常: {str(e)[:120]}")
            return renewed_list, skipped_list, failed_list

        # 录屏保存完再关闭
        if ENABLE_VIDEO:
            try:
                page.video.save_as(f"screenshots/acct{idx}_video.webm")
            except Exception:
                pass
        ctx.close()
        browser.close()

    return renewed_list, skipped_list, failed_list


# ── 主入口 ────────────────────────────────────────────────
if __name__ == "__main__":
    accounts = load_accounts()
    if not accounts:
        log_error("未找到任何账号！请设置 ACCOUNT1_EMAIL / ACCOUNT1_PASSWORD 等环境变量")
        sys.exit(1)

    log(f"[网络] 直连出口 IP: {get_outbound_ip()}")
    log(f"[网络] 代理出口 IP: {get_proxy_ip()}")
    log(f"共 {len(accounts)} 个账号待处理")
    log(f"录屏: {'开启' if ENABLE_VIDEO else '关闭'}")

    all_renewed, all_skipped, all_failed = [], [], []

    for account in accounts:
        try:
            r, s, f = run_account(account)
            all_renewed.extend(r)
            all_skipped.extend(s)
            all_failed.extend(f)
        except Exception as ex:
            em = account["email_masked"]
            log_error(f"账号{account['index']} 顶层异常: {ex}")
            traceback.print_exc()
            all_failed.append(f"账号{account['index']}({em}) · 顶层异常: {str(ex)[:100]}")

    # ── 汇总推送 ──────────────────────────────────────────
    log("=" * 50)
    log(f"续期成功: {len(all_renewed)} 个")
    log(f"无需续期: {len(all_skipped)} 个")
    log(f"失败/异常: {len(all_failed)} 个")

    if all_renewed:
        lines = ["✅ <b>ACLClouds 自动续期成功</b>", ""]
        lines += [f"• {i}" for i in all_renewed]
        if all_failed:
            lines += ["", "⚠️ 以下项目失败："] + [f"• {i}" for i in all_failed]
        lines += ["", "ACLClouds Auto Renew"]
        send_all_push("\n".join(lines))
    elif all_failed:
        lines = ["❌ <b>ACLClouds 续期失败</b>", ""]
        lines += [f"• {i}" for i in all_failed]
        lines += ["", "ACLClouds Auto Renew"]
        send_all_push("\n".join(lines))
    else:
        log("所有账号均无需续期，不发送推送")
