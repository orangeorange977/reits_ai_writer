"""天眼查查询客户端。

本地部署优先复用已登录的 ``tyc`` CLI；没有 CLI 时才回退旧 MCP Key。
所有返回都保留原始文本，供数据中间层落证据和查询时间，不让模型凭印象补数。

对外只暴露一个 call(tool_name, arguments) -> str：返回天眼查那边的 Markdown 文本
（或一段可读的错误说明，保证不会因为查询失败而中断整章生成）。
"""
import json
import logging
import shutil
import subprocess

import httpx

from backend.config import TIANYANCHA_MCP_URL, TIANYANCHA_MCP_KEY

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2025-06-18"


def is_enabled() -> bool:
    return bool(shutil.which("tyc") or TIANYANCHA_MCP_KEY)


_CLI_COMMANDS = {
    "search_companies": ("company", "companies"),
    "get_company_basic_profile": ("company", "registration-info"),
    "get_company_profile": ("company", "profile"),
    "get_actual_controller": ("company", "actual-controller"),
    "get_company_people": ("company", "key-personnel"),
}


def _cli_call(tool_name: str, arguments: dict, timeout: float) -> str:
    executable = shutil.which("tyc")
    command = _CLI_COMMANDS.get(tool_name)
    if not executable or not command:
        return ""
    query = str(arguments.get("query") or arguments.get("company_name") or "").strip()
    if not query:
        return "（天眼查查询缺少企业名称）"
    argv = [executable, *command, query]
    if tool_name in {"search_companies", "get_company_people"}:
        argv += ["--pageNum", str(arguments.get("pageNum") or arguments.get("page") or 1),
                 "--pageSize", str(arguments.get("pageSize") or arguments.get("page_size") or 10)]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        raise RuntimeError(output or f"tyc 退出码 {completed.returncode}")
    return output or "（天眼查空结果）"


def _headers(session_id: str = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {TIANYANCHA_MCP_KEY}",
    }
    if session_id:
        h["Mcp-Session-Id"] = session_id
    return h


def _parse(resp) -> list:
    """兼容 application/json 与 text/event-stream(SSE) 两种返回，取出所有 JSON 消息。"""
    if "text/event-stream" in resp.headers.get("content-type", ""):
        msgs = []
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    try:
                        msgs.append(json.loads(data))
                    except Exception:
                        pass
        return msgs
    try:
        return [resp.json()]
    except Exception:
        return []


def _result_for(msgs: list, rpc_id):
    for m in msgs:
        if isinstance(m, dict) and m.get("id") == rpc_id:
            if m.get("error"):
                raise RuntimeError(m["error"].get("message", "天眼查 MCP 返回错误"))
            return m.get("result")
    for m in msgs:  # 兜底：SSE 里没回显 id 时，取第一个带 result 的
        if isinstance(m, dict) and "result" in m:
            return m["result"]
    return None


def call(tool_name: str, arguments: dict, timeout: float = 60.0) -> str:
    """调用天眼查某个工具，返回其 Markdown 文本；失败返回可读的错误说明（不抛异常）。"""
    if shutil.which("tyc") and tool_name in _CLI_COMMANDS:
        try:
            return _cli_call(tool_name, arguments or {}, timeout)
        except Exception as e:
            logger.warning("天眼查 CLI 查询失败 [%s] %s: %s", tool_name, arguments, e)
            if not TIANYANCHA_MCP_KEY:
                return f"（天眼查 CLI 查询失败：{e}）"
    if not TIANYANCHA_MCP_KEY:
        return "（未配置天眼查密钥，无法查询企业信息）"
    try:
        with httpx.Client(timeout=timeout) as client:
            # 1) initialize
            r = client.post(TIANYANCHA_MCP_URL, headers=_headers(), json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
                           "clientInfo": {"name": "reit-ai", "version": "1.0"}},
            })
            session_id = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
            # 2) notifications/initialized
            client.post(TIANYANCHA_MCP_URL, headers=_headers(session_id),
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            # 3) tools/call
            r3 = client.post(TIANYANCHA_MCP_URL, headers=_headers(session_id), json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments or {}},
            })
            result = _result_for(_parse(r3), 2)
        if not result:
            return "（天眼查无返回）"
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        out = "\n".join(texts).strip()
        return out or "（天眼查空结果）"
    except Exception as e:
        logger.warning(f"天眼查查询失败 [{tool_name}] {arguments}: {e}")
        return f"（天眼查查询失败：{e}）"
