"""
大模型客户端封装（Kimi/Moonshot + DeepSeek 双厂商）

两家 API 都兼容 OpenAI 的接口格式，直接用 openai SDK，只是 base_url 不同。
路由规则：模型名以 deepseek 开头 → DeepSeek，否则 → Moonshot(Kimi)。
API Key 从 backend.config 读取（config.py 里是从 .env 环境变量加载的，不要在这里硬编码）。

能力差异（重要）：
- DeepSeek 不支持读图：ocr_images 始终走 Moonshot；
- deepseek-reasoner 不支持函数调用：chat_with_tools 自动降级为 deepseek-chat；
- Moonshot 内置联网搜索($web_search)：仅 Moonshot 模型可用。
"""
import base64
import json
import logging
import random
import re
import time

from openai import (OpenAI, RateLimitError, InternalServerError,
                    APITimeoutError, APIConnectionError, APIStatusError)

from backend.config import (MOONSHOT_API_KEY, MOONSHOT_BASE_URL, MOONSHOT_MODEL,
                            MOONSHOT_VISION_MODEL,
                            DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL)

logger = logging.getLogger(__name__)

# 可自动重试的“临时性”错误：限流/服务过载(429)、5xx、超时、连接中断。
# Moonshot 偶发 429 engine_overloaded_error，退避重试即可，不该直接失败。
_RETRYABLE = (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError)
_MAX_RETRIES = 5          # 首次之外最多再重试的次数
_BASE_DELAY = 2.0         # 退避基准秒数（指数增长 + 抖动）


def _is_deepseek(model: str) -> bool:
    return bool(model) and model.lower().startswith("deepseek")


def get_client(model: str = None) -> OpenAI:
    """按模型名路由到对应厂商的 OpenAI 兼容客户端。"""
    if _is_deepseek(model):
        if not DEEPSEEK_API_KEY:
            raise RuntimeError(
                "未配置 DEEPSEEK_API_KEY，请在项目根目录的 .env 文件里设置后重启服务"
            )
        return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL, max_retries=0)
    if not MOONSHOT_API_KEY:
        raise RuntimeError(
            "未配置 MOONSHOT_API_KEY，请在项目根目录的 .env 文件里设置后重启服务"
        )
    # SDK 自带重试关闭，由本模块 _create 统一控制；否则单次 90 秒超时会在 SDK
    # 内部再重试两次，外层无法感知，视觉任务一次可能卡 4～8 分钟。
    return OpenAI(api_key=MOONSHOT_API_KEY, base_url=MOONSHOT_BASE_URL, max_retries=0)


def _create(client: OpenAI, **kwargs):
    """调用 chat.completions.create，遇到临时性错误（过载/限流/5xx/超时）自动指数退避重试。"""
    last_err = None
    max_retries = int(kwargs.pop("_max_retries", _MAX_RETRIES))
    for attempt in range(max_retries + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except _RETRYABLE as e:
            last_err = e
        except APIStatusError as e:
            # 其它按状态码判断：仅 429 / 5xx 才重试，其余（如 400 参数错）立即抛出
            if getattr(e, "status_code", None) not in (429, 500, 502, 503, 504):
                raise
            last_err = e
        if attempt < max_retries:
            delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(f"[Kimi] 接口临时错误（{type(last_err).__name__}），"
                           f"{delay:.1f}s 后第 {attempt + 1}/{max_retries} 次重试")
            time.sleep(delay)
    raise last_err


def chat(messages: list[dict], model: str = None, temperature: float = 1.0) -> str:
    """最基础的对话调用：传入messages（OpenAI格式），返回模型回复的文本。"""
    model = model or MOONSHOT_MODEL
    client = get_client(model)
    extra = {}
    if _is_deepseek(model):
        temperature = min(temperature, 1.0)  # DeepSeek 的 temperature 上限 1.0
        extra["max_tokens"] = 8192           # 默认 4096 会把长章节 JSON 截断
    resp = _create(
        client,
        model=model,
        messages=messages,
        temperature=temperature,
        **extra,
    )
    return resp.choices[0].message.content


def ocr_images(images: list, model: str = None, instruction: str = "") -> str:
    """把若干张图片（PNG/JPG 字节）发给视觉模型识别文字。
    instruction 为空：逐字识别全部文字并按原文返回（默认，适合承诺函/营业执照等小件）。
    instruction 非空：只针对该问题在图中查找、只回相关内容（如“落款日期和落款单位”“审计意见段落”
    “某科目金额”），不必通读全页——用于大扫描件定点取数，输出更短更准。"""
    if not images:
        return ""
    # DeepSeek 不支持读图：视觉识别始终走 Moonshot（忽略传入的 model）
    client = get_client(MOONSHOT_MODEL)
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
        model=MOONSHOT_VISION_MODEL or MOONSHOT_MODEL,  # 视觉识别固定走 Moonshot（kimi-k3 支持读图）
        messages=[{"role": "user", "content": content}],
        temperature=1.0,   # kimi-k3 要求 temperature=1.0
        timeout=90.0,
        _max_retries=0,
    )
    return resp.choices[0].message.content or ""


