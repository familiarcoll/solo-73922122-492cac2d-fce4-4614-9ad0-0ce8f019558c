"""病例交叉表安全交付工具 —— FastAPI 网页应用。

页面：
  GET  /         上传页（文件或粘贴 CSV）
  POST /analyze  解析、遮盖并在浏览器中预览交付表
  POST /download 下载与预览同内容的 CSV
  GET  /inquiry  遮盖交付表区域合计查询：上传页
  POST /inquiry  解析交付 CSV，通过相容性校验后进入区域选择页
  POST /inquiry/reselect  保留已上传文件、返回区域选择页
  POST /inquiry/range     返回所选交叉区域总人数的可达区间
"""

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import re

from .csvutil import (InputError, decode_upload, delivery_rows,
                      parse_crosstab, render_delivery_csv)
from .inquiry import (DeliveryTable, is_feasible, parse_delivery,
                      selected_region_range)
from .masking import find_hidden_cells

BASE_DIR = Path(__file__).resolve().parent

_SAFE_FILENAME_RE = re.compile(r"[^\w.\-一-鿿]+")

app = FastAPI(title="病例交叉表安全交付工具")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

NO_SOLUTION_MESSAGE = (
    "无法生成满足要求的交付表：在保留行合计、列合计与总计的前提下，"
    "即使遮盖全部正数格，仍存在必须遮盖的格子会被唯一确定，"
    "无法保证每个被遮盖格都有至少两种相容取值。"
    "建议先合并地区或年龄段以降低稀疏度，再重新上传。"
)

INFEASIBLE_MESSAGE = (
    "该交付表不存在相容填法：无法在保证每个 * 格均为正整数的前提下，"
    "同时满足全部公开格与行合计、列合计、总计。请核对文件是否被改动或截断。"
)

MIN_SELECTION = 2


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(request: Request,
                  file: UploadFile | None = None,
                  csv_text: str = Form(default="")):
    # 优先使用上传文件；未上传时使用粘贴的文本
    raw = b""
    if file is not None and file.filename:
        raw = await file.read()
    if not raw and csv_text.strip():
        raw = csv_text.encode("utf-8")
    if not raw:
        return _input_error(request, ["请上传 CSV 文件，或在文本框中粘贴 CSV 内容。"])

    try:
        text = decode_upload(raw)
        tab = parse_crosstab(text)
    except InputError as exc:
        return _input_error(request, exc.messages)

    hidden = find_hidden_cells(tab.values)
    if hidden is None:
        return templates.TemplateResponse(
            request, "result.html",
            {"ok": False, "reason": NO_SOLUTION_MESSAGE},
        )

    mandatory = {(i, j) for i in range(tab.n_rows) for j in range(tab.n_cols)
                 if tab.values[i][j] in (1, 2)}
    extra = sorted(hidden - mandatory)
    extra_labels = [f"{tab.row_labels[i]} × {tab.col_labels[j]}" for (i, j) in extra]

    csv_content = render_delivery_csv(tab, hidden)
    header, body, footer = delivery_rows(tab, hidden)
    return templates.TemplateResponse(
        request, "result.html",
        {
            "ok": True,
            "header": header,
            "body": body,
            "footer": footer,
            "n_hidden": len(hidden),
            "n_mandatory": len(mandatory),
            "n_extra": len(extra),
            "extra_labels": extra_labels,
            "csv_content": csv_content,
            "filename": "masked_crosstab.csv",
        },
    )


@app.post("/download")
async def download(csv_content: str = Form(...),
                   filename: str = Form(default="masked_crosstab.csv")):
    # 清理文件名，避免响应头注入
    filename = _SAFE_FILENAME_RE.sub("_", filename).strip("._") or "masked_crosstab"
    if not filename.endswith(".csv"):
        filename += ".csv"
    # 带 BOM 的 UTF-8，便于 Excel 直接打开；内容与网页预览完全一致
    data = csv_content.encode("utf-8-sig")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _input_error(request: Request, messages):
    return templates.TemplateResponse(
        request, "index.html",
        {"errors": messages},
        status_code=400,
    )


# ---------------------------------------------------------------------------
# 遮盖交付表区域合计查询
# ---------------------------------------------------------------------------

