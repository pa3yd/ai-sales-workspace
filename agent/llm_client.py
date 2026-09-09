# -*- coding: utf-8 -*-
"""
DeepSeek 客户端：封装 API 调用，让其他模块不用关心网络细节。

只用 Python 标准库 urllib 实现，不需要 pip install 任何东西。
API Key 的两种配置方式（环境变量优先级更高）：
  1) 环境变量：setx DEEPSEEK_API_KEY sk-xxxxxx
  2) 配置文件：config.json 里的 DEEPSEEK_API_KEY 字段
"""

import json
import os
import re
import urllib.request
import urllib.error

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
)


class DeepSeekError(Exception):
    """自定义异常：API 调用出错时抛出，方便上层统一兜底"""
    pass


def load_config() -> dict:
    """读取 config.json，文件不存在就返回空字典"""
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_api_key():
    """返回 (api_key, 来源说明)；找不到时 api_key 为 None"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key, "环境变量 DEEPSEEK_API_KEY"

    cfg = load_config()
    key = (cfg.get("DEEPSEEK_API_KEY") or "").strip()
    if key and not key.lower().startswith("在这里填写"):
        return key, "config.json"
    return None, "未配置（请在 config.json 中填写，或设置环境变量 DEEPSEEK_API_KEY）"


class DeepSeekClient:

    def __init__(self, api_key: str, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    # ---------- 核心方法 ----------

    def chat(self, system_prompt: str, user_prompt: str,
             temperature: float = 0.2, max_tokens: int = 2000,
             json_mode: bool = True, timeout: int = 90) -> str:
        """和 DeepSeek 对话，返回模型回复的文本（字符串）"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            # 开启 JSON 输出模式，模型会更稳定地返回合法 JSON
            payload["response_format"] = {"type": "json_object"}

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise DeepSeekError(self._explain_error(e.code, detail))
        except urllib.error.URLError as e:
            raise DeepSeekError(
                f"网络连接失败：{e.reason}。如果你在公司网络环境，"
                f"可能需要设置代理（HTTP_PROXY / HTTPS_PROXY 环境变量）"
            )
        except Exception as e:
            raise DeepSeekError(f"未知错误：{e}")

        result = json.loads(raw)
        return result["choices"][0]["message"]["content"]

    def chat_json(self, system_prompt: str, user_prompt: str, **kwargs) -> dict:
        """
        在 chat() 基础上加一步：把返回的文本解析成 Python 字典。
        解析失败时自动降级：先用宽松解析；再失败则用正则硬抽 subject/body。
        """
        text = self.chat(system_prompt, user_prompt, json_mode=True, **kwargs)
        # 第 1 试：宽松 JSON 解析
        try:
            return parse_json_loose(text)
        except json.JSONDecodeError:
            pass
        # 第 2 试：正则硬抽（处理模型返回的 JSON 里含未转义换行/引号的常见情况）
        return extract_fields_loose(text)

    def check_key(self) -> str:
        """用一次极小的请求验证 API Key 是否可用，返回成功信息"""
        reply = self.chat(
            "You are a helpful assistant.",
            "Reply with one word: OK",
            json_mode=False,
            max_tokens=10,
            timeout=30,
        )
        return reply.strip()

    # ---------- 内部工具 ----------

    @staticmethod
    def _explain_error(code: int, detail: str) -> str:
        """把 HTTP 错误码翻译成人话，方便排查"""
        tips = {
            401: "API Key 无效或已失效（401）。请到 platform.deepseek.com 重新生成 Key",
            402: "账户余额不足（402）。请到 DeepSeek 平台充值",
            404: "模型不存在或无权访问（404）。请检查 config.json 里的 MODEL",
            422: "请求参数错误（422）：" + detail[:200],
            429: "请求过于频繁或被限流（429）。稍后重试",
            500: "DeepSeek 服务器内部错误（500）。稍后重试",
            503: "DeepSeek 服务暂时不可用（503）。稍后重试",
        }
        return tips.get(code, f"HTTP {code} 错误：{detail[:300]}")


def parse_json_loose(text: str) -> dict:
    """
    宽松地从模型回复中提取 JSON。
    模型有时会带上 ```json ... ``` 标记，需要剥掉再解析。
    """
    text = text.strip()
    if text.startswith("```"):
        # 去掉首尾的代码围栏
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # 截取第一个 { 到最后一个 } 之间的内容
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def extract_fields_loose(text: str) -> dict:
    """
    兜底方案：当 JSON 严格解析失败时，用正则硬抽字段。
    主要应对模型把换行/引号忘了转义的情况。
    """
    def find_field(name):
        # 匹配 "name":"..." 直到下一个 ", 或 " 或 }
        m = re.search(
            rf'"{name}"\s*:\s*"((?:[^"\\]|\\.)*)(?<!\\)"',
            text,
            re.DOTALL,
        )
        return m.group(1) if m else None

    def unescape(s: str) -> str:
        # 把 JSON 字符串里常见的转义还原
        return (s.replace("\\n", "\n")
                 .replace("\\r", "\r")
                 .replace("\\t", "\t")
                 .replace('\\"', '"')
                 .replace("\\\\", "\\"))

    result = {}
    for field in ("subject", "body", "contact_name", "company", "country",
                  "email", "website", "product_query", "quantity_unit",
                  "intent", "urgency", "summary"):
        val = find_field(field)
        if val is not None:
            result[field] = unescape(val)
    if not result:
        raise ValueError(f"无法从模型回复中提取任何字段：{text[:200]}")
    return result
