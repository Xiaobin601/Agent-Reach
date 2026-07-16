# -*- coding: utf-8 -*-
"""Auto-extract cookies from local browsers for all supported platforms.

Supports: Chrome, Firefox, Edge, Brave, Opera
Extracts: Twitter, XiaoHongShu, Bilibili cookies in one shot.

Usage:
    agent-reach configure --from-browser chrome
"""

from typing import Dict, List, Tuple

# Platform cookie specs: (platform_name, domain_pattern, needed_cookies)
PLATFORM_SPECS = [
    {
        "name": "Twitter/X",
        "domains": [".x.com", ".twitter.com"],
        "cookies": ["auth_token", "ct0"],
        "config_key": "twitter",
    },
    {
        "name": "XiaoHongShu",
        "domains": [".xiaohongshu.com"],
        "cookies": None,  # None = grab all cookies as header string
        "config_key": "xhs",
    },
    {
        "name": "Bilibili",
        "domains": [".bilibili.com"],
        "cookies": ["SESSDATA", "bili_jct"],
        "config_key": "bilibili",
    },
    {
        "name": "Xueqiu",
        "domains": [".xueqiu.com", "xueqiu.com"],
        "cookies": None,  # grab all — xq_a_token + session cookies required
        "config_key": "xueqiu",
    },
]


def _extract_via_opencli() -> dict:
    """尝试通过 OpenCLI 守护进程获取各大平台的 Cookies 列表。

    返回的结构与 extract_all 降级后的 raw 提取结构一致，
    例如：
    {
        "twitter": {"auth_token": "xxx", "ct0": "yyy"},
        "xhs": {"cookie_string": "a=1; b=2; ..."},
        ...
    }
    """
    import urllib.request
    import json
    import uuid

    url = "http://localhost:19825/command"
    headers = {
        "X-OpenCLI": "1",
        "Content-Type": "application/json"
    }

    # 1. 先检测 opencli 守护进程是否连接且 Extension 在线
    try:
        req = urllib.request.Request("http://localhost:19825/status", method="GET")
        req.add_header("X-OpenCLI", "1")
        with urllib.request.urlopen(req, timeout=2) as resp:
            status_data = json.loads(resp.read().decode("utf-8"))
            if not (status_data.get("ok") and status_data.get("extensionConnected")):
                return {}
    except Exception:
        return {}

    # 2. 依次提取
    results = {}

    # 提取映射 (platform_key, domain_query, needed_cookies_list)
    targets = [
        ("twitter", ".x.com", ["auth_token", "ct0"]),
        ("xhs", "xiaohongshu.com", None),
        ("bilibili", ".bilibili.com", ["SESSDATA", "bili_jct"]),
        ("xueqiu", "xueqiu.com", None),
    ]

    for platform_key, domain, needed_cookies in targets:
        body = {
            "id": str(uuid.uuid4()),
            "action": "cookies",
            "session": "agent-reach-sync",
            "domain": domain
        }
        try:
            post_data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(url, data=post_data, method="POST")
            req.add_header("X-OpenCLI", "1")
            req.add_header("Content-Type", "application/json")

            with urllib.request.urlopen(req, timeout=5) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                if not resp_data.get("ok"):
                    continue

                raw_cookies = resp_data.get("data", [])

                # 适配 extract_all 原始逻辑
                class _Cookie:
                    def __init__(self, name, value, domain_val):
                        self.name = name
                        self.value = value
                        self.domain = domain_val

                cookie_jar = [_Cookie(c.get("name", ""), c.get("value", ""), c.get("domain", "")) for c in raw_cookies]

                platform_cookies = {}
                all_cookies_for_domain = []

                spec_domains = [domain] if not domain.startswith(".") else [domain, domain[1:]]
                if platform_key == "twitter":
                    spec_domains = [".x.com", ".twitter.com"]

                for cookie in cookie_jar:
                    domain_match = any(
                        cookie.domain.endswith(d) or cookie.domain == d.lstrip(".")
                        for d in spec_domains
                    )
                    if not domain_match:
                        continue

                    all_cookies_for_domain.append(cookie)
                    if needed_cookies is not None:
                        if cookie.name in needed_cookies:
                            platform_cookies[cookie.name] = cookie.value

                if needed_cookies is None:
                    if all_cookies_for_domain:
                        cookie_str = "; ".join(
                            f"{c.name}={c.value}" for c in all_cookies_for_domain
                        )
                        results[platform_key] = {"cookie_string": cookie_str}
                else:
                    if platform_cookies:
                        results[platform_key] = platform_cookies
        except Exception:
            continue

    return results


