"""区间求解核心算法测试：手算用例 + 暴力枚举交叉验证。"""

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.inquiry import (DeliveryTable, MinCostFlow, is_feasible,
                         parse_delivery, selected_region_range)


# ---------------------------------------------------------------------------
# 暴力枚举：列出全部相容填法（* 格为正整数），求区域合计的真实最小/最大值。
# 只用于小规模随机表交叉验证。
# ---------------------------------------------------------------------------

class BudgetExceeded(Exception):
    pass


def brute_region_range(tab, selected_rows, selected_cols, budget=2_000_000):
    n, m = tab.n_rows, tab.n_cols
    masked = sorted(tab.masked)
    # 行/列在遮盖格上需要承担的剩余人数
    row_need = [tab.row_totals[i]
                - sum(tab.cells[i][j] for j in range(m) if tab.cells[i][j] != "*")
                for i in range(n)]
    col_need = [tab.col_totals[j]
                - sum(tab.cells[i][j] for i in range(n) if tab.cells[i][j] != "*")
                for j in range(m)]
    if any(x < 0 for x in row_need + col_need) or sum(row_need) != sum(col_need):
        return None
    if sum(tab.row_totals) != tab.grand_total or sum(tab.col_totals) != tab.grand_total:
        return None

    fixed_public = sum(
        tab.cells[i][j]
        for i in range(n) for j in range(m)
        if i in selected_rows and j in selected_cols and tab.cells[i][j] != "*")

    values = [None] * len(masked)
    results = []
    state = {"nodes": 0}

    def rec(k, row_rem, col_rem):
        state["nodes"] += 1
        if state["nodes"] > budget:
            raise BudgetExceeded
        if k == len(masked):
            if all(x == 0 for x in row_rem) and all(x == 0 for x in col_rem):
                region = fixed_public + sum(
                    values[idx] for idx, (i, j) in enumerate(masked)
                    if i in selected_rows and j in selected_cols)
                results.append(region)
            return
        i, j = masked[k]
        # 本格取值上界：不能超过所在行/列剩余需求
        hi = min(row_rem[i], col_rem[j])
        for v in range(1, hi + 1):
            values[k] = v
            row_rem[i] -= v
            col_rem[j] -= v
            rec(k + 1, row_rem, col_rem)
            row_rem[i] += v
            col_rem[j] += v

    try:
        rec(0, list(row_need), list(col_need))
    except BudgetExceeded:
        pytest.skip("暴力枚举节点数超预算")
    if not results:
        return None
    return min(results), max(results)


def make_table(cells, row_totals, col_totals, grand,
               row_labels=None, col_labels=None):
    n = len(cells)
    m = len(cells[0])
    return DeliveryTable(
        corner="地区",
        col_labels=col_labels or [f"龄{j}" for j in range(m)],
        row_labels=row_labels or [f"区{i}" for i in range(n)],
        cells=cells,
        row_totals=row_totals,
        col_totals=col_totals,
        grand_total=grand,
    )


def table_from_values(values, hide_predicate):
    """由完整真值表构造遮盖交付表（合计由真值求和得到，保证自洽）。"""
    n, m = len(values), len(values[0])
    cells = [["*" if hide_predicate(i, j) else values[i][j] for j in range(m)]
             for i in range(n)]
    row_totals = [sum(row) for row in values]
    col_totals = [sum(values[i][j] for i in range(n)) for j in range(m)]
    return make_table(cells, row_totals, col_totals, sum(row_totals))


# ---------------------------------------------------------------------------
# MinCostFlow 单元测试
# ---------------------------------------------------------------------------

def test_mincostflow_basic():
    # 节点：S=2, T=3。
    # S->0 容量 3（费用 0），S->1 容量 2（费用 0）；
    # 0->T 容量 3（费用 5），1->T 容量 2（费用 1）；
    # 1->0 容量 2（费用 1）：1 的流量可中转给 0 再到 T。
    # 目标流量 4：优先走便宜的 1->T 两单位（费用 2），
    # 再走 0->T 两单位（费用 10），最小费用 12。
    mcf = MinCostFlow(4)
    S, T = 2, 3
    mcf.add_edge(S, 0, 3, 0)
    mcf.add_edge(S, 1, 2, 0)
    mcf.add_edge(0, T, 3, 5)
    mcf.add_edge(1, T, 2, 1)
    mcf.add_edge(1, 0, 2, 1)
    flow, cost = mcf.flow(S, T, 4)
    assert flow == 4
    assert cost == 12
    # 重新建网请求 5 单位：便宜路径用尽后，最后 1 单位经 1->0->T 中转（费用 6）
    mcf2 = MinCostFlow(4)
    mcf2.add_edge(S, 0, 3, 0)
    mcf2.add_edge(S, 1, 2, 0)
    mcf2.add_edge(0, T, 3, 5)
    mcf2.add_edge(1, T, 2, 1)
    mcf2.add_edge(1, 0, 2, 1)
    flow2, cost2 = mcf2.flow(S, T, 5)
    assert flow2 == 5
    # 最优：1->T 走 1（1），1 单位经 1->0->T 中转（6），0->T 走 3（15），共 17
    assert cost2 == 17


