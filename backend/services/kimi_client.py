"""
Kimi (Moonshot AI) 大模型客户端封装

Moonshot 的 API 兼容 OpenAI 的接口格式，所以直接用 openai SDK，
只是把 base_url 换成 Moonshot 的地址。API Key 从 backend.config 读取
（config.py 里是从 .env 环境变量加载的，不要在这里硬编码）。
"""
import base64
import json
import logging
import random
import time

from openai import (OpenAI, RateLimitError, InternalServerError,
                    APITimeoutError, APIConnectionError, APIStatusError)

from backend.config import (MOONSHOT_API_KEY, MOONSHOT_BASE_URL, MOONSHOT_MODEL,
                            MOONSHOT_VISION_MODEL)

logger = logging.getLogger(__name__)

# 可自动重试的“临时性”错误：限流/服务过载(429)、5xx、超时、连接中断。
# Moonshot 偶发 429 engine_overloaded_error，退避重试即可，不该直接失败。
_RETRYABLE = (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError)
_MAX_RETRIES = 5          # 首次之外最多再重试的次数
_BASE_DELAY = 2.0         # 退避基准秒数（指数增长 + 抖动）


def get_client() -> OpenAI:
    if not MOONSHOT_API_KEY:
        raise RuntimeError(
            "未配置 MOONSHOT_API_KEY，请在项目根目录的 .env 文件里设置后重启服务"
        )
    return OpenAI(api_key=MOONSHOT_API_KEY, base_url=MOONSHOT_BASE_URL)


def _create(client: OpenAI, **kwargs):
    """调用 chat.completions.create，遇到临时性错误（过载/限流/5xx/超时）自动指数退避重试。"""
    last_err = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except _RETRYABLE as e:
            last_err = e
        except APIStatusError as e:
            # 其它按状态码判断：仅 429 / 5xx 才重试，其余（如 400 参数错）立即抛出
            if getattr(e, "status_code", None) not in (429, 500, 502, 503, 504):
                raise
            last_err = e
        if attempt < _MAX_RETRIES:
            delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(f"[Kimi] 接口临时错误（{type(last_err).__name__}），"
                           f"{delay:.1f}s 后第 {attempt + 1}/{_MAX_RETRIES} 次重试")
            time.sleep(delay)
    raise last_err


def chat(messages: list[dict], model: str = None, temperature: float = 1.0) -> str:
    """最基础的对话调用：传入messages（OpenAI格式），返回模型回复的文本。"""
    client = get_client()
    resp = _create(
        client,
        model=model or MOONSHOT_MODEL,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content


def ocr_images(images: list, model: str = None, instruction: str = "") -> str:
    """把若干张图片（PNG/JPG 字节）发给视觉模型识别文字。
    instruction 为空：逐字识别全部文字并按原文返回（默认，适合承诺函/营业执照等小件）。
    instruction 非空：只针对该问题在图中查找、只回相关内容（如“落款日期和落款单位”“审计意见段落”
    “某科目金额”），不必通读全页——用于大扫描件定点取数，输出更短更准。"""
    if not images:
        return ""
    client = get_client()
    if (instruction or "").strip():
        prompt = (
            f"请在下面的图片中查找与“{instruction.strip()}”相关的内容，"
            "只返回相关的原文/数字/日期/名称（保留原样，不要翻译、不要改写、不要总结），"
            "并简要标明它出现在图中的位置（如“落款处”“第X项”）。"
            "与该问题无关的其它文字不用输出。若图中确实没有相关内容，请回复“未找到相关内容”。"
        )
    else:
        prompt = (
            "请逐字识别下面图片中的全部文字（包括日期、盖章/落款处、表格里的数字），"
            "按原文完整输出，保留数字和日期原样；不要翻译、不要总结、不要加解释。多张图片按顺序识别。"
        )
    content = [{"type": "text", "text": prompt}]
    for img in images:
        b64 = base64.b64encode(img).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})
    resp = _create(
        client,
        model=model or MOONSHOT_VISION_MODEL or MOONSHOT_MODEL,  # 默认用主模型（kimi-k3 支持读图）
        messages=[{"role": "user", "content": content}],
        temperature=1.0,   # kimi-k3 要求 temperature=1.0
    )
    return resp.choices[0].message.content or ""


def chat_with_tools(messages: list[dict], tools: list, tool_executor,
                    model: str = None, temperature: float = 1.0,
                    max_rounds: int = 16) -> str:
    """带函数调用(function calling)的对话：Kimi 若请求调用工具，就用 tool_executor 执行、
    把结果回喂，循环直到 Kimi 给出最终文本回复。

    tool_executor(name: str, arguments: dict) -> str
    """
    client = get_client()
    model = model or MOONSHOT_MODEL
    msgs = list(messages)

    for i in range(max_rounds):
        resp = _create(
            client,
            model=model, messages=msgs, tools=tools,
            tool_choice="auto", temperature=temperature,
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            content = msg.content or ""
            if content.strip():
                return content
            # 模型没调用工具却又没给正文（偶发空回复）：明确要求它现在直接给出最终结果
            logger.warning(f"[chat_with_tools] 第{i+1}轮返回空正文且无工具调用，追问一次要求直接输出")
            msgs.append({"role": "assistant", "content": ""})
            msgs.append({"role": "user",
                         "content": "请现在直接输出最终结果本身（严格符合前面要求的 JSON），"
                                    "不要再调用任何工具，不要输出空内容或解释。"})
            continue

        # 记录助手这轮的工具调用请求
        msgs.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })
        # 逐个执行并回喂结果
        for tc in tool_calls:
            name = tc.function.name
            # Moonshot 内置联网搜索：不自己执行，把参数原样回传，由 Moonshot 服务端完成搜索
            if name == "$web_search":
                logger.info(f"[工具调用] $web_search 参数={tc.function.arguments}")
                msgs.append({"role": "tool", "tool_call_id": tc.id, "name": name,
                             "content": tc.function.arguments or "{}"})
                continue
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            logger.info(f"[工具调用] {name} 参数={args}")  # 便于核实 Kimi 是否真的查了天眼查/材料/联网
            try:
                result = tool_executor(name, args)
            except Exception as e:
                logger.warning(f"工具执行失败 {name}: {e}")
                result = f"（工具执行失败：{e}）"
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    # 达到轮数上限：不再给工具，逼它用已查到的信息给出最终文本（最多再试 2 次拿到非空正文）
    logger.warning(f"[chat_with_tools] 已达工具调用轮数上限({max_rounds})，改为强制输出最终结果")
    msgs.append({"role": "user",
                 "content": "已达到工具调用上限。请立即基于已获取的信息直接输出最终结果本身"
                            "（严格符合前面要求的 JSON 格式），缺失项按前面的规则标注，"
                            "不要再调用任何工具，不要返回空内容。"})
    for _ in range(2):
        resp = _create(
            client, model=model, messages=msgs, temperature=temperature,
        )
        content = resp.choices[0].message.content or ""
        if content.strip():
            return content
        logger.warning("[chat_with_tools] 强制输出仍为空，重试")
    return ""
