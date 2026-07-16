# -*- coding: utf-8 -*-
"""Twitter/X — check if twitter-cli or bird CLI is available."""

from .base import Channel
from agent_reach.probe import probe_command


class TwitterChannel(Channel):
    name = "twitter"
    description = "Twitter/X 推文"
    backends = ["twitter-cli", "OpenCLI", "bird CLI (legacy)"]
    tier = 1

    def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse
        d = urlparse(url).netloc.lower()
        return "x.com" in d or "twitter.com" in d

    def check(self, config=None):
        """Probe candidates in order; first fully-usable backend wins.

        与其他多后端渠道同一套两段式：先收集全部候选状态，第一个 ok 获胜；
        没有 ok 才轮到第一个 warn——否则「装了但未登录」的 twitter-cli
        会把排在后面、完整可用的 OpenCLI 挡在门外。
        """
        self.active_backend = None
        findings = []

        for backend in self.ordered_backends(config):
            if backend == "twitter-cli":
                result = self._check_twitter_cli(config)
            elif backend == "OpenCLI":
                result = self._check_opencli()
            elif backend == "bird CLI (legacy)":
                result = self._check_bird()
            else:
                continue

            if result is None:
                continue  # 未安装——不参与候选
            findings.append((backend, *result))

        for wanted in ("ok", "warn"):
            for backend, status, message in findings:
                if status == wanted:
                    self.active_backend = backend
                    return status, message

        if findings:  # 只剩 broken/timeout 候选
            return "error", "\n".join(m for _, _, m in findings)

        return "warn", (
            "Twitter CLI 未安装。安装方式：\n"
            "  pipx install twitter-cli\n"
            "或：\n"
            "  uv tool install twitter-cli"
        )

    def _check_twitter_cli(self, config=None):
        """探测 twitter-cli。返回 None 表示未安装，否则返回 (status, message)。

        为避免触发系统 Keychain 弹窗：
        1. 优先使用不敏感的 `twitter --version` 探测其是否存在。
        2. 若存在，检查环境变量或系统配置中是否已提供凭证。
           若已提供，安全调用 `twitter status` 检查可用性；
           若无凭证，必定未认证，直接返回 warn，避免运行 status 触发降级读取 Chrome 导致弹窗。
        """
        import os

        # 1. 快速安全地探测是否存在
        probe_exist = probe_command(
            "twitter", ["--version"], timeout=10, retries=1, package="twitter-cli"
        )
        if probe_exist.status == "missing":
            return None
        if probe_exist.status == "broken":
            return "error", "twitter-cli 命令存在但无法执行。\n" + probe_exist.hint
        if probe_exist.status == "timeout":
            return "error", "twitter-cli 探测超时。\n" + probe_exist.hint

        # 2. 判断凭证是否就绪以防弹窗
        has_env = os.environ.get("TWITTER_AUTH_TOKEN") and os.environ.get("TWITTER_CT0")
        has_config = False
        if config:
            has_config = config.get("twitter_auth_token") and config.get("twitter_ct0")

        if not (has_env or has_config):
            # 无凭证，不调用可能导致自动解密读取的 `twitter status`，免除弹窗
            return "warn", (
                "twitter-cli 已安装但未认证。设置方式：\n"
                "  export TWITTER_AUTH_TOKEN=\"xxx\"\n"
                "  export TWITTER_CT0=\"yyy\"\n"
                "或确保已在浏览器中登录 x.com 并配置好 OpenCLI 绕过弹窗"
            )

        # 3. 凭证存在，安全校验状态
        # 若当前进程环境变量中没有凭证但配置中有，则临时注入环境变量中，以防子进程降级读取本地 Chrome
        old_env_token = os.environ.get("TWITTER_AUTH_TOKEN")
        old_env_ct0 = os.environ.get("TWITTER_CT0")
        if config and not (old_env_token and old_env_ct0):
            os.environ["TWITTER_AUTH_TOKEN"] = config.get("twitter_auth_token") or ""
            os.environ["TWITTER_CT0"] = config.get("twitter_ct0") or ""

        try:
            probe = probe_command(
                "twitter", ["status"], timeout=15, retries=1, package="twitter-cli"
            )
        finally:
            if old_env_token is None:
                os.environ.pop("TWITTER_AUTH_TOKEN", None)
            else:
                os.environ["TWITTER_AUTH_TOKEN"] = old_env_token
            if old_env_ct0 is None:
                os.environ.pop("TWITTER_CT0", None)
            else:
                os.environ["TWITTER_CT0"] = old_env_ct0

        if probe.status == "timeout":
            return "error", "twitter-cli 健康检查超时（已重试 1 次）。\n" + probe.hint

        output = probe.output
        if "ok: true" in output:
            return "ok", (
                "twitter-cli 完整可用（搜索、读推文、时间线、长文/Article、"
                "用户查询、Thread）"
            )
        if "not_authenticated" in output:
            return "warn", (
                "twitter-cli 已安装但未认证。设置方式：\n"
                "  export TWITTER_AUTH_TOKEN=\"xxx\"\n"
                "  export TWITTER_CT0=\"yyy\"\n"
                "或确保已在浏览器中登录 x.com"
            )
        return "warn", (
            "twitter-cli 已安装但认证检查失败。运行：\n"
            "  twitter -v status 查看详细信息"
        )

    def _check_opencli(self):
        """OpenCLI candidate. None = not installed."""
        from agent_reach.backends import opencli_status

        st = opencli_status()
        if not st.installed:
            return None
        if st.broken:
            return "error", st.hint
        if st.ready:
            return "ok", (
                "OpenCLI 可用（复用浏览器登录态）。用法："
                "opencli twitter search/article/user-posts -f yaml"
            )
        return "warn", st.hint

    def _check_bird(self):
        """探测 bird/birdx（legacy 回退）。返回 None 表示均未安装，否则返回 (status, message)。"""
        last_failure = None
        for cmd in ("bird", "birdx"):
            probe = probe_command(
                cmd, ["check"], timeout=15, retries=1, package="@steipete/bird"
            )
            if probe.status == "missing":
                continue
            if probe.status == "broken":
                last_failure = (
                    "error",
                    f"{cmd} 命令存在但无法执行（bird 是 npm 包，可用 "
                    "npm install -g @steipete/bird 重装）。\n" + probe.hint,
                )
                continue  # bird 坏了再试 birdx
            if probe.status == "timeout":
                last_failure = (
                    "error",
                    f"{cmd} 健康检查超时（已重试 1 次）。\n" + probe.hint,
                )
                continue

            output = probe.output
            if probe.ok:
                return "ok", "bird CLI 可用（读取、搜索推文，含长文/X Article）"
            if "Missing credentials" in output or "missing" in output.lower():
                return "warn", (
                    "bird CLI 已安装但未配置认证。设置环境变量：\n"
                    "  export AUTH_TOKEN=\"xxx\"\n"
                    "  export CT0=\"yyy\""
                )
            return "warn", (
                "bird CLI 已安装但认证检查失败。"
            )
        return last_failure
