# -*- coding: utf-8 -*-
"""ROUND 7.1.1B: homepage and Pipeline share Recent Access recording."""
from pathlib import Path

APP = (Path(__file__).resolve().parent / "workbench" / "app.py").read_text(encoding="utf-8")


def test_homepage_and_pipeline_use_the_same_recent_access_helper():
    home = APP.split("def _home_select_inquiry", 1)[1].split("def _home_execute_resolved_action", 1)[0]
    pipeline = APP.split("def _open_deal_from_pipeline", 1)[1].split("def _render_task_card", 1)[0]
    assert "_record_recent_deal_access(iid)" in home
    assert "_record_recent_deal_access(inquiry_id)" in pipeline


def test_recent_access_renders_canonical_deal_deduplication():
    section = APP.split("_recent_groups = []", 1)[1].split("tab1, tab2", 1)[0]
    assert "_recent_seen" in section
    assert "repr(_recent_group[\"key\"])" in section
    assert 'key=f"open_recent_{_open_iid}"' in section


if __name__ == "__main__":
    test_homepage_and_pipeline_use_the_same_recent_access_helper()
    test_recent_access_renders_canonical_deal_deduplication()
    print("recent access round 7.1.1B PASS")
