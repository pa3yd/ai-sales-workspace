# -*- coding: utf-8 -*-
"""Frozen CRM V1 Sidebar: navigation, Smart Views, and Recent Access."""
import os, sys
from pathlib import Path
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parent; sys.path[:0]=[str(ROOT),str(ROOT/'workbench')]
from workbench.queue_ui import wait, customer_key, group_customers
from workbench.db import list_inquiries, STATUS_TODO

def test_sidebar_navigation_and_recent_access():
    import datetime
    now=datetime.datetime.now()
    assert wait((now-datetime.timedelta(minutes=32)).strftime('%Y-%m-%d %H:%M'),now=now)=='32分钟'
    assert customer_key(cust_id=7,company='Acme')[0]=='cid'
    assert len(group_customers([{'id':1,'company':'Acme'},{'id':2,'company':'acme'}]))==1
    rows=list_inquiries(); at=AppTest.from_file(str(ROOT/'workbench'/'app.py'),default_timeout=90).run()
    assert not at.exception
    md='\n'.join(str(x.value) for x in at.sidebar.markdown)
    for label in ('工作视图','收件箱','商机','最近访问'):
        assert label in md
    keys=[str(x.key) for x in at.sidebar.button]
    for label in ('优先处理','待回复','今日跟进','已逾期','新询盘','全部商机'):
        assert f'sv_{label}' in keys
    assert any(str(x.label)=='筛选与视图' for x in at.sidebar.expander)
    assert '打开商机后会显示在这里' in md
    assert not [k for k in keys if k.startswith('open_recent_')]
    if rows:
        at.sidebar.button(key='sv_待回复').click().run()
        todo={r[0] for r in rows if r[7]==STATUS_TODO}
        assert set(at.session_state['queue_ids']) <= todo
        action_keys=[str(x.key) for x in at.button if str(x.key).startswith('mq_open_')]
        if action_keys:
            at.button(key=action_keys[0]).click().run()
            recent=[str(x.key) for x in at.sidebar.button if str(x.key).startswith('open_recent_')]
            assert len(recent)==1
            at.sidebar.button(key=recent[0]).click().run()
            assert [str(x.key) for x in at.sidebar.button if str(x.key).startswith('open_recent_')]==recent

if __name__=='__main__':
    test_sidebar_navigation_and_recent_access(); print('sidebar R6 current architecture PASS')
