"""
大模型客户端封装（Kimi/Moonshot + DeepSeek + MiniMax 三厂商）

三家 API 都兼容 OpenAI 的接口格式，直接用 openai SDK，只是 base_url 不同。
路由规则：模型名以 deepseek 开头 → DeepSeek；以 minimax 开头 → MiniMax；其余 → Moonshot(Kimi)。
API Key 从 backend.config 读取（config.py 里是从 .env 环境变量加载的，不要在这里硬编码）。

能力差异（重要）：
- DeepSeek 不支持读图：视觉识别走 Moonshot 或 MiniMax（跟随当前所选主模型厂商）；
- deepseek-reasoner 不支持函数调用：chat_with_tools 自动降级为 deepseek-chat；
- Moonshot 内置联网搜索($web_search)：仅 Moonshot 模型可用；
- MiniMax M 系列默认带思考前缀（会混入正文破坏 JSON 输出）：调用时统一关闭思考。
"""
import base64
import json
import logging
import random
import time

from openai import (OpenAI, RateLimitError, InternalServerError,
                    APITimeoutError, APIConnectionError, APIStatusError)

from backend.config import (MOONSHOT_API_KEY, MOONSHOT_BASE_URL, MOONSHOT_MODEL,
                            MOONSHOT_VISION_MODEL,
                            DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
                            MINIMAX_API_KEY, MINIMAX_BASE_URL, MINIMAX_MODEL,
                            MINIMAX_VISION_MODEL, DATA_SOURCE_BASE)

logger = logging.getLogger(__name__)

# 可自动重试的“临时性”错误：限流/服务过载(429)、5xx、超时、连接中断。
# Moonshot 偶发 429 engine_overloaded_error，退避重试即可，不该直接失败。
_RETRYABLE = (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError)
_MAX_RETRIES = 5          # 首次之外最多再重试的次数
_BASE_DELAY = 2.0         # 退避基准秒数（指数增长 + 抖动）


def _is_deepseek(model: str) -> bool:
    return bool(model) and model.lower().startswith("deepseek")


def _is_minimax(model: str) -> bool:
    return bool(model) and model.lower().startswith("minimax")


def _current_selected_model() -> str:
    """当前网页所选主模型（读 workspace/model_setting.json，与 skill_runner 同一份）。
    在 kimi_client 内直读避免与 skill_runner 循环导入；仅供 ocr_images 选厂商用。"""
    try:
        p = DATA_SOURCE_BASE / "model_setting.json"
        if p.exists():
            m = (json.loads(p.read_text(encoding="utf-8-sig")) or {}).get("model")
            if m:
                return m
    except Exception:
        pass
    return MOONSHOT_MODEL


def get_client(model: str = None) -> OpenAI:
    """按模型名路由到对应厂商的 OpenAI 兼容客户端。"""
    if _is_deepseek(model):
        if not DEEPSEEK_API_KEY:
            raise RuntimeError(
                "未配置 DEEPSEEK_API_KEY，请在项目根目录的 .env 文件里设置后重启服务"
            )
        return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    if _is_minimax(model):
        if not MINIMAX_API_KEY:
            raise RuntimeError(
                "未配置 MINIMAX_API_KEY，请在项目根目录的 .env 文件里设置后重启服务"
            )
        return OpenAI(api_key=MINIMAX_API_KEY, base_url=MINIMAX_BASE_URL)
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