def _extract_via_browser_tools() -> dict:
    """尝试通过本地的 browser-tools CDP 连接获取 Cookies。

    返回的结构与 extract_all 降级后的 raw 提取结构一致。
    """
    import subprocess
    import shutil
    import os

    # 1. 快速检查 node 是否存在
    if not shutil.which("node"):
        return {}

    tools_dir = "/Users/xiaobin/ai-study/badlogic/pi-skills/browser-tools"
    script_path = os.path.join(tools_dir, "browser-cookies.js")
    if not os.path.exists(script_path):
        return {}

    try:
        # 执行脚本，抓取 stdout
        r = subprocess.run(
            ["node", script_path],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=8
        )
        if r.returncode != 0:
            return {}

        # 解析控制台输出
        lines = r.stdout.splitlines()
        raw_cookies = []
        current = {}

        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                if current and "name" in current:
                    raw_cookies.append(current)
                    current = {}
                continue

            if line.startswith("  "):
                # 这是子属性，例如 "  domain: xxx"
                if ":" in line_strip:
                    k, _, v = line_strip.partition(":")
                    current[k.strip()] = v.strip()
            else:
                # 这是主名，例如 "name: value"
                if ":" in line_strip:
                    k, _, v = line_strip.partition(":")
                    current["name"] = k.strip()
                    current["value"] = v.strip()

        if current and "name" in current:
            raw_cookies.append(current)

        # 转换为适配结构
        class _Cookie:
            def __init__(self, name, value, domain_val):
                self.name = name
                self.value = value
                self.domain = domain_val

        cookie_jar = [_Cookie(c.get("name", ""), c.get("value", ""), c.get("domain", "")) for c in raw_cookies]

        results = {}
        targets = [
            ("twitter", ".x.com", ["auth_token", "ct0"]),
            ("xhs", "xiaohongshu.com", None),
            ("bilibili", ".bilibili.com", ["SESSDATA", "bili_jct"]),
            ("xueqiu", "xueqiu.com", None),
        ]

        for platform_key, domain, needed_cookies in targets:
            platform_cookies = {}
            all_cookies_for_domain = []

            spec_domains = [domain] if not domain.startswith(".") else [domain, domain[1:]]
            if platform_key == "twitter":
                spec_domains = [".x.com", ".twitter.com"]

            for cookie in cookie_jar:
                domain_match = any(
                    cookie.domain.endswith(d) or cookie.domain == d.lstrip(".")
                    for d in spec_domains
                )
                if not domain_match:
                    continue

                all_cookies_for_domain.append(cookie)
                if needed_cookies is not None:
                    if cookie.name in needed_cookies:
                        platform_cookies[cookie.name] = cookie.value

            if needed_cookies is None:
                if all_cookies_for_domain:
                    cookie_str = "; ".join(
                        f"{c.name}={c.value}" for c in all_cookies_for_domain
                    )
                    results[platform_key] = {"cookie_string": cookie_str}
            else:
                if platform_cookies:
                    results[platform_key] = platform_cookies

        return results
    except Exception:
        return {}