def test_mincostflow_prefers_cheaper_path():
    # 同一流量存在两条路径时，必须选费用低的那条
    mcf = MinCostFlow(4)
    S, T = 2, 3
    mcf.add_edge(S, 0, 5, 0)
    mcf.add_edge(S, 1, 5, 0)
    mcf.add_edge(0, T, 5, 9)
    mcf.add_edge(1, T, 5, 2)
    flow, cost = mcf.flow(S, T, 3)
    assert flow == 3
    assert cost == 6  # 全部走 1->T，费用 3*2


def test_mincostflow_infeasible():
    # T 是孤立节点：没有任何通往 T 的弧，流量只能为 0
    mcf = MinCostFlow(3)
    mcf.add_edge(0, 1, 2, 0)
    flow, _ = mcf.flow(0, 2, 3)
    assert flow == 0


def test_mincostflow_negative_cost():
    # 费用为负的边必须被纳入最小费用
    mcf = MinCostFlow(3)
    mcf.add_edge(0, 1, 5, -3)
    mcf.add_edge(1, 2, 5, 10)
    flow, cost = mcf.flow(0, 2, 4)
    assert flow == 4
    assert cost == 4 * 7


# ---------------------------------------------------------------------------
# 解析测试
# ---------------------------------------------------------------------------

DELIVERY = (
    "地区,0-17岁,18-64岁,65岁以上,合计\n"
    "甲区,*,*,0,6\n"
    "乙区,*,*,3,12\n"
    "丙区,4,6,8,18\n"
    "合计,11,14,11,36\n"
)


def test_parse_delivery_ok():
    tab = parse_delivery(DELIVERY)
    assert tab.row_labels == ["甲区", "乙区", "丙区"]
    assert tab.col_labels == ["0-17岁", "18-64岁", "65岁以上"]
    assert tab.cells == [["*", "*", 0], ["*", "*", 3], [4, 6, 8]]
    assert tab.row_totals == [6, 12, 18]
    assert tab.col_totals == [11, 14, 11]
    assert tab.grand_total == 36
    assert tab.masked == {(0, 0), (0, 1), (1, 0), (1, 1)}


def test_parse_delivery_bom_and_blank_lines():
    tab = parse_delivery("﻿地区,A,B,合计\n\n甲,*,1,5\n乙,2,*,7\n合计,5,7,12\n\n")
    assert tab.cells == [["*", 1], [2, "*"]]
    assert tab.grand_total == 12


@pytest.mark.parametrize("text,fragment", [
    ("地区,A,B\n甲,*,1\n乙,2,*\n", "2~4"),                 # 缺合计行列
    ("地区,A,合计\n甲,*,5\n乙,2,7\n合计,7,12\n", "2~4"),   # 仅 1 个数据列
    ("地区,A,B,合计\n甲,*,1\n乙,2,*\n合计,5,7,12\n", "不一致"),  # 行宽度不齐
    ("地区,A,A,合计\n甲,*,1,5\n乙,2,*,7\n合计,5,7,12\n", "重复"),
    ("地区,A,B,合计\n甲,*,x,5\n乙,2,*,7\n合计,5,7,12\n", "非负整数或遮盖标记"),
    ("地区,A,B,合计\n甲,*,-1,5\n乙,2,*,7\n合计,5,7,12\n", "非负整数或遮盖标记"),
    ("地区,A,B,小计\n甲,*,1,5\n乙,2,*,7\n合计,5,7,12\n", "行合计列"),
    ("地区,A,B,合计\n甲,*,1,5\n乙,2,*,7\n总计,5,7,12\n", "列合计行"),
    ("地区,A,B,合计\n甲,*,1,x\n乙,2,*,7\n合计,5,7,12\n", "行合计"),
    ("地区,A,B,合计\n甲,*,1,5\n乙,2,*,7\n合计,x,7,12\n", "列合计"),
    ("地区,A,B,合计\n甲,*,1,5\n乙,2,*,7\n合计,5,7,x\n", "总计"),
    ("", "为空"),
])
def test_parse_delivery_invalid(text, fragment):
    from app.csvutil import InputError
    with pytest.raises(InputError) as exc:
        parse_delivery(text)
    assert any(fragment in msg for msg in exc.value.messages)