def chat(messages: list[dict], model: str = None, temperature: float = 1.0,
         max_tokens: int = None) -> str:
    """最基础的对话调用：传入messages（OpenAI格式），返回模型回复的文本。
    max_tokens 仅对 DeepSeek/MiniMax 生效：推理类模型的思考会占用输出预算，
    需要较长推理链的任务（如评分）应显式调大。"""
    model = model or _current_selected_model()
    client = get_client(model)
    extra = {}
    if _is_deepseek(model):
        temperature = min(temperature, 1.0)  # DeepSeek 的 temperature 上限 1.0
        extra["max_tokens"] = max_tokens or 16384   # 推理模型思考占预算，8192 会截断长章节 JSON
    if _is_minimax(model):
        temperature = min(temperature, 1.0)
        extra["max_tokens"] = max_tokens or 16384   # 8192 会截断长章节 JSON（曾只保留前3小节）
        # M 系列默认开思考，<think> 前缀会混入正文破坏 JSON 输出：统一关闭
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
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
    “某科目金额”），不必通读全页——用于大扫描件定点取数，输出更短更准。
    厂商选择：跟随当前所选主模型——选 MiniMax 走 MiniMax，否则走 Moonshot；
    DeepSeek 不支持读图，选 DeepSeek 时回退 Moonshot（未配置则试 MiniMax）。"""
    if not images:
        return ""
    sel = _current_selected_model()
    if _is_minimax(sel) and MINIMAX_API_KEY:
        client = get_client(sel)
        vision_model = MINIMAX_VISION_MODEL or sel
        extra_body = {"thinking": {"type": "disabled"}}   # 关思考，避免 <think> 混入识别结果
    elif MOONSHOT_API_KEY:
        client = get_client(MOONSHOT_MODEL)
        vision_model = MOONSHOT_VISION_MODEL or MOONSHOT_MODEL
        extra_body = None
    elif MINIMAX_API_KEY:   # Moonshot 未配置/不可用时回退 MiniMax
        client = get_client(MINIMAX_MODEL)
        vision_model = MINIMAX_VISION_MODEL or MINIMAX_MODEL
        extra_body = {"thinking": {"type": "disabled"}}
    else:
        raise RuntimeError("未配置任何支持读图的模型密钥（MOONSHOT/MINIMAX），无法视觉识别")
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
    extra = {"extra_body": extra_body} if extra_body else {}
    resp = _create(
        client,
        model=vision_model,
        messages=[{"role": "user", "content": content}],
        temperature=1.0,   # kimi-k3 要求 temperature=1.0；MiniMax 上限同为 1.0
        **extra,
    )
    return resp.choices[0].message.content or ""


def chat_with_tools(messages: list[dict], tools: list, tool_executor,
                    model: str = None, temperature: float = 1.0,
                    max_rounds: int = 40) -> str:
    """带函数调用(function calling)的对话：模型若请求调用工具，就用 tool_executor 执行、
    把结果回喂，循环直到模型给出最终文本回复。
    注：deepseek-reasoner 官方不支持函数调用，自动降级为 deepseek-chat。

    tool_executor(name: str, arguments: dict) -> str

    注：max_rounds 曾为 16，实测 MiniMax-M3 生成长章节一轮就发近百次工具调用，
    16 轮常在关键材料（如审计报告）还没读到时就被强制交卷，导致表格填成取数说明，故提到 40。
    """
    model = model or _current_selected_model()
    if _is_deepseek(model) and "reasoner" in model.lower():
        logger.warning(f"[chat_with_tools] {model} 不支持函数调用，自动降级为 {DEEPSEEK_MODEL}")
        model = DEEPSEEK_MODEL
    client = get_client(model)
    extra = {}
    if _is_deepseek(model):
        temperature = min(temperature, 1.0)  # DeepSeek 的 temperature 上限 1.0
        extra["max_tokens"] = 16384          # 推理模型思考占预算，8192 会截断长章节 JSON
        # DeepSeek 只认 type=function：过滤掉 Moonshot 内置工具（builtin_function 等）
        tools = [t for t in (tools or []) if t.get("type") == "function"]
    if _is_minimax(model):
        temperature = min(temperature, 1.0)
        extra["max_tokens"] = 16384                  # 8192 会截断长章节 JSON（曾只保留前3小节）
        # MiniMax 同样只认 type=function；且必须关思考，否则 <think> 破坏 JSON 输出
        tools = [t for t in (tools or []) if t.get("type") == "function"]
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
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
            # （仅 Moonshot 模型支持；DeepSeek 无此能力，返回提示让其基于已有信息作答）
            if name == "$web_search":
                logger.info(f"[工具调用] $web_search 参数={tc.function.arguments}")
                if _is_deepseek(model) or _is_minimax(model):
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
