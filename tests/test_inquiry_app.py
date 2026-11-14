"""区域合计区间查询的 FastAPI 集成测试。"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app

client = TestClient(app)

DELIVERY = (
    "地区,0-17岁,18-64岁,65岁以上,合计\n"
    "甲区,*,*,0,6\n"
    "乙区,*,*,3,12\n"
    "丙区,4,6,8,18\n"
    "合计,11,14,11,36\n"
)


def test_inquiry_index_ok():
    resp = client.get("/inquiry")
    assert resp.status_code == 200
    assert "区域合计区间查询" in resp.text
    assert "/inquiry" in resp.text


def test_home_links_to_inquiry():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "/inquiry" in resp.text


def test_inquiry_upload_pasted_text_renders_selection():
    resp = client.post("/inquiry", data={"csv_text": DELIVERY})
    assert resp.status_code == 200
    # 选择页含行/列复选框、合计预览与遮盖标记
    assert 'name="rows"' in resp.text
    assert 'name="cols"' in resp.text
    assert "合计" in resp.text
    assert "*" in resp.text
    # 隐藏域回传 CSV 内容
    assert 'name="csv_content"' in resp.text
    # 不泄露遮盖格单格人数：页面仅展示 * 与公开数字
    assert "masked" in resp.text


def test_inquiry_upload_file():
    resp = client.post(
        "/inquiry",
        files={"file": ("delivery.csv", DELIVERY.encode("utf-8"), "text/csv")},
    )
    assert resp.status_code == 200
    assert 'name="rows"' in resp.text


def test_inquiry_empty_input():
    resp = client.post("/inquiry", data={"csv_text": ""})
    assert resp.status_code == 400
    assert "请上传" in resp.text


def test_inquiry_malformed_csv_explains_reason():
    bad = "地区,A,B,合计\n甲,*,x,5\n乙,1,2,3\n合计,2,4,8\n"
    resp = client.post("/inquiry", data={"csv_text": bad})
    assert resp.status_code == 400
    assert "非负整数或遮盖标记" in resp.text


def test_inquiry_wrong_dimensions():
    # 只有 1 个数据列
    bad = "地区,A,合计\n甲,*,5\n乙,2,7\n合计,7,12\n"
    resp = client.post("/inquiry", data={"csv_text": bad})
    assert resp.status_code == 400
    assert "2~4" in resp.text


def test_inquiry_infeasible_table_no_range():
    # 两个 * 至少各为 1，行合计却只有 1：无相容填法
    bad = "地区,A,B,合计\n甲,*,*,1\n乙,1,2,3\n合计,2,4,4\n"
    resp = client.post("/inquiry", data={"csv_text": bad})
    assert resp.status_code == 400
    assert "不存在相容填法" in resp.text
    # 不返回任何区间数字
    assert "可达区间" not in resp.text


def test_inquiry_grand_total_mismatch():
    bad = "地区,A,B,合计\n甲,*,3,8\n乙,2,*,7\n合计,6,9,99\n"
    resp = client.post("/inquiry", data={"csv_text": bad})
    assert resp.status_code == 400
    assert "不存在相容填法" in resp.text


def test_range_requires_two_rows():
    resp = client.post(
        "/inquiry/range",
        data={"csv_content": DELIVERY, "rows": ["0"], "cols": ["0", "1"]},
    )
    assert resp.status_code == 400
    assert "至少选择 2 个地区" in resp.text


def test_range_requires_two_cols():
    resp = client.post(
        "/inquiry/range",
        data={"csv_content": DELIVERY, "rows": ["0", "1"], "cols": ["1"]},
    )
    assert resp.status_code == 400
    assert "至少选择 2 个年龄段" in resp.text


def test_range_certain_sum_displayed():
    resp = client.post(
        "/inquiry/range",
        data={"csv_content": DELIVERY, "rows": ["0", "1"], "cols": ["0", "1"]},
    )
    assert resp.status_code == 200
    assert "确定的区域合计" in resp.text
    assert ">15<" in resp.text
    # 不展示遮盖格单格人数（没有逐格枚举列表）
    assert "单格" in resp.text


def test_range_interval_displayed_when_uncertain():
    # 3x3 全遮盖表，选两行两列，区间应有宽度
    csv_text = (
        "地区,A,B,C,合计\n"
        "甲,*,*,*,9\n乙,*,*,*,10\n丙,*,*,*,9\n"
        "合计,7,10,11,28\n"
    )
    resp = client.post(
        "/inquiry",
        data={"csv_text": csv_text},
    )
    assert resp.status_code == 200
    resp2 = client.post(
        "/inquiry/range",
        data={"csv_content": csv_text, "rows": ["0", "1"], "cols": ["1", "2"]},
    )
    assert resp2.status_code == 200
    assert "可达区间" in resp2.text
    assert "确定的区域合计" not in resp2.text


def test_range_full_selection_equals_grand_total():
    resp = client.post(
        "/inquiry/range",
        data={"csv_content": DELIVERY,
              "rows": ["0", "1", "2"], "cols": ["0", "1", "2"]},
    )
    assert resp.status_code == 200
    assert "确定的区域合计" in resp.text
    assert ">36<" in resp.text


def test_range_all_public_table_is_certain():
    public = "地区,A,B,合计\n甲,1,2,3\n乙,4,5,9\n合计,5,7,12\n"
    resp = client.post(
        "/inquiry/range",
        data={"csv_content": public, "rows": ["0", "1"], "cols": ["0", "1"]},
    )
    assert resp.status_code == 200
    assert ">12<" in resp.text


def test_range_invalid_index_falls_back_to_selection():
    resp = client.post(
        "/inquiry/range",
        data={"csv_content": DELIVERY, "rows": ["not-an-int"], "cols": ["0", "1"]},
    )
    assert resp.status_code == 400
    assert "参数格式不正确" in resp.text


def test_range_infeasible_content_rejected_at_range_endpoint():
    bad = "地区,A,B,合计\n甲,*,*,1\n乙,1,2,3\n合计,2,4,4\n"
    resp = client.post(
        "/inquiry/range",
        data={"csv_content": bad, "rows": ["0", "1"], "cols": ["0", "1"]},
    )
    assert resp.status_code == 400
    assert "不存在相容填法" in resp.text


def test_reselect_keeps_csv_and_renders_selection():
    resp = client.post("/inquiry/reselect", data={"csv_content": DELIVERY})
    assert resp.status_code == 200
    assert 'name="rows"' in resp.text
    assert 'name="csv_content"' in resp.text


def test_reselect_rejects_malformed():
    resp = client.post(
        "/inquiry/reselect",
        data={"csv_content": "地区,A\n甲,1\n"},
    )
    assert resp.status_code == 400


def test_original_masking_flow_still_works():
    # 原有遮盖/预览/下载链路不受影响
    good = ("地区,0-17岁,18-64岁,65岁以上\n"
            "甲区,5,1,0\n乙区,2,7,3\n丙区,4,6,8\n")
    resp = client.post("/analyze", data={"csv_text": good})
    assert resp.status_code == 200
    assert "下载同内容 CSV" in resp.text
    marker = 'name="csv_content" value="'
    start = resp.text.index(marker) + len(marker)
    end = resp.text.index('"', start)
    dl = client.post("/download",
                     data={"csv_content": resp.text[start:end],
                           "filename": "masked_crosstab.csv"})
    assert dl.status_code == 200
    assert "attachment" in dl.headers["content-disposition"]


def test_bundled_sample_files_load_and_query():
    base = Path(__file__).resolve().parent.parent / "samples"
    for name in ("example_delivery.csv", "example_delivery_range.csv"):
        text = (base / name).read_text(encoding="utf-8-sig")
        resp = client.post("/inquiry", data={"csv_text": text})
        assert resp.status_code == 200, name
        assert 'name="rows"' in resp.text


def test_masking_output_is_consumable_by_inquiry():
    # 完整闭环：/analyze 产出的交付 CSV 可直接用于区间查询
    good = ("地区,0-17岁,18-64岁,65岁以上\n"
            "甲区,5,1,0\n乙区,2,7,3\n丙区,4,6,8\n")
    analyzed = client.post("/analyze", data={"csv_text": good})
    marker = 'name="csv_content" value="'
    start = analyzed.text.index(marker) + len(marker)
    delivery = analyzed.text[start:analyzed.text.index('"', start)]

    uploaded = client.post("/inquiry", data={"csv_text": delivery})
    assert uploaded.status_code == 200
    start2 = uploaded.text.index(marker) + len(marker)
    delivery2 = uploaded.text[start2:uploaded.text.index('"', start2)]

    result = client.post(
        "/inquiry/range",
        data={"csv_content": delivery2, "rows": ["0", "1"], "cols": ["0", "1"]},
    )
    assert result.status_code == 200
    # 真实区域人数 5+1+2+7 = 15，被唯一确定
    assert "确定的区域合计：<strong>15" in result.text
    # 页面不暴露任何遮盖格的单格人数
    assert "单格" in result.text
