# -*- coding: utf-8 -*-
"""
离线自检脚本（不需要 API Key、不联网、不花钱）

用一个"假客户端"模拟 DeepSeek 的返回结果，验证：
  ① LLM 提取结果能否正确合并到规则结果上
  ② LLM 产品匹配能否把产品 id 还原成完整产品数据
  ③ LLM 回复生成能否正常拼装
  ④ 网络/Key 出错时，是否能自动降级为规则模式（这是最重要的保险）

运行：python test_offline.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.llm_client import DeepSeekClient, DeepSeekError
from agent.llm_extractor import LLMInquiryExtractor
from agent.llm_matcher import LLMProductMatcher
from agent.replier import ReplyGenerator
from agent.lead_score import LeadScorer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(BASE_DIR, "data", "products.json")

INQUIRY = """Hi, we are looking for 5000pcs neoprnee swim caps.
This is Michael Brown from AquaGear Trading Ltd, a distributor in the United States.
Website: www.aquagear-us.com   Email: michael@aquagear-us.com
We need custom logo printing. Urgent!"""

# 模拟 DeepSeek 的返回（故意写得"脏"一点：带代码围栏、数量写成 "5,000"）
FAKE_EXTRACT = """```json
{
  "contact_name": "Michael Brown",
  "company": "AquaGear Trading Ltd",
  "country": "United States",
  "email": "michael@aquagear-us.com",
  "website": "www.aquagear-us.com",
  "product_query": "5000pcs neoprene swim caps with custom logo",
  "quantity": "5,000",
  "quantity_unit": "pcs",
  "target_price": null,
  "target_price_currency": null,
  "intent": "OEM/贴牌定制",
  "urgency": "high",
  "summary": "美国经销商求购5000顶可定制logo的氯丁橡胶泳帽，需求紧急",
  "confidence": 0.95
}
```"""

FAKE_MATCH = '{"best_id": "P001", "alternatives": ["P002"], ' \
             '"reason": "客户描述的是 neoprene swim cap，P001 完全对应", "confidence": 0.93}'

FAKE_REPLY = '{"subject": "Re: Neoprene Swim Cap - 5,000 pcs | Quotation", ' \
             '"body": "Dear Michael,\\n\\nThanks for reaching out!\\n\\nBest regards,\\nSales Team"}'


class FakeClient(DeepSeekClient):
    """假的 DeepSeek 客户端：不联网，按关键词返回预先写好的内容"""

    def __init__(self, fail=False):
        super().__init__("fake-key")
        self.fail = fail

    def chat(self, system_prompt, user_prompt, **kwargs):
        if self.fail:
            raise DeepSeekError("API Key 无效或已失效（401）")
        if "product matching engine" in system_prompt:
            return FAKE_MATCH
        if "salesperson" in system_prompt:
            return FAKE_REPLY
        return FAKE_EXTRACT


def test_llm_happy_path():
    print("【测试 1】LLM 正常路径")
    client = FakeClient()
    info = LLMInquiryExtractor(client).extract(INQUIRY)
    assert info["contact_name"] == "Michael Brown", info["contact_name"]
    assert info["company"] == "AquaGear Trading Ltd", info["company"]
    assert info["quantity"] == 5000, info["quantity"]          # "5,000" 应被清洗成 5000
    assert info["country"] == "United States"
    assert info["urgency"] == "high"
    assert info["target_price"] is None                         # null 要变成 None 而不是字符串
    assert info["source"].startswith("LLM")
    print(f"   √ 提取成功：{info['company']} / {info['quantity']} pcs / {info['country']}")
    print(f"   √ AI 摘要：{info['summary']}")

    matches = LLMProductMatcher(client, CATALOG).match(INQUIRY)
    assert matches[0]["id"] == "P001", matches[0]["id"]
    assert matches[0]["name"] == "Neoprene Swim Cap"
    assert len(matches) == 2                                    # best + 1 个备选
    print(f"   √ 匹配成功：{matches[0]['name']} {int(matches[0]['match_score']*100)}% - {matches[0]['match_reason']}")

    lead = LeadScorer().score(info, matches)
    print(f"   √ 评分成功：{lead['score']} 分 -> {lead['grade'][:1]} 级")

    draft = ReplyGenerator().generate(info, matches[0], client)
    assert draft.startswith("Subject:")
    print(f"   √ 回复生成成功（共 {len(draft.splitlines())} 行）")


def test_llm_fallback():
    print("\n【测试 2】LLM 失败时的自动降级")
    client = FakeClient(fail=True)
    info = LLMInquiryExtractor(client).extract(INQUIRY)
    assert info["source"].lower().startswith("rule"), info["source"]
    assert info["quantity"] == 5000        # 规则提取依然要能拿到数量
    assert info["company"] is not None
    print(f"   √ 降级后仍可用规则提取：{info['company']} / {info['quantity']} pcs（{info['source']}）")

    matches = LLMProductMatcher(client, CATALOG).match(INQUIRY)
    assert matches and matches[0]["name"] == "Neoprene Swim Cap"
    print(f"   √ 降级后仍可用规则匹配：{matches[0]['name']}")

    draft = ReplyGenerator().generate(info, matches[0], client)
    assert "Dear Michael" in draft
    print("   √ 降级后仍生成了模板草稿")


def test_json_clean():
    print("\n【测试 3】脏数据清洗")
    from agent.llm_client import parse_json_loose
    from agent.llm_extractor import LLMInquiryExtractor
    ex = LLMInquiryExtractor(FakeClient())
    assert ex._to_int("5,000 pcs") == 5000
    assert ex._to_int(None) is None
    assert ex._to_float("USD 1.5") == 1.5
    assert ex._clean("unknown") is None
    assert ex._clean("N/A") is None
    d = parse_json_loose("```json\n{\"a\": 1}\n```")
    assert d["a"] == 1
    print("   √ 数量/价格/null 值/代码围栏 清洗全部通过")


if __name__ == "__main__":
    test_llm_happy_path()
    test_llm_fallback()
    test_json_clean()
    print("\n全部测试通过！接下来配好 API Key 就能真实调用了。")