def extract_all(browser: str = "chrome") -> Dict[str, dict]:
    """
    Extract cookies for all supported platforms from the specified browser.

    Returns:
        {
            "twitter": {"auth_token": "xxx", "ct0": "yyy"},
            "xhs": {"cookie_string": "a=1; b=2; ..."},
            "bilibili": {"SESSDATA": "xxx", "bili_jct": "yyy"},
        }
    """
    # 优先使用 chrome 专用免弹窗方式
    if browser.lower() == "chrome":
        # 1. 优先尝试通过 OpenCLI 守护进程获取，以 100% 避免 Keychain 弹窗
        opencli_cookies = _extract_via_opencli()
        if opencli_cookies:
            return opencli_cookies

        # 2. 如果无 OpenCLI，尝试通过 browser-tools 免弹窗获取
        bt_cookies = _extract_via_browser_tools()
        if bt_cookies:
            return bt_cookies

    # Try rookiepy first (Rust-based, more stable), fallback to browser_cookie3
    use_rookiepy = False
    try:
        import rookiepy
        use_rookiepy = True
    except ImportError:
        try:
            import browser_cookie3
        except ImportError:
            raise RuntimeError(
                "Cookie extraction requires rookiepy or browser_cookie3.\n"
                "Install: pip install rookiepy  (recommended)\n"
                "     or: pip install browser-cookie3"
            )

    browser = browser.lower()
    supported = ["chrome", "firefox", "edge", "brave", "opera"]
    if browser not in supported:
        raise ValueError(
            f"Unsupported browser: {browser}. Supported: {', '.join(supported)}"
        )

    if use_rookiepy:
        # rookiepy returns list of dicts with name/value/domain/path keys
        try:
            browser_funcs = {
                "chrome": rookiepy.chrome,
                "firefox": rookiepy.firefox,
                "edge": rookiepy.edge,
                "brave": rookiepy.brave,
                "opera": rookiepy.opera,
            }
            raw_cookies = browser_funcs[browser]()
            # Wrap into objects with .name, .value, .domain for compatibility
            class _Cookie:
                def __init__(self, d):
                    self.name = d.get("name", "")
                    self.value = d.get("value", "")
                    self.domain = d.get("domain", "")
            cookie_jar = [_Cookie(c) for c in raw_cookies]
        except Exception as e:
            raise RuntimeError(
                f"Could not read {browser} cookies via rookiepy: {e}\n"
                f"Make sure {browser} is closed and you have permission."
            )
    else:
        browser_funcs = {
            "chrome": browser_cookie3.chrome,
            "firefox": browser_cookie3.firefox,
            "edge": browser_cookie3.edge,
            "brave": browser_cookie3.brave,
            "opera": browser_cookie3.opera,
        }
        try:
            cookie_jar = browser_funcs[browser]()
        except Exception as e:
            raise RuntimeError(
                f"Could not read {browser} cookies: {e}\n"
                f"Make sure {browser} is closed and you have permission."
            )

    results = {}

    for spec in PLATFORM_SPECS:
        platform_cookies = {}
        all_cookies_for_domain = []

        for cookie in cookie_jar:
            # Check if cookie belongs to this platform
            domain_match = any(
                cookie.domain.endswith(d) or cookie.domain == d.lstrip(".")
                for d in spec["domains"]
            )
            if not domain_match:
                continue

            all_cookies_for_domain.append(cookie)

            if spec["cookies"] is not None:
                if cookie.name in spec["cookies"]:
                    platform_cookies[cookie.name] = cookie.value

        if spec["cookies"] is None:
            # Grab all as header string
            if all_cookies_for_domain:
                cookie_str = "; ".join(
                    f"{c.name}={c.value}" for c in all_cookies_for_domain
                )
                results[spec["config_key"]] = {"cookie_string": cookie_str}
        else:
            if platform_cookies:
                results[spec["config_key"]] = platform_cookies

    return results


def _open_owner_only(path: str):
    """Open *path* for writing, atomically creating it with mode 0o600.

    Mirrors the pattern used by Config.save() in config.py: O_WRONLY|O_CREAT|
    O_TRUNC + an explicit mode argument so the file is never briefly
    world-readable between open() and a later os.chmod(). On Windows (or any
    OS that rejects the open flags) we fall back to a plain open().
    """
    import os
    import stat

    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,  # 0o600
        )
        if os.name != "nt":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return os.fdopen(fd, "w", encoding="utf-8")
    except OSError:
        handle = open(path, "w", encoding="utf-8")
        if os.name != "nt":
            os.chmod(path, 0o600)
        return handle


def _sync_xfetch_session(auth_token: str, ct0: str) -> None:
    """Sync Twitter credentials to ~/.config/xfetch/session.json (legacy xreach compat)."""
    import json
    import os

    try:
        from agent_reach.utils.paths import make_private_dir

        xfetch_dir = os.path.join(os.path.expanduser("~"), ".config", "xfetch")
        make_private_dir(xfetch_dir)
        session_path = os.path.join(xfetch_dir, "session.json")
        session_data: dict = {}
        if os.path.exists(session_path):
            try:
                with open(session_path, "r", encoding="utf-8") as sf:
                    session_data = json.load(sf)
            except (json.JSONDecodeError, OSError):
                session_data = {}
        session_data["authToken"] = auth_token
        session_data["ct0"] = ct0
        with _open_owner_only(session_path) as sf:
            json.dump(session_data, sf, indent=2)
    except Exception:
        # Non-fatal: agent-reach config is the source of truth, xfetch sync is best-effort
        pass


