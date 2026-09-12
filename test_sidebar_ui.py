# -*- coding: utf-8 -*-
"""CRM V1 Sidebar UI: navigation and Recent Access, not a second queue."""
import datetime, os, shutil, sys, tempfile
from pathlib import Path
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parent; sys.path[:0]=[str(ROOT),str(ROOT/'workbench')]
import db
from workbench.queue_ui import ago, flag

def report(product, qty):
 return {'score':70,'lead':{'grade':'B','score':70},'matches':[],
 'extracted':{'company':'Northwind GmbH','country':'Germany','contact_name':'Anna','product_query':product,'quantity':qty,'source':'TEST'},
 'insight':{'product_match':{'product_match_status':'NO_MATCH'},'requirement_completeness':{'level':'HIGH'}}}

def test_sidebar_current_ui_and_recent_access():
 now=datetime.datetime.now(); assert ago((now-datetime.timedelta(minutes=12)).strftime('%Y-%m-%d %H:%M'),now=now)=='12分钟前'
 assert flag('Germany')=='🇩🇪' and flag('Atlantis')==''
 old,tmp=db.DB_PATH,tempfile.mkdtemp(); db.DB_PATH=os.path.join(tmp,'ui.db')
 try:
  db.init_db(); db.save_inquiry('A',report('Steel Bottle','5,000 pcs')); db.save_inquiry('A revision',report('Steel Bottle','3,000 pcs')); db.save_inquiry('B',report('Desk Lamp','1,000 pcs'))
  at=AppTest.from_file(str(ROOT/'workbench'/'app.py'),default_timeout=90).run(); assert not at.exception
  md='\n'.join(str(x.value) for x in at.sidebar.markdown); keys=[str(x.key) for x in at.sidebar.button]
  for text in ('工作视图','收件箱','商机','最近访问'): assert text in md
  for key in ('sv_优先处理','sv_待回复','sv_今日跟进','sv_已逾期','sv_新询盘','sv_全部商机'): assert key in keys
  assert any(str(x.label)=='筛选与视图' for x in at.sidebar.expander)
  assert '打开商机后会显示在这里' in md and 'Deal Quick Access' not in md and 'Queue Score' not in md
  assert not [k for k in keys if k.startswith('open_recent_')]
  action_keys=[str(x.key) for x in at.button if str(x.key).startswith('mq_open_')]; assert len(action_keys)>=2
  at.button(key=action_keys[0]).click().run(); recent=[str(x.key) for x in at.sidebar.button if str(x.key).startswith('open_recent_')]; assert len(recent)==1
  at.sidebar.button(key=recent[0]).click().run(); assert [str(x.key) for x in at.sidebar.button if str(x.key).startswith('open_recent_')]==recent
  at.button(key=action_keys[1]).click().run(); recent=[str(x.key) for x in at.sidebar.button if str(x.key).startswith('open_recent_')]; assert len(recent)==2
 finally:
  db.DB_PATH=old; shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':
 test_sidebar_current_ui_and_recent_access(); print('sidebar UI current architecture PASS')
