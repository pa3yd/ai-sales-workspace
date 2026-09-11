# -*- coding: utf-8 -*-
"""FINAL PRE-TEST FIX: Deal state consistency regression.
Run directly: python test_deal_state_consistency.py
"""
import sys
sys.path.insert(0, 'workbench')
import queue_ui as ui


def _state(group, opp=None):
    return ui.deal_work_item(group, opp=opp).get('resolvedState') or {}


def test_brightpromo_no_match_keeps_customer_product():
    group = {
        'key': ('deal', ('co', 'brightpromo bv'), 'stainless steel water', 'open'),
        'lead': {
            'id': 5, 'company': 'BrightPromo BV', 'contact': 'Sophie',
            'country': 'Netherlands', 'status': '待处理', 'pri': '优先处理',
            'need': {
                'product_query': 'Stainless Steel Water Bottles',
                'intent': 'Stainless Steel Water Bottles',
                'qty': '约 10,000 pcs', 'blockers': [],
                'readiness': 'PRELIMINARY_QUOTE_READY',
            },
            'action': {'type': 'SEND_REPLY', 'label': '查看并发送回复'},
        },
        'items': [], 'count': 1,
    }
    group['items'] = [group['lead']]
    opp = {'id': 5, 'stage': 'REQUIREMENT_CONFIRMED',
           'product': 'Stainless Steel Water Bottles',
           'details': {'product_match_status': 'NO_MATCH',
                       'supplier_capability_status': 'UNKNOWN',
                       'requirement_completeness': 'HIGH'}}
    s = _state(group, opp)
    assert s['customerProductRequirement'] == 'Stainless Steel Water Bottles'
    assert s['productRequirementStatus'] == 'KNOWN'
    assert s['productMatchStatus'] == 'NO_MATCH'
    assert s['supplierCapabilityStatus'] == 'UNKNOWN'
    assert s['nextBestAction']['type'] == 'CHECK_SUPPLIER_CAPABILITY'
    assert s['primaryCta'] == '检查供应能力'


def test_nordhaus_quoted_uses_followup():
    group = {
        'key': ('deal', ('co', 'nordhaus electronics gmbh'), 'wireless anc earbuds', 'open'),
        'lead': {
            'id': 1, 'company': 'NordHaus Electronics GmbH', 'contact': 'Michael',
            'country': 'Germany', 'status': '待处理', 'pri': '优先处理',
            'fu_state': '已逾期',
            'need': {'product_query': 'Wireless ANC Earbuds', 'qty': '约 5,000 pcs',
                     'blockers': [], 'readiness': 'PRELIMINARY_QUOTE_READY'},
            'action': {'type': 'FOLLOW_UP_CUSTOMER', 'label': '立即跟进'},
        },
        'items': [], 'count': 1,
    }
    group['items'] = [group['lead']]
    opp = {'id': 1, 'stage': 'QUOTED', 'product': 'Wireless ANC Earbuds',
           'details': {'product_match_status': 'NO_MATCH',
                       'supplier_capability_status': 'UNKNOWN',
                       'requirement_completeness': 'HIGH'}}
    s = _state(group, opp)
    assert s['customerProductRequirement'] == 'Wireless ANC Earbuds'
    assert s['pipelineStage'] == 'QUOTED'
    assert s['nextBestAction']['type'] == 'FOLLOW_UP'
    assert s['primaryCta'] == '执行跟进'


if __name__ == '__main__':
    test_brightpromo_no_match_keeps_customer_product()
    test_nordhaus_quoted_uses_followup()
    print('deal state consistency tests passed')