@app.get("/inquiry", response_class=HTMLResponse)
async def inquiry_page(request: Request):
    return templates.TemplateResponse(request, "inquiry.html")


@app.post("/inquiry", response_class=HTMLResponse)
async def inquiry_upload(request: Request,
                         file: UploadFile | None = None,
                         csv_text: str = Form(default="")):
    raw = b""
    if file is not None and file.filename:
        raw = await file.read()
    if not raw and csv_text.strip():
        raw = csv_text.encode("utf-8")
    if not raw:
        return _inquiry_error(request, ["请上传交付 CSV 文件，或在文本框中粘贴 CSV 内容。"])

    try:
        text = decode_upload(raw)
        tab = parse_delivery(text)
    except InputError as exc:
        return _inquiry_error(request, exc.messages)

    if not is_feasible(tab):
        return templates.TemplateResponse(
            request, "inquiry.html",
            {"errors": [INFEASIBLE_MESSAGE]},
            status_code=400,
        )

    # 回传时去掉 BOM，避免隐藏域与后续解析中重复出现 BOM 前缀
    return _render_selection(request, tab, text.lstrip("﻿"))


@app.post("/inquiry/reselect", response_class=HTMLResponse)
async def inquiry_reselect(request: Request, csv_content: str = Form(...)):
    try:
        tab = parse_delivery(csv_content)
    except InputError as exc:
        return _inquiry_error(request, exc.messages)
    if not is_feasible(tab):
        return _inquiry_error(request, [INFEASIBLE_MESSAGE])
    return _render_selection(request, tab, csv_content)


@app.post("/inquiry/range", response_class=HTMLResponse)
async def inquiry_range(request: Request,
                        csv_content: str = Form(...),
                        rows: list[str] = Form(default=[]),
                        cols: list[str] = Form(default=[])):
    try:
        tab = parse_delivery(csv_content)
    except InputError as exc:
        return _inquiry_error(request, exc.messages)

    if not is_feasible(tab):
        return _inquiry_error(request, [INFEASIBLE_MESSAGE])

    try:
        row_idx = sorted({int(x) for x in rows})
        col_idx = sorted({int(x) for x in cols})
    except ValueError:
        return _render_selection(
            request, tab, csv_content,
            errors=["地区与年龄段的选择参数格式不正确，请重新选择。"])
    row_idx = [i for i in row_idx if 0 <= i < tab.n_rows]
    col_idx = [j for j in col_idx if 0 <= j < tab.n_cols]

    errors = []
    if len(row_idx) < MIN_SELECTION:
        errors.append(f"请至少选择 {MIN_SELECTION} 个地区（当前选择 {len(row_idx)} 个）。")
    if len(col_idx) < MIN_SELECTION:
        errors.append(f"请至少选择 {MIN_SELECTION} 个年龄段（当前选择 {len(col_idx)} 个）。")
    if errors:
        return _render_selection(request, tab, csv_content, errors=errors,
                                 selected_rows=set(row_idx),
                                 selected_cols=set(col_idx))

    result = selected_region_range(tab, row_idx, col_idx)
    if result is None:
        # 与 is_feasible 互为兜底：正常情况下不会走到这里
        return _inquiry_error(request, [INFEASIBLE_MESSAGE])
    lo, hi = result

    return templates.TemplateResponse(
        request, "range_result.html",
        {
            "tab": tab,
            "row_labels": [tab.row_labels[i] for i in row_idx],
            "col_labels": [tab.col_labels[j] for j in col_idx],
            "lo": lo,
            "hi": hi,
            "certain": lo == hi,
            "n_masked": len(tab.masked),
            "csv_content": csv_content,
        },
    )


def _render_selection(request: Request, tab: DeliveryTable, csv_content: str,
                      errors=None, selected_rows=None, selected_cols=None):
    return templates.TemplateResponse(
        request, "inquiry_select.html",
        {
            "tab": tab,
            "csv_content": csv_content,
            "errors": errors or [],
            "selected_rows": selected_rows or set(),
            "selected_cols": selected_cols or set(),
            "min_selection": MIN_SELECTION,
        },
        status_code=400 if errors else 200,
    )


def _inquiry_error(request: Request, messages):
    return templates.TemplateResponse(
        request, "inquiry.html",
        {"errors": messages},
        status_code=400,
    )
