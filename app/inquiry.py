"""遮盖交付表的区域合计区间求解。

背景
----
分析员手里只有已发布的遮盖交叉表：数据格为非负整数（公开）或 ``*``
（遮盖），并保留行合计、列合计与总计。``*`` 格只能填入**正整数**，
所有填法都必须与公开格及行/列合计相容。

本模块回答：在所有相容填法中，指定行（地区）集合与指定列（年龄段）
集合的交叉区域总人数，真正可以达到的最小值与最大值。

建模
----
设第 i 行被遮盖格集合为 H_i，第 j 列被遮盖格集合为 H^j：

1. 每条被遮盖边先预流下界 1（正整数下界）；
2. 行节点剩余供给 a_i = 行合计 - 公开格之和 - |H_i|；
3. 列节点剩余需求 b_j = 列合计 - 公开格之和 - |H^j|。

剩余问题是一个标准的二部运输问题（无下界整数流）：

    S -> 行 i：容量 a_i；行 i -> 列 j（被遮盖边）：容量无穷；
    列 j -> T：容量 b_j。

最大流达到总和 F 当且仅当原表存在相容填法。把目标区域内被遮盖边的
单位费用置 1、其余置 0，最小/最大费用流即区域内被遮盖人数的最小/
最大增量；加上区域内公开格的固定人数，得到区域合计的可达区间。

全部计算均为整数运算，无浮点误差；网络规模不超过 4+4+2=10 个节点、
16 条边，Bellman-Ford 最短路配合瓶颈增广即可瞬时完成。
"""

from .csvutil import InputError, TOTAL_LABEL


class MinCostFlow:
    """最小费用最大流（整数边权，允许负费用）。

    连续最短路（SSP）：每轮用 Bellman-Ford 在残量网络上求最短路，
    沿该路径按瓶颈容量整批增广。初始网络无负环，残量网络中出现的
    负费用反向边与 SSP 的最优性条件相容，故每轮最短路增广后所得流
    始终是同流量下的最小费用流。网络不超过 10 个节点、16 条边，
    Bellman-Ford 的开销可以忽略，且全程整数运算、无浮点误差。
    """

    def __init__(self, n):
        self.n = n
        self.graph = [[] for _ in range(n)]

    def add_edge(self, fr, to, cap, cost):
        forward = [to, cap, cost, len(self.graph[to])]
        backward = [fr, 0, -cost, len(self.graph[fr])]
        self.graph[fr].append(forward)
        self.graph[to].append(backward)
        return forward

    def flow(self, s, t, maxf):
        """求 s->t 流量为 maxf 的最小费用流。

        返回 (实际流量, 最小费用)；无法达到 maxf 时实际流量小于 maxf。
        """
        n = self.n
        total_flow = 0
        total_cost = 0
        unreachable = 10 ** 30  # 显式哨兵：所有可行费用都远小于此值
        while total_flow < maxf:
            # Bellman-Ford 求 s 出发的最短距离与前驱边
            dist = [unreachable] * n
            dist[s] = 0
            prev_v = [-1] * n
            prev_e = [-1] * n
            for _ in range(n - 1):
                updated = False
                for v in range(n):
                    if dist[v] == unreachable:
                        continue
                    for ei, e in enumerate(self.graph[v]):
                        to, cap, cost, _ = e
                        if cap > 0 and dist[v] + cost < dist[to]:
                            dist[to] = dist[v] + cost
                            prev_v[to] = v
                            prev_e[to] = ei
                            updated = True
                if not updated:
                    break
            if dist[t] == unreachable:
                break  # 残量网络中已无增广路
            # 瓶颈容量：沿最短路整批增广
            add = maxf - total_flow
            v = t
            while v != s:
                pv, pe = prev_v[v], prev_e[v]
                add = min(add, self.graph[pv][pe][1])
                v = pv
            v = t
            while v != s:
                pv, pe = prev_v[v], prev_e[v]
                e = self.graph[pv][pe]
                e[1] -= add
                self.graph[v][e[3]][1] += add
                v = pv
            total_flow += add
            total_cost += add * dist[t]
        return total_flow, total_cost


