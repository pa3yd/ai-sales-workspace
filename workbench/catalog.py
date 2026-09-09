# -*- coding: utf-8 -*-
"""
产品库读写模块（工作台"产品库"标签页用）
直接读写 ../data/products.json，与 agent 的 ProductMatcher 共用同一份数据，
所以在这里改产品，分析时立刻生效（需清空引擎缓存，见 app.py）。
"""
import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(BASE_DIR, "data", "products.json")


def load_products():
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_products(products):
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)


def next_id(products):
    """根据现有 Pxxx 生成下一个编号"""
    max_n = 0
    for p in products:
        pid = p.get("id", "")
        if pid.startswith("P") and pid[1:].isdigit():
            max_n = max(max_n, int(pid[1:]))
    return f"P{max_n + 1:03d}"


def add_product(fields: dict) -> str:
    """新增一个产品，返回新 id。fields 见 normalize_fields。"""
    products = load_products()
    pid = next_id(products)
    item = {
        "id": pid,
        "name": fields["name"].strip(),
        "name_cn": fields["name_cn"].strip(),
        "keywords": [k.strip() for k in fields["keywords"] if k.strip()],
        "moq": int(fields["moq"]),
        "price_range": [float(fields["price_min"]), float(fields["price_max"])],
        "unit": fields["unit"].strip(),
        "lead_time": fields["lead_time"].strip(),
        "hs_code": fields["hs_code"].strip(),
        "stock": int(fields.get("stock") or 0),
        "safety_stock": int(fields.get("safety_stock") or 0),
    }
    products.append(item)
    save_products(products)
    return pid


def update_product(pid, fields: dict):
    products = load_products()
    for p in products:
        if p.get("id") == pid:
            p.update({
                "name": fields["name"].strip(),
                "name_cn": fields["name_cn"].strip(),
                "keywords": [k.strip() for k in fields["keywords"] if k.strip()],
                "moq": int(fields["moq"]),
                "price_range": [float(fields["price_min"]), float(fields["price_max"])],
                "unit": fields["unit"].strip(),
                "lead_time": fields["lead_time"].strip(),
                "hs_code": fields["hs_code"].strip(),
                "stock": int(fields.get("stock") or 0),
                "safety_stock": int(fields.get("safety_stock") or 0),
            })
            break
    save_products(products)


def set_stock(pid, stock: int, safety_stock: int = None):
    """只改库存/安全库存，不动其他字段。"""
    products = load_products()
    for p in products:
        if p.get("id") == pid:
            p["stock"] = int(stock)
            if safety_stock is not None:
                p["safety_stock"] = int(safety_stock)
            break
    save_products(products)


def delete_product(pid):
    products = load_products()
    products = [p for p in products if p.get("id") != pid]
    save_products(products)
