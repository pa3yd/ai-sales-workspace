# -*- coding: utf-8 -*-
"""R4 UI 冒烟测试：用 AppTest 全页执行 + 打开历史询盘触发 render_report。"""
import sys, os, io, traceback

sys.path.insert(0, os.path.abspath("workbench"))
os.chdir(os.path.abspath("workbench"))

from streamlit.testing.v1 import AppTest

# 1) 空态首页（无选中询盘）
at = AppTest.from_file("workbench/app.py", default_timeout=60)
at.run()
print("STEP1 empty page: exception =", at.exception)
if at.exception:
    for e in at.exception:
        print("--- traceback ---")
        traceback.print_exception(type(e), e, e.__traceback__)

# 2) 打开一条历史询盘（触发 render_report 全分支）
at = AppTest.from_file("workbench/app.py", default_timeout=60)
at.session_state["selected_id"] = 1
at.session_state["analyzed"] = None
at.run()
print("STEP2 history id=1: exception =", at.exception)
if at.exception:
    for e in at.exception:
        print("--- traceback ---")
        traceback.print_exception(type(e), e, e.__traceback__)

# 3) 新分析路径：会话里放 analyzed（规则引擎结果）触发 render_report
sys.path.insert(0, os.path.abspath(".."))
import main
ex, mt, cl = main.build_engine("rule")
DAVID = """Dear Supplier,

We are ABC Trading Ltd based in the United Kingdom. We would like to place an order for 3000 pcs swimming caps. Could you please advise whether you can supply 500ml and 750ml bottles? We need the goods asap, delivery within 3 weeks.

Please send us your best quotation with MOQ.

Best regards,
David Smith
Purchasing Manager
david.smith@abctrading.co.uk
"""
rep = main.analyze(DAVID, ex, mt, cl)
at = AppTest.from_file("workbench/app.py", default_timeout=60)
at.session_state["analyzed"] = rep
at.session_state["analyzed_text"] = DAVID
at.session_state["selected_id"] = None
at.run()
print("STEP3 analyzed(david): exception =", at.exception)
if at.exception:
    for e in at.exception:
        print("--- traceback ---")
        traceback.print_exception(type(e), e, e.__traceback__)

# 4) 检查关键文案是否渲染（第四阶段视觉层级后的标签）
marks = ([str(m.value) for m in at.markdown] + [str(t.value) for t in at.text]
         + [str(b.label) for b in at.button] + [str(h.value) for h in at.title])
joined = "\n".join(marks)
for kw in ["AI 外贸询盘工作台", "AI 商机判断", "待处理", "下一步",
           "回复草稿", "AI 分析结果", "客户与产品", "产品匹配",
           "客户关键信息", "报价准备度", "原始询盘", "原始数据"]:
    print(("FOUND " if kw in joined or kw in str(at.metric) else "MISS  ") + kw)

# 5) KPI 数字（metric 文本）
mtxt = "\n".join(str(m.label) + "=" + str(m.value) for m in at.metric)
print("METRICS sample:\n", mtxt[:600])