class DeliveryTable:
    """解析后的遮盖交付表。"""

    def __init__(self, corner, col_labels, row_labels, cells,
                 row_totals, col_totals, grand_total):
        self.corner = corner
        self.col_labels = col_labels      # 不含合计列
        self.row_labels = row_labels      # 不含合计行
        self.cells = cells                # 元素为 int 或 HIDE_MARK("*")
        self.row_totals = row_totals
        self.col_totals = col_totals
        self.grand_total = grand_total

    @property
    def n_rows(self):
        return len(self.row_labels)

    @property
    def n_cols(self):
        return len(self.col_labels)

    @property
    def masked(self):
        return {(i, j) for i in range(self.n_rows)
                for j in range(self.n_cols) if self.cells[i][j] == "*"}


def parse_delivery(text):
    """解析交付 CSV（含合计行/列），仅做格式校验；相容性由本模块其余函数判断。"""
    import csv
    import io
    import re

    from .masking import HIDE_MARK

    text = text.lstrip("﻿")  # 粘贴的文本可能带有 BOM
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise InputError([f"CSV 解析失败：{exc}"])
    rows = [[cell.strip() for cell in row] for row in rows]
    rows = [row for row in rows if any(cell != "" for cell in row)]
    if not rows:
        raise InputError(["CSV 内容为空，请上传文件或粘贴文本。"])

    errors = []
    width = len(rows[0])
    n_data_cols = width - 2  # 角标 + 数据列 + 合计列
    n_data_rows = len(rows) - 2  # 表头 + 数据行 + 合计行
    if not (2 <= n_data_cols <= 4):
        errors.append(f"数据列数应为 2~4 列（当前 {max(n_data_cols, 0)} 列）。")
    if not (2 <= n_data_rows <= 4):
        errors.append(f"数据行数应为 2~4 行（当前 {max(n_data_rows, 0)} 行）。")
    for r, row in enumerate(rows[1:], start=2):
        if len(row) != width:
            errors.append(f"第 {r} 行有 {len(row)} 个单元格，与表头的 {width} 个不一致。")
    if errors:
        raise InputError(errors)

    header = rows[0]
    if header[-1] != TOTAL_LABEL:
        errors.append(f"表头最后一列应为“{TOTAL_LABEL}”（行合计列）。")
    if rows[-1][0] != TOTAL_LABEL:
        errors.append(f"最后一行的行标签应为“{TOTAL_LABEL}”（列合计行）。")

    corner = header[0]
    col_labels = header[1:-1]
    row_labels = [row[0] for row in rows[1:-1]]
    for idx, label in enumerate(col_labels):
        if label == "":
            errors.append(f"第 1 行第 {idx + 2} 列的年龄段标签为空。")
    for idx, label in enumerate(row_labels):
        if label == "":
            errors.append(f"第 {idx + 2} 行第 1 列的地区标签为空。")
    dup_cols = sorted({x for x in col_labels if col_labels.count(x) > 1})
    if dup_cols:
        errors.append("列标签（年龄段）重复："
                      + "、".join(f"“{x}”" for x in dup_cols) + "。")
    dup_rows = sorted({x for x in row_labels if row_labels.count(x) > 1})
    if dup_rows:
        errors.append("行标签（地区）重复："
                      + "、".join(f"“{x}”" for x in dup_rows) + "。")

    int_re = re.compile(r"\d+")
    cells = []
    for r, row in enumerate(rows[1:-1], start=2):
        parsed = []
        for c, token in enumerate(row[1:-1], start=2):
            if token == HIDE_MARK:
                parsed.append(HIDE_MARK)
            elif int_re.fullmatch(token):
                parsed.append(int(token))
            else:
                errors.append(
                    f"第 {r} 行第 {c} 列的“{token}”不是非负整数或遮盖标记 *。")
                parsed.append(0)
        cells.append(parsed)

    def parse_total(token, where):
        if int_re.fullmatch(token):
            return int(token)
        errors.append(f"{where}“{token}”不是非负整数。")
        return 0

    row_totals = [parse_total(rows[i][-1],
                              f"第 {i + 1} 行的行合计")
                  for i in range(1, n_data_rows + 1)]
    col_totals = [parse_total(rows[-1][j],
                              f"第 {j} 列的列合计")
                  for j in range(1, n_data_cols + 1)]
    grand_total = parse_total(rows[-1][-1], "总计")
    if errors:
        raise InputError(errors)

    return DeliveryTable(corner, col_labels, row_labels, cells,
                         row_totals, col_totals, grand_total)