def vision_page_markdown(image: bytes, instruction: str = "") -> str:
    """把单页扫描件精读成可替换页级缓存的完整 Markdown。

    与 ``ocr_images(..., instruction=...)`` 不同，这里即使给了业务关注点也必须返回整页，
    关注点只用于提醒模型提高相关表格/字段的识别精度，避免中间层丢掉上下文。
    """
    if not image:
        return ""
    client = get_client(MOONSHOT_MODEL)
    focus = (instruction or "").strip()
    prompt = (
        "请把这一页底稿完整转写为 Markdown。必须保留标题、正文层级、表格全部行列、"
        "金额正负号、千分位、小数、日期、单位、脚注和盖章/落款文字；不要总结，不要改写，"
        "无法辨认的单元格写【无法辨认】，不要猜测。表格请输出为 Markdown 表格。"
    )
    if focus:
        prompt += f"业务特别关注：{focus}。请在完整转写的前提下重点核对这些内容。"
    b64 = base64.b64encode(image).decode("ascii")
    resp = _create(
        client,
        model=MOONSHOT_VISION_MODEL or MOONSHOT_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
        temperature=1.0,
        timeout=90.0,
        _max_retries=0,
    )
    return resp.choices[0].message.content or ""


def vision_extract_json(images: list[bytes], instruction: str) -> dict:
    """Read selected evidence pages and return strict JSON for a caller-defined schema."""
    if not images:
        return {}
    client = get_client(MOONSHOT_MODEL)
    content = [{"type": "text", "text": (
        f"{instruction}\n"
        "只根据图片中能明确辨认的内容填写。无法确认的值必须使用空字符串，严禁猜测。"
        "金额保留图片原单位和完整数字。只输出一个合法 JSON 对象，不要代码块、解释或前后缀。"
    )}]
    for image in images:
        b64 = base64.b64encode(image).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    resp = _create(
        client,
        model=MOONSHOT_VISION_MODEL or MOONSHOT_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=1.0,
        timeout=90.0,
        _max_retries=0,
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    return json.loads(raw)


def chat_with_tools(messages: list[dict], tools: list, tool_executor,
                    model: str = None, temperature: float = 1.0,
                    max_rounds: int = 16) -> str:
    """带函数调用(function calling)的对话：模型若请求调用工具，就用 tool_executor 执行、
    把结果回喂，循环直到模型给出最终文本回复。
    注：deepseek-reasoner 官方不支持函数调用，自动降级为 deepseek-chat。

    tool_executor(name: str, arguments: dict) -> str
    """
    model = model or MOONSHOT_MODEL
    if _is_deepseek(model) and "reasoner" in model.lower():
        logger.warning(f"[chat_with_tools] {model} 不支持函数调用，自动降级为 {DEEPSEEK_MODEL}")
        model = DEEPSEEK_MODEL
    client = get_client(model)
    extra = {}
    if _is_deepseek(model):
        temperature = min(temperature, 1.0)  # DeepSeek 的 temperature 上限 1.0
        extra["max_tokens"] = 8192           # 默认 4096 会把长章节 JSON 截断
        # DeepSeek 只认 type=function：过滤掉 Moonshot 内置工具（builtin_function 等）
        tools = [t for t in (tools or []) if t.get("type") == "function"]
    msgs = list(messages)

    for i in range(max_rounds):
        resp = _create(
            client,
            model=model, messages=msgs, tools=tools,
            tool_choice="auto", temperature=temperature,
            **extra,
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            content = msg.content or ""
            if content.strip():
                return content
            # Built-in web search occasionally finishes with an empty assistant
            # message.  Never append that empty standalone assistant message:
            # Moonshot rejects it on the next request.  Make one tool-free final
            # answer call from the valid history instead.
            logger.warning(f"[chat_with_tools] 第{i+1}轮返回空正文且无工具调用，追问一次要求直接输出")
            final_messages = msgs + [{
                "role": "user",
                "content": "请现在直接基于已经取得的搜索结果输出最终结果本身（严格符合前面要求的 JSON），"
                           "不要再调用任何工具，不要输出空内容或解释。",
            }]
            final_resp = _create(
                client, model=model, messages=final_messages,
                temperature=temperature, **extra)
            final_content = final_resp.choices[0].message.content or ""
            if final_content.strip():
                return final_content
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
            # （仅 Moonshot 模型支持；DeepSeek 无此能力，返回提示让其基于已有信息作答）
            if name == "$web_search":
                logger.info(f"[工具调用] $web_search 参数={tc.function.arguments}")
                if _is_deepseek(model):
                    msgs.append({"role": "tool", "tool_call_id": tc.id, "name": name,
                                 "content": "（当前模型无联网搜索能力，请基于已提供的材料信息作答）"})
                else:
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
            **extra,
        )
        content = resp.choices[0].message.content or ""
        if content.strip():
            return content
        logger.warning("[chat_with_tools] 强制输出仍为空，重试")
    return ""


def web_search_json(query: str, output_instruction: str) -> dict:
    """Run one evidence-oriented Moonshot web search and return strict JSON.

    The returned object must include source URLs supplied by the model/search tool;
    callers should persist the raw object as provenance rather than only the answer.
    """
    messages = [
        {"role": "system", "content": (
            "你是企业公开信息检索员。必须先调用联网搜索，只采用公司官网、交易所、国家发展改革委、"
            "证监会等权威来源。无法确认时写空值，不得把未搜索到推断为不存在。"
        )},
        {"role": "user", "content": (
            f"检索问题：{query}\n\n{output_instruction}\n"
            "只输出合法 JSON 对象，不要代码块。sources 必须是数组，每项包含 title、url、published_at、quote。"
        )},
    ]
    raw = chat_with_tools(
        messages,
        [{"type": "builtin_function", "function": {"name": "$web_search"}}],
        lambda _name, _args: "",
        model=MOONSHOT_MODEL,
        temperature=1.0,
        max_rounds=4,
    )
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I)
    return json.loads(cleaned)