def test_parse_delivery_3col_is_valid():
    # 上面参数化中的 3 列数据用例本身合法，单独确认
    tab = parse_delivery("地区,A,B,合计\n甲,*,1,5\n乙,2,*,7\n合计,5,7,12\n")
    assert tab.n_rows == 2 and tab.n_cols == 2


# ---------------------------------------------------------------------------
# 手算区间用例
# ---------------------------------------------------------------------------

def test_certain_region_sum():
    # 文档主示例：2x2 遮盖块，区域合计被唯一确定为 15
    tab = parse_delivery(DELIVERY)
    assert is_feasible(tab)
    assert selected_region_range(tab, {0, 1}, {0, 1}) == (15, 15)


def test_full_table_range_equals_grand_total():
    tab = parse_delivery(DELIVERY)
    lo, hi = selected_region_range(tab, {0, 1, 2}, {0, 1, 2})
    assert (lo, hi) == (36, 36) == (tab.grand_total, tab.grand_total)


def test_partial_region_has_real_range():
    # 3x3：
    # 行0: *, *, 3  合计 9 -> 两 * 之和为 6
    # 行1: *, 4, *  合计 10
    # 行2: 2, *, *  合计 9
    values = [[1, 5, 3], [4, 4, 2], [2, 1, 6]]
    tab = table_from_values(values, lambda i, j: True)
    # 全表当然确定
    assert selected_region_range(tab, {0, 1, 2}, {0, 1, 2}) == (28, 28)
    # 选两行两列，与暴力结果比对
    result = selected_region_range(tab, {0, 1}, {1, 2})
    expected = brute_region_range(tab, {0, 1}, {1, 2})
    assert result == expected
    assert result[0] < result[1]  # 确实存在区间宽度


def test_all_public_table_is_certain():
    tab = parse_delivery(
        "地区,A,B,合计\n甲,1,2,3\n乙,4,5,9\n合计,5,7,12\n")
    assert tab.masked == set()
    assert is_feasible(tab)
    assert selected_region_range(tab, {0, 1}, {0, 1}) == (12, 12)
    assert selected_region_range(tab, {0}, {0, 1}) == (3, 3)


def test_infeasible_row_total_too_small():
    # 两个 * 至少各为 1，行合计却只有 1
    tab = parse_delivery(
        "地区,A,B,合计\n甲,*,*,1\n乙,1,2,3\n合计,2,4,4\n")
    assert is_feasible(tab) is False
    assert selected_region_range(tab, {0, 1}, {0, 1}) is None


def test_infeasible_column_contradiction():
    # 列 A 公开值已有 5，列合计却只有 3
    tab = parse_delivery(
        "地区,A,B,合计\n甲,5,*,8\n乙,*,2,3\n合计,3,8,11\n")
    assert is_feasible(tab) is False


def test_infeasible_grand_total_mismatch():
    tab = parse_delivery(
        "地区,A,B,合计\n甲,*,3,8\n乙,2,*,7\n合计,6,9,99\n")
    assert is_feasible(tab) is False
    assert selected_region_range(tab, {0, 1}, {0, 1}) is None


def test_infeasible_row_sum_vs_grand():
    # 行合计之和与列合计之和不一致
    tab = make_table([["*", 3], [2, "*"]], [8, 7], [6, 9], 15)
    assert is_feasible(tab) is False


def test_feasible_but_tight_lower_bounds():
    # 每个 * 都只能取 1（下界即可行解），区间退化为确定值
    tab = parse_delivery(
        "地区,A,B,合计\n甲,*,5,6\n乙,3,*,4\n合计,4,6,10\n")
    assert is_feasible(tab)
    assert selected_region_range(tab, {0, 1}, {0, 1}) == (10, 10)