def _residual_supplies(tab):
    """预流每条遮盖边下界 1 后，各行剩余供给 a_i 与各列剩余需求 b_j。

    任一中间量为负即说明公开格之和（或遮盖格数量）已经超过合计，
    不可能相容。返回 (a, b, F)，不相容时返回 None。
    """
    n, m = tab.n_rows, tab.n_cols
    a = [0] * n
    b = [0] * m
    for i in range(n):
        public_sum = sum(tab.cells[i][j] for j in range(m)
                         if tab.cells[i][j] != "*")
        n_hidden = sum(1 for j in range(m) if tab.cells[i][j] == "*")
        a[i] = tab.row_totals[i] - public_sum - n_hidden
        if a[i] < 0:
            return None
    for j in range(m):
        public_sum = sum(tab.cells[i][j] for i in range(n)
                         if tab.cells[i][j] != "*")
        n_hidden = sum(1 for i in range(n) if tab.cells[i][j] == "*")
        b[j] = tab.col_totals[j] - public_sum - n_hidden
        if b[j] < 0:
            return None
    if sum(a) != sum(b):
        return None
    return a, b, sum(a)


def is_feasible(tab):
    """公开格与合计是否存在至少一种相容填法（总计一并校验）。"""
    residual = _residual_supplies(tab)
    if residual is None:
        return False
    a, b, total = residual

    # 行合计之和与列合计之和必须都等于总计
    if sum(tab.row_totals) != tab.grand_total:
        return False
    if sum(tab.col_totals) != tab.grand_total:
        return False

    mcf, s, t = _build_network(tab, a, b, total)
    flow, _ = mcf.flow(s, t, total)
    return flow == total


def _build_network(tab, a, b, total, selected_rows=None,
                   selected_cols=None, weighted=False):
    """构建二部运输最小费用网络，返回 (mcf, 源点, 汇点)。"""
    n, m = tab.n_rows, tab.n_cols
    s = n + m
    t = n + m + 1
    mcf = MinCostFlow(n + m + 2)
    for i in range(n):
        mcf.add_edge(s, i, a[i], 0)
    for j in range(m):
        mcf.add_edge(n + j, t, b[j], 0)
    for (i, j) in tab.masked:
        if weighted and i in selected_rows and j in selected_cols:
            cost = 1
        else:
            cost = 0
        # 残余总量 total 即每条遮盖边的天然容量上界
        mcf.add_edge(i, n + j, total, cost)
    return mcf, s, t


def selected_region_range(tab, selected_rows, selected_cols):
    """求所选交叉区域总人数的可达区间。

    参数:
        tab: DeliveryTable
        selected_rows: 选中的行下标集合（至少 2 个）
        selected_cols: 选中的列下标集合（至少 2 个）

    返回:
        (最小值, 最大值)；无相容填法时返回 None。
    """
    residual = _residual_supplies(tab)
    if residual is None:
        return None
    a, b, total = residual
    if sum(tab.row_totals) != tab.grand_total:
        return None
    if sum(tab.col_totals) != tab.grand_total:
        return None

    n, m = tab.n_rows, tab.n_cols
    selected_rows = set(selected_rows)
    selected_cols = set(selected_cols)

    # 区域内公开格的人数是固定常量
    fixed_public = sum(
        tab.cells[i][j]
        for i in range(n) for j in range(m)
        if i in selected_rows and j in selected_cols
        and tab.cells[i][j] != "*"
    )
    # 区域内遮盖格预流的下界 1 也是固定的
    fixed_lower = sum(
        1 for i in range(n) for j in range(m)
        if i in selected_rows and j in selected_cols
        and tab.cells[i][j] == "*"
    )
    base = fixed_public + fixed_lower

    # 最小化区域内遮盖人数：区域内遮盖边费用 1，其余 0
    mcf_min, s, t = _build_network(tab, a, b, total,
                                   selected_rows, selected_cols, weighted=True)
    flow, min_cost = mcf_min.flow(s, t, total)
    if flow != total:
        return None
    # 最大化：对同一张费用表取负，求最小费用流
    mcf_max, _, _ = _build_network(tab, a, b, total,
                                   selected_rows, selected_cols, weighted=True)
    for v in range(mcf_max.n):
        for e in mcf_max.graph[v]:
            e[2] = -e[2]
    flow, neg_cost = mcf_max.flow(s, t, total)
    if flow != total:
        return None
    max_cost = -neg_cost
    return base + min_cost, base + max_cost