def _sync_bird_env(auth_token: str, ct0: str) -> None:
    """Write Twitter credentials to ~/.config/bird/credentials.env for bird CLI.

    bird reads AUTH_TOKEN and CT0 from environment variables. This writes a
    shell-sourceable file so users can `source ~/.config/bird/credentials.env`.
    Values are passed through shlex.quote so a token containing a quote, $, or
    backtick cannot break out into shell syntax when the file is sourced.
    """
    import os
    import shlex

    try:
        from agent_reach.utils.paths import make_private_dir

        bird_dir = os.path.join(os.path.expanduser("~"), ".config", "bird")
        make_private_dir(bird_dir)
        env_path = os.path.join(bird_dir, "credentials.env")
        with _open_owner_only(env_path) as f:
            f.write(f"AUTH_TOKEN={shlex.quote(auth_token)}\n")
            f.write(f"CT0={shlex.quote(ct0)}\n")
    except Exception:
        # Non-fatal: agent-reach config is the source of truth, bird env sync is best-effort
        pass


# Alias for callers expecting the name _sync_bird_credentials
_sync_bird_credentials = _sync_bird_env


def configure_from_browser(browser: str, config) -> List[Tuple[str, bool, str]]:
    """
    Extract cookies and configure all found platforms.
    
    Returns list of (platform_name, success, message) tuples.
    """
    results_list = []

    try:
        extracted = extract_all(browser)
    except Exception as e:
        return [("Browser", False, str(e))]

    if not extracted:
        return [("All platforms", False,
                 f"No platform cookies found in {browser}. "
                 f"Make sure you're logged into Twitter, XiaoHongShu, etc. in {browser}.")]

    # Configure each found platform
    if "twitter" in extracted:
        tc = extracted["twitter"]
        if "auth_token" in tc and "ct0" in tc:
            config.set("twitter_auth_token", tc["auth_token"])
            config.set("twitter_ct0", tc["ct0"])
            # Legacy sync (best-effort)
            _sync_xfetch_session(tc["auth_token"], tc["ct0"])
            results_list.append(("Twitter/X", True, "auth_token + ct0"))
        else:
            found = ", ".join(tc.keys())
            missing = [k for k in ["auth_token", "ct0"] if k not in tc]
            results_list.append(("Twitter/X", False,
                                 f"Found {found}, but missing: {', '.join(missing)}. "
                                 f"Make sure you're logged into x.com in {browser}."))

    if "xhs" in extracted:
        cookie_str = extracted["xhs"].get("cookie_string", "")
        if cookie_str:
            config.set("xhs_cookie", cookie_str)
            n_cookies = len(cookie_str.split(";"))
            results_list.append(("XiaoHongShu", True, f"{n_cookies} cookies"))

    if "bilibili" in extracted:
        bc = extracted["bilibili"]
        if "SESSDATA" in bc:
            config.set("bilibili_sessdata", bc["SESSDATA"])
            if "bili_jct" in bc:
                config.set("bilibili_csrf", bc["bili_jct"])
            results_list.append(("Bilibili", True, "SESSDATA" +
                                 (" + bili_jct" if "bili_jct" in bc else "")))
        else:
            results_list.append(("Bilibili", False,
                                 f"No SESSDATA found. Make sure you're logged into bilibili.com in {browser}."))

    if "xueqiu" in extracted:
        cookie_str = extracted["xueqiu"].get("cookie_string", "")
        # Only save if xq_a_token is present — anonymous cookies are useless
        if cookie_str and "xq_a_token" in cookie_str:
            config.set("xueqiu_cookie", cookie_str)
            n_cookies = len(cookie_str.split(";"))
            results_list.append(("Xueqiu", True, f"{n_cookies} cookies (含 xq_a_token)"))
        elif cookie_str:
            results_list.append(("Xueqiu", False,
                                 f"找到 {len(cookie_str.split(';'))} 个 Cookie 但缺少 xq_a_token，"
                                 f"请先在 {browser} 中登录 xueqiu.com"))

    return results_list