def test_large_numbers_no_overflow():
    values = [[10**9, 1], [1, 10**9]]
    tab = table_from_values(values, lambda i, j: True)
    lo, hi = selected_region_range(tab, {0, 1}, {0, 1})
    assert lo == hi == 2 * 10**9 + 2


# ---------------------------------------------------------------------------
# 随机交叉验证：对随机真值表随机遮盖，区间算法 vs 暴力枚举
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(120))
def test_range_matches_brute_force(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 3)
    m = rng.randint(2, 3)
    values = [[rng.randint(0, 4) for _ in range(m)] for _ in range(n)]
    # 随机挑选遮盖格：保证至少遮盖一个正数格，零格不遮盖
    positive = [(i, j) for i in range(n) for j in range(m) if values[i][j] >= 1]
    if not positive:
        pytest.skip("全零表无遮盖格")
    hidden = {c for c in positive if rng.random() < 0.6}
    if not hidden:
        hidden = {rng.choice(positive)}
    tab = table_from_values(values, lambda i, j: (i, j) in hidden)
    assert is_feasible(tab)  # 由真值构造，必然可行

    # 随机选择至少 2 行 2 列
    rows = sorted(rng.sample(range(n), rng.randint(2, n)))
    cols = sorted(rng.sample(range(m), rng.randint(2, m)))
    got = selected_region_range(tab, set(rows), set(cols))
    expected = brute_region_range(tab, set(rows), set(cols))
    assert got == expected
    lo, hi = got
    assert lo <= hi
    # 可达值不会超过总计
    assert hi <= tab.grand_total


@pytest.mark.parametrize("seed", range(40))
def test_infeasible_tables_detected(seed):
    rng = random.Random(5000 + seed)
    n = rng.randint(2, 3)
    m = rng.randint(2, 3)
    values = [[rng.randint(0, 5) for _ in range(m)] for _ in range(n)]
    positive = [(i, j) for i in range(n) for j in range(m) if values[i][j] >= 1]
    if not positive:
        pytest.skip("全零表")
    hidden = {c for c in positive if rng.random() < 0.5} or {rng.choice(positive)}
    tab = table_from_values(values, lambda i, j: (i, j) in hidden)
    # 随机篡改一个合计，制造不相容；暴力枚举应与算法判断一致
    tamper = rng.choice(["row", "col", "grand"])
    if tamper == "row":
        i = rng.randrange(n)
        tab.row_totals[i] += rng.choice([-1, 1]) * rng.randint(1, 3)
    elif tamper == "col":
        j = rng.randrange(m)
        tab.col_totals[j] += rng.choice([-1, 1]) * rng.randint(1, 3)
    else:
        tab.grand_total += rng.choice([-1, 1])
    # 篡改后可能恰好仍自洽（例如改了行合计但总计同步……这里不同步，通常矛盾）
    got_feasible = is_feasible(tab)
    # 用暴力枚举独立判断
    brute = brute_region_range(tab, set(range(n)), set(range(m)))
    assert got_feasible is (brute is not None)


def test_4x4_table_performance_and_correctness():
    # 4x4 全遮盖小数值：与暴力枚举交叉验证，且应瞬时完成
    rng = random.Random(42)
    values = [[rng.randint(1, 4) for _ in range(4)] for _ in range(4)]
    tab = table_from_values(values, lambda i, j: True)
    import time
    start = time.monotonic()
    lo, hi = selected_region_range(tab, {0, 1}, {0, 1})
    elapsed = time.monotonic() - start
    assert elapsed < 5
    expected = brute_region_range(tab, {0, 1}, {0, 1})
    assert (lo, hi) == expected


def test_4x4_large_values_performance():
    # 大数值 4x4 全遮盖：不能依赖枚举，验证性能与基本恒等式
    rng = random.Random(7)
    values = [[rng.randint(1, 200) for _ in range(4)] for _ in range(4)]
    tab = table_from_values(values, lambda i, j: True)
    import time
    start = time.monotonic()
    lo, hi = selected_region_range(tab, {0, 1}, {0, 1})
    assert time.monotonic() - start < 5
    # 每个遮盖格至少为 1，故区域下界 >= 4；上界不超过总计
    assert lo >= 4
    assert hi <= tab.grand_total
    # 全表选择必然退化为总计
    assert selected_region_range(tab, set(range(4)), set(range(4))) == (
        tab.grand_total, tab.grand_total)
