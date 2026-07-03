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

# ── 单个项目续期（真实 UI 点击 + 人机验证 + captcha_token 直调 API）──
def renew_via_ui(page, tag: str, name: str, identifier: str, idx: int):
    """
    在 /projects 页面：
      1. 找到该项目卡片里的 Renew 按钮并点击 → 弹出 Anti-bot confirmation 弹窗
      2. 点击 div.auth-captcha-inner 触发验证码请求
      3. 监听网络响应，抓取后端返回的 captcha_token
         （抓包确认：勾选复选框后，前端会请求一个独立接口，
          返回形如 {"captcha_token": "eyJ..."} 的 JSON）
      4. 拿到 token 后，不再依赖弹窗是否自动提交/是否有确认按钮，
         直接携带 token 调用 POST /api/client/servers/{id}/upgrade/renew
      5. 根据返回的 success / expires_at 判断结果
    成功返回新的剩余天数（float，可能为 None 表示无法解析），失败抛异常。
    """
    from playwright.sync_api import TimeoutError as PWTimeout

    card = page.locator("div.client-card", has_text=name).first

    # 用于从网络响应里捕获 captcha_token
    captcha_token_holder = {"token": None, "source_url": None}

    def _on_response(res):
        try:
            ctype = res.headers.get("content-type") or ""
            if "application/json" not in ctype:
                return
            body = res.json()
            if isinstance(body, dict) and body.get("captcha_token"):
                if not captcha_token_holder["token"]:
                    captcha_token_holder["token"] = body["captcha_token"]
                    captcha_token_holder["source_url"] = res.url
        except Exception:
            pass

    page.on("response", _on_response)

    try:
        # ── 1. 点击 Renew 按钮 ─────────────────────────────
        log(f"[{tag}] [{name}] 查找并点击 Renew 按钮...")
        # 不依赖按钮文字（面板语言按账号不同可能是 Renew/Renouveler/续订 等），
        # 改为按 DOM 结构定位：续期按钮唯一地位于 div.projects-card-expiry 容器内，
        # 与 Gérer/Modifier/Supprimer 所在的 div.projects-card-actions 是分开的区块。
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

        # ── 3. 点击验证码复选框，触发 captcha_token 请求 ───────
        log(f"[{tag}] [{name}] 点击续期弹窗 captcha 复选框...")
        dialog.locator("div.auth-captcha-inner").click(timeout=10000)

        # ── 4. 轮询等待 captcha_token 被网络监听捕获（最多 30s）──
        # 期间检测是否弹出了"点击图块"这类二级挑战（风控升级），
        # 一旦发现就立刻导出弹窗完整 HTML，方便后续针对性写选择器，
        # 不用干等 30s 浪费时间。
        challenge_dumped = False
        for i in range(30):
            if captcha_token_holder["token"]:
                break
            if not challenge_dumped and i >= 3:
                try:
                    dialog_html = dialog.evaluate("el => el.outerHTML")
                    if "auth-captcha-inner" not in dialog_html or "Cliquez" in dialog_html \
                       or "Click on" in dialog_html or "select" in dialog_html.lower():
                        os.makedirs("screenshots", exist_ok=True)
                        dump_path = f"screenshots/acct{idx}_renew_{identifier}_challenge_dump.html"
                        with open(dump_path, "w", encoding="utf-8") as f:
                            f.write(dialog_html)
                        log_warn(f"[{tag}] [{name}] 检测到可能的二级验证挑战，"
                                 f"已导出弹窗 HTML 到 {dump_path}")
                        screenshot(page, f"acct{idx}_renew_{identifier}_b1_challenge")
                        challenge_dumped = True
                except Exception:
                    pass
            time.sleep(1)

        screenshot(page, f"acct{idx}_renew_{identifier}_b_captcha")

        if not captcha_token_holder["token"]:
            extra = "（弹窗 HTML 已导出，见 artifact 中的 challenge_dump.html）" if challenge_dumped else ""
            raise RuntimeError(f"30s 内未捕获到 captcha_token，验证码请求可能未触发{extra}")

        token = captcha_token_holder["token"]
        log(f"[{tag}] [{name}] captcha_token 已捕获（来源: {captcha_token_holder['source_url']}），"
            f"直接调用续期接口")

        # ── 5. 携带 token 直接调用续期 API ──────────────────
        renew_url = f"/api/client/servers/{identifier}/upgrade/renew"
        result = page.evaluate("""async ({url, token}) => {
            const xsrf = decodeURIComponent(
                (document.cookie.match(/XSRF-TOKEN=([^;]+)/) || [])[1] || ''
            );
            const r = await fetch(url, {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'X-XSRF-TOKEN': xsrf
                },
                body: JSON.stringify({captcha_token: token})
            });
            return {status: r.status, body: await r.text()};
        }""", {"url": renew_url, "token": token})

        screenshot(page, f"acct{idx}_renew_{identifier}_c_after")

        if result['status'] != 200:
            raise RuntimeError(f"续期请求失败 HTTP {result['status']}: {result['body'][:200]}")

        try:
            data = json.loads(result['body'])
        except Exception:
            raise RuntimeError(f"续期响应无法解析为 JSON: {result['body'][:200]}")

        if not data.get('success'):
            raise RuntimeError(f"续期返回失败: {data.get('message', '未知错误')}")

        log(f"[{tag}] [{name}] 续期成功 ✅ {data.get('message', '')}")

        # ── 6. 尝试关闭弹窗（如果还开着，不强制要求成功）──────
        try:
            cancel_btn = dialog.locator("button:has-text('Cancel')").first
            if cancel_btn.is_visible(timeout=1000):
                cancel_btn.click(timeout=2000)
        except Exception:
            pass

        if data.get('expires_at'):
            return parse_expires(data['expires_at'])
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
                    new_remaining = renew_via_ui(page, tag, name, identifier, idx)
                    if new_remaining is not None:
                        log(f"[{tag}] [{name}] 续期成功 ✅ {remaining:.2f}天 → {new_remaining:.2f}天")
                        renewed_list.append(
                            f"{tag} · {name}（{remaining:.1f}天 → {new_remaining:.1f}天）")
                    else:
                        log(f"[{tag}] [{name}] 续期成功 ✅（无法查询新过期时间）")
                        renewed_list.append(f"{tag} · {name}（续期前 {remaining:.1f} 天）")

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
