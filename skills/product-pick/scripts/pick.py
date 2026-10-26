#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""product-pick 计算脚本

子命令：
  roc   从排序换算 ROC 权重
  run   归一化 + 加权打分 + 敏感性 + 双前沿 + 价格分档

纯标准库，Python 3.6+ 兼容。
"""

import argparse
import json
import math
import re
import sys

EPS = 1e-12
INF = float("inf")


# ---------- 解析 ----------

def split_list(text, seps="[,，]"):
    return [x.strip() for x in re.split(seps, text) if x.strip()]


def parse_attrs(text):
    """'蓬松度:+,总重:-,压缩体积:-' -> [(name, sign)]"""
    items = []
    for chunk in split_list(text):
        c = chunk.replace("：", ":")
        if ":" not in c:
            raise ValueError("属性缺少方向标记（+ 或 -）：%r" % chunk)
        name, sign = c.rsplit(":", 1)
        name = name.strip()
        sign = sign.strip()
        if not name:
            raise ValueError("属性名为空：%r" % chunk)
        if sign not in ("+", "-", "＋", "－"):
            raise ValueError("属性 %s 的方向必须是 + 或 -，实际是 %r" % (name, sign))
        items.append((name, "+" if sign in ("+", "＋") else "-"))
    if not items:
        raise ValueError("属性列表为空")
    names = [n for n, _ in items]
    dup = set([n for n in names if names.count(n) > 1])
    if dup:
        raise ValueError("属性重名：%s" % "、".join(sorted(dup)))
    return items


def parse_floats(text, label):
    out = []
    for x in split_list(text):
        try:
            value = float(x.replace("%", ""))
        except ValueError:
            raise ValueError("%s 里出现非数字：%r" % (label, x))
        if not math.isfinite(value):
            raise ValueError("%s 里出现非有限数字：%r" % (label, x))
        out.append(value)
    return out


def parse_weights(text, n):
    raw = parse_floats(text, "权重")
    if len(raw) != n:
        raise ValueError("权重个数（%d）与属性个数（%d）不一致" % (len(raw), n))
    if any(w < 0 for w in raw):
        raise ValueError("权重不能为负")
    total = sum(raw)
    if total <= 0:
        raise ValueError("权重之和为 0")
    return [w / total for w in raw], raw


def parse_data(text, n_attrs):
    """'A:1,2,3;B:4,?,6' -> [(name, [float or None])]"""
    rows = []
    for chunk in split_list(text, "[;；]"):
        c = chunk.replace("：", ":")
        if ":" not in c:
            raise ValueError("数据行缺少冒号：%r" % chunk)
        name, rest = c.split(":", 1)
        name = name.strip()
        if not name:
            raise ValueError("产品名为空：%r" % chunk)
        vals = []
        for v in split_list(rest):
            v = v.strip()
            if v in ("?", "未知", "-", "NA", "na", "n/a", "N/A"):
                vals.append(None)
            else:
                try:
                    value = float(v.replace("%", ""))
                except ValueError:
                    raise ValueError("产品 %s 的取值不是数字：%r" % (name, v))
                if not math.isfinite(value):
                    raise ValueError("产品 %s 的取值不是有限数字：%r" % (name, v))
                vals.append(value)
        if len(vals) != n_attrs:
            raise ValueError(
                "产品 %s 有 %d 个取值，但属性有 %d 个" % (name, len(vals), n_attrs))
        rows.append((name, vals))
    if not rows:
        raise ValueError("数据为空")
    names = [n for n, _ in rows]
    dup = set([n for n in names if names.count(n) > 1])
    if dup:
        raise ValueError("产品重名：%s" % "、".join(sorted(dup)))
    return rows


def parse_price(text):
    """'A:2499,B:1899' -> {name: float}"""
    out = {}
    chunks = re.split(r"[,，;；](?=\s*[^,:;，；]+?\s*:)", text)
    for chunk in [x.strip() for x in chunks if x.strip()]:
        c = chunk.replace("：", ":")
        if ":" not in c:
            raise ValueError("价格项缺少冒号：%r" % chunk)
        name, v = c.split(":", 1)
        name = name.strip()
        if not name:
            raise ValueError("价格项产品名为空：%r" % chunk)
        if name in out:
            raise ValueError("价格重复：%s" % name)
        try:
            value = float(v.strip().replace(",", "").replace("¥", "").replace("￥", ""))
        except ValueError:
            raise ValueError("产品 %s 的价格不是数字：%r" % (name, v))
        if not math.isfinite(value) or value < 0:
            raise ValueError("产品 %s 的价格必须是非负有限数字：%r" % (name, v))
        out[name] = value
    return out


def parse_gates(text):
    """'A:fail,B:pass' -> {name: 'fail'|'pass'}"""
    states = {}
    for chunk in split_list(text, "[,;，；]"):
        c = chunk.replace("：", ":")
        if ":" not in c:
            raise ValueError("门槛项缺少冒号：%r" % chunk)
        name, state = c.split(":", 1)
        name = name.strip()
        state = state.strip().lower()
        if not name:
            raise ValueError("门槛项产品名为空：%r" % chunk)
        if name in states:
            raise ValueError("门槛重复：%s" % name)
        if state in ("fail", "f", "不合格", "x", "✗"):
            states[name] = "fail"
        elif state in ("pass", "p", "合格", "o", "✓", "ok"):
            states[name] = "pass"
        else:
            raise ValueError("门槛状态只能是 pass / fail，实际是 %r" % state)
    return states


def parse_risk(text):
    """'A:0,B:0.12' -> {name: sigma}，sigma 为相对不确定度（0.12 = 12%）"""
    out = {}
    for chunk in split_list(text):
        c = chunk.replace("：", ":")
        if ":" not in c:
            raise ValueError("不确定度项缺少冒号：%r" % chunk)
        name, v = c.split(":", 1)
        name = name.strip()
        if not name:
            raise ValueError("不确定度项产品名为空：%r" % chunk)
        if name in out:
            raise ValueError("不确定度重复：%s" % name)
        raw = v.strip().replace("%", "")
        try:
            sigma = float(raw)
        except ValueError:
            raise ValueError("产品 %s 的不确定度不是数字：%r" % (name, v))
        if not math.isfinite(sigma):
            raise ValueError("产品 %s 的不确定度不是有限数字：%r" % (name, v))
        if "%" in v:
            sigma = sigma / 100.0
        elif sigma >= 1.0:
            # 写成 12 这种整数，按百分数理解
            sigma = sigma / 100.0
        if sigma < 0 or sigma > 1:
            raise ValueError("产品 %s 的不确定度应在 0–1（或 0–100%%）之间" % name)
        out[name] = sigma
    return out


def impute_median(rows, n_attrs):
    """用每列已知值的中位数填补缺失值，返回 (rows, details)。"""
    medians = []
    for i in range(n_attrs):
        values = sorted(row[1][i] for row in rows if row[1][i] is not None)
        if not values:
            medians.append(None)
            continue
        middle = len(values) // 2
        if len(values) % 2:
            medians.append(values[middle])
        else:
            medians.append((values[middle - 1] + values[middle]) / 2.0)

    details = {}
    output = []
    for name, vals in rows:
        new_vals = list(vals)
        changed = {}
        for i, value in enumerate(new_vals):
            if value is None and medians[i] is not None:
                new_vals[i] = medians[i]
                changed[i] = medians[i]
        if changed:
            details[name] = changed
        output.append((name, new_vals))
    return output, details


# ---------- 计算 ----------

def roc_weights(n):
    """rank-order centroid: w_i = (1/n) * sum_{k=i}^{n} 1/k"""
    ws = []
    for i in range(1, n + 1):
        s = sum(1.0 / k for k in range(i, n + 1))
        ws.append(s / n)
    total = sum(ws)
    return [w / total for w in ws]


def normalize(rows, attrs):
    """rows: [(name, vals)] 全部 complete 的。返回 {name: [n]}、各属性 min/max/警告。"""
    n_attr = len(attrs)
    stats = []
    for i in range(n_attr):
        col = [r[1][i] for r in rows]
        lo, hi = min(col), max(col)
        stats.append({"min": lo, "max": hi, "span": hi - lo})

    norm = {}
    for name, vals in rows:
        ns = []
        for i in range(n_attr):
            st = stats[i]
            if st["span"] <= EPS:
                ns.append(0.5)          # 无区分度，全体记 0.5
            else:
                sign = attrs[i][1]
                if sign == "+":
                    ns.append((vals[i] - st["min"]) / st["span"])
                else:
                    ns.append((st["max"] - vals[i]) / st["span"])
        norm[name] = ns
    return norm, stats


def scores_of(norm, weights):
    return dict((name, 100.0 * sum(w * v for w, v in zip(weights, ns)))
                for name, ns in norm.items())


def flip_factor(weights, d, k):
    """把属性 k 的权重乘以 f 后，两产品得分差归零所需的 f。不可行返回 None。"""
    if abs(d[k]) < EPS or weights[k] <= EPS:
        return None
    others = sum(weights[i] * d[i] for i in range(len(d)) if i != k)
    f = -others / (weights[k] * d[k])
    if f <= 1e-9 or f > 1e6:
        return None
    return f


def adjust_for_worst_case(rows, attrs, risk):
    """按产品不确定度把每个数值往不利方向移动。"""
    adjusted_rows = []
    for name, vals in rows:
        sigma = risk.get(name, 0.0)
        adjusted = []
        for value, (_, sign) in zip(vals, attrs):
            delta = abs(value) * sigma
            adjusted.append(value - delta if sign == "+" else value + delta)
        adjusted_rows.append((name, adjusted))
    return adjusted_rows


def sens_analysis(attrs, weights, norm, ranked_names, scores):
    """返回 (逐条翻转点列表, 最脆弱的一条)"""
    if len(ranked_names) < 2:
        return [], None
    w_name = ranked_names[0]
    out = []
    best = None
    for r_name in ranked_names[1:]:
        d = [norm[w_name][i] - norm[r_name][i] for i in range(len(attrs))]
        cand = None
        for k in range(len(attrs)):
            f = flip_factor(weights, d, k)
            if f is None:
                continue
            delta = abs(f - 1.0)
            if cand is None or delta < cand["delta"]:
                cand = {"k": k, "f": f, "delta": delta}
        if cand is None:
            continue
        out.append({
            "challenger": r_name,
            "criterion": attrs[cand["k"]][0],
            "factor": round(cand["f"], 4),
            "change_pct": round((cand["f"] - 1.0) * 100.0, 1),
            "delta": cand["delta"],
            "score_gap": round(scores[w_name] - scores[r_name], 2),
        })
        if best is None or cand["delta"] < best["delta"]:
            best = out[-1]
    return out, best


def verdict_of(best):
    if best is None:
        return "none", "只有一个有效候选，无需比较。"
    d = best["delta"]
    gap = best.get("score_gap", 0.0)
    if abs(gap) < 0.5:
        return "tie", (
            "**完全平局。** 第一名与「%s」只差 %.2f 分，远小于打分的精度——"
            "这不是「很难选」，是「数学上分不出高下」。\n"
            "直接按次级标准决定：更便宜 / 可退货 / 有现货 / 售后网点近 / 交期快。"
            "继续调整权重只会得到随机的结果。"
            % (best["challenger"], abs(gap)))
    if d < 0.15:
        return "fragile", (
            "**极不稳健。**「%s」的权重只要变动 %.0f%% 赢家就换人"
            "（当前分差仅 %.2f 分），说明两者的真实差异小于打分噪音。\n"
            "正确动作不是继续调权重，而是承认这两款实质等价，"
            "用次级标准决定（更便宜 / 可退货 / 有现货 / 交期快 / 售后网点近）。"
            % (best["criterion"], abs(best["change_pct"]), abs(gap)))
    if d < 0.30:
        return "unstable", (
            "**不稳健。** 结论依赖权重设定：把「%s」的权重挪动 %.0f%%，"
            "赢家就从「%s」变成「%s」。判断一下你的真实偏好是否与刚才的排序一致。"
            % (best["criterion"], abs(best["change_pct"]),
               best.get("winner", "当前第一名"), best["challenger"]))
    return "robust", (
        "**稳健。** 最敏感的一条也要把「%s」的权重改动 %.0f%% 才会翻盘。"
        % (best["criterion"], abs(best["change_pct"])))


def dominates(a, b, axis_lower_better):
    sa, sb = a["score"], b["score"]
    aa, ab = a["axis"], b["axis"]
    if sa < sb - EPS:
        return False
    if axis_lower_better:
        if aa > ab + EPS:
            return False
    else:
        if aa < ab - EPS:
            return False
    return (sa > sb + EPS) or (abs(aa - ab) > EPS)


def pareto(points, axis_lower_better):
    """points: [{'name','score','axis'}] -> 非支配集"""
    keep = []
    for p in points:
        dominated = False
        for q in points:
            if q is p:
                continue
            if dominates(q, p, axis_lower_better):
                dominated = True
                break
        if not dominated:
            keep.append(p)
    keep.sort(key=lambda x: x["axis"], reverse=not axis_lower_better)
    return keep


def band_split(points, edges):
    ordered = sorted(edges)
    if any(not math.isfinite(edge) for edge in ordered):
        raise ValueError("价格分档边界必须是有限数字")
    if any(abs(a - b) <= EPS for a, b in zip(ordered, ordered[1:])):
        raise ValueError("价格分档边界不能重复")
    bounds = [-INF] + ordered + [INF]
    out = []
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        # Intervals are [lo, hi), except the final interval [lo, +inf).
        # A value equal to a boundary therefore belongs to exactly one band.
        if hi == INF:
            seg = [p for p in points if p["axis"] >= lo]
        else:
            seg = [p for p in points if p["axis"] >= lo and p["axis"] < hi]
        if not seg:
            out.append({"lo": lo, "hi": hi, "best": None, "count": 0})
            continue
        seg.sort(key=lambda x: x["score"], reverse=True)
        out.append({"lo": lo, "hi": hi, "best": seg[0], "count": len(seg)})
    return out


# ---------- 格式化 ----------

def num(x, nd=2):
    if x is None:
        return "—"
    if abs(x - round(x)) < 1e-9:
        return "%d" % int(round(x))
    return ("%%.%df" % nd) % x


def money(x):
    if x is None:
        return "—"
    if abs(x - round(x)) < 1e-9:
        return "¥%s" % format(int(round(x)), ",")
    return "¥%.2f" % x


# ---------- 子命令 ----------

def cmd_roc(args):
    names = split_list(args.order)
    if not names:
        raise ValueError("排序为空")
    n = len(names)
    ws = roc_weights(n)
    if args.json or getattr(args, "json_global", False):
        print(json.dumps({
            "order": names,
            "weights_pct": [round(w * 100, 1) for w in ws],
            "n": n,
        }, ensure_ascii=False, indent=2))
        return 0

    print("## ROC 权重换算（rank-order centroid）\n")
    print("| 排名 | 属性 | 权重 | 直觉参考 |")
    print("|---|---|---|---|")
    for i, (nm, w) in enumerate(zip(names, ws)):
        hint = ""
        if i == 0:
            hint = "约掌握 %d%% 的决定权" % round(w * 100)
        elif i == n - 1:
            hint = "基本不影响排名"
        print("| %d | %s | %.1f%% | %s |" % (i + 1, nm, w * 100, hint))
    print()
    print("合计 100%。排序只提供序数信息，具体百分比由 ROC 反推——")
    print("你只需要能排出「谁比谁重要」，不需要报出「重要几倍」。")
    print()
    if n >= 8:
        print("> 提醒：属性数已到 %d 个，榜首权重被摊薄到 %.0f%%。"
              "如果某个维度对你确实是决定性的，考虑砍掉最不重要的几项。" % (n, ws[0] * 100))
    return 0


def cmd_run(args):
    attrs = parse_attrs(args.attrs)
    n_attr = len(attrs)
    weights, raw_weights = parse_weights(args.weights, n_attr)

    rows = parse_data(args.data, n_attr)
    gates = parse_gates(args.gates) if args.gates else {}
    prices = parse_price(args.price) if args.price else {}

    product_names = set(name for name, _ in rows)
    unknown_prices = sorted(set(prices) - product_names)
    if unknown_prices:
        raise ValueError("价格里的产品不在数据中：%s" % "、".join(unknown_prices))
    unknown_gates = sorted(set(gates) - product_names)
    if unknown_gates:
        raise ValueError("门槛里的产品不在数据中：%s" % "、".join(unknown_gates))
    if gates and set(gates) != product_names:
        missing_gates = sorted(product_names - set(gates))
        raise ValueError(
            "启用 --gates 后必须为每个候选明确标记 pass 或 fail，缺少：%s"
            % "、".join(missing_gates))

    notes = []

    # 门槛
    gated_out = []
    kept = []
    for name, vals in rows:
        if gates.get(name) == "fail":
            gated_out.append(name)
        else:
            kept.append((name, vals))
    if gated_out:
        notes.append("硬门槛剔除：%s（未参与排名）" % "、".join(gated_out))
    if not kept:
        raise ValueError("经过门槛过滤后没有候选了")

    # 预算过滤（在归一化之前，归一化必须重算）
    if args.max_price is not None:
        if not math.isfinite(args.max_price) or args.max_price < 0:
            raise ValueError("预算上限必须是非负有限数字")
        seg = []
        dropped = []
        for name, vals in kept:
            p = prices.get(name)
            if p is None:
                raise ValueError("用了 --max-price 但产品 %s 没有价格" % name)
            if p <= args.max_price + EPS:
                seg.append((name, vals))
            else:
                dropped.append(name)
        if not seg:
            raise ValueError("预算 %.0f 以内没有候选" % args.max_price)
        notes.append("预算过滤：%.0f 以内保留 %d 个，剔除 %s"
                     % (args.max_price, len(seg), "、".join(dropped) if dropped else "无"))
        kept = seg

    # 完整性。默认严格排除；只有显式 --impute median 才会补值。
    imputed = {}
    if args.impute == "median":
        kept, imputed = impute_median(kept, n_attr)
        if imputed:
            imputation_notes = []
            for name in sorted(imputed):
                cells = ["%s=%s" % (attrs[i][0], num(value))
                         for i, value in sorted(imputed[name].items())]
                imputation_notes.append("%s（%s）" % (name, "、".join(cells)))
            notes.append("中位数插补：%s（插补值仅作参考，需在盲区中披露）"
                         % "；".join(imputation_notes))
    complete = [(n, v) for n, v in kept if all(x is not None for x in v)]
    incomplete = [n for n, v in kept if any(x is None for x in v)]
    if incomplete:
        notes.append("数据不足（有缺失值，不参与排名与前沿）：%s" % "、".join(incomplete))
    if not complete:
        raise ValueError("没有任何一个候选的数据是完整的")
    if len(complete) < 2:
        notes.append("只有 1 个有效候选，归一化后该属性必然是 0.5、得分 50 分——"
                     "这只是占位值，不代表产品质量，也没有比较意义")
    elif len(complete) == 2:
        notes.append("只有 2 个候选，每个属性归一化后必然是一个 1 一个 0，"
                     "没有中间地带，任何微小差距都会被放大成满格差异。"
                     "这种情况下得分只反映「谁在你最看重的维度上赢」，"
                     "请直接看原始值而不是分数")

    norm, stats = normalize(complete, attrs)
    scores = scores_of(norm, weights)
    ranked = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)

    # 归一化警告
    data_map = dict(complete)
    for i, st in enumerate(stats):
        col = [data_map[n][i] for n in ranked]
        # 全为不超过 100 的整数，多半是评分尺度（1–5 / 1–10 / 0–100），不做异常值警告
        score_like = all(abs(v - round(v)) < 1e-9 and abs(v) <= 100 for v in col)
        if st["span"] <= EPS:
            notes.append("属性「%s」在所有候选中同值，无区分度（不参与排名差异）"
                         % attrs[i][0])
        elif score_like:
            continue
        elif st["min"] > EPS and st["max"] / st["min"] > 3:
            notes.append("属性「%s」的极值差超过 3 倍（最大 %s / 最小 %s），"
                         "归一化会把其余候选压缩成一小段，注意区分度失真"
                         % (attrs[i][0], num(st["max"]), num(st["min"])))
        elif st["min"] > EPS and st["max"] / st["min"] < 1.2:
            notes.append("属性「%s」的候选间差异有限（最大/最小 = %.2f），"
                         "归一化会放大这点差距" % (attrs[i][0], st["max"] / st["min"]))

    # 敏感性
    sens, best = sens_analysis(attrs, weights, norm, ranked, scores)
    if best:
        best["winner"] = ranked[0]

    # 赢家是否在所有属性上都不劣于其他候选
    dominates_all = False
    if len(ranked) >= 2:
        dominates_all = all(
            all(norm[ranked[0]][i] >= norm[r][i] - EPS for i in range(n_attr))
            for r in ranked[1:])

    has_zero_weight = any(w <= EPS for w in weights)
    if len(ranked) < 2:
        verdict_key, verdict_text = "single", "只有一个有效候选，无需做敏感性分析。"
    elif has_zero_weight and not dominates_all:
        verdict_key, verdict_text = "limited", (
            "**敏感性分析不完整。** 当前有属性权重为 0，乘法式调权无法模拟"
            "从 0 提高到正数的情形；赢家也不是在所有属性上都不劣于其他候选。"
            "不要把当前排名称为绝对稳健，必要时把该属性重新纳入排序后重算。")
    elif not sens:
        verdict_key, verdict_text = "absolute", (
            "**绝对稳健。** 赢家在所有属性上都不劣于其他候选"
            "（每个维度上都不输），因此**任何单属性的权重调整都无法翻转结论**。"
            "这不是「没做敏感性分析」，而是敏感性分析的最强结果。"
            if dominates_all else
            "**绝对稳健。** 要让其他候选胜出，需要把某个属性的权重改动到"
            "不可能的倍数——在任何现实权重下结论都不会变。")
    else:
        verdict_key, verdict_text = verdict_of(best)

    # 最坏情况检验：数据不可靠的产品按不利方向偏移后重算
    worst = None
    if args.worst:
        risk = parse_risk(args.worst)
        known = [n for n, _ in complete]
        unknown = [n for n in risk if n not in known]
        if unknown:
            raise ValueError("--worst 里的产品不在有效候选中：%s" % "、".join(unknown))
        adj_rows = adjust_for_worst_case(complete, attrs, risk)
        norm_w, _ = normalize(adj_rows, attrs)
        scores_w = scores_of(norm_w, weights)
        ranked_w = sorted(scores_w.keys(), key=lambda k: scores_w[k], reverse=True)
        rank_orig = dict((n, i + 1) for i, n in enumerate(ranked))
        rank_worst = dict((n, i + 1) for i, n in enumerate(ranked_w))
        worst = {
            "winner_changed": ranked_w[0] != ranked[0],
            "worst_winner": ranked_w[0],
            "rows": [{
                "name": n,
                "sigma": risk.get(n, 0.0),
                "score": round(scores[n], 2),
                "score_worst": round(scores_w[n], 2),
                "rank": rank_orig[n],
                "rank_worst": rank_worst[n],
            } for n in ranked],
        }

    # 前沿
    price_front = None
    if prices and all(n in prices for n, _ in complete):
        pts = [{"name": n, "score": scores[n], "axis": prices[n]} for n, _ in complete]
        price_front = pareto(pts, axis_lower_better=True)
    elif prices:
        missing = [n for n, _ in complete if n not in prices]
        notes.append("部分产品缺价格，价格前沿与分档无法计算：%s" % "、".join(missing))

    axis_front = None
    axis_name = args.axis
    axis_lower = True
    if axis_name:
        matches = [a for a in attrs if a[0] == axis_name]
        if not matches:
            raise ValueError("--axis 指定的「%s」不在属性列表里" % axis_name)
        axis_lower = (matches[0][1] == "-")
        idx = [a[0] for a in attrs].index(axis_name)
        pts = [{"name": n, "score": scores[n], "axis": vals[idx]} for n, vals in complete]
        axis_front = pareto(pts, axis_lower_better=axis_lower)

    bands = None
    if args.bands:
        if not prices:
            raise ValueError("--bands 需要同时提供 --price")
        edges = parse_floats(args.bands, "分档边界")
        missing_band_prices = [n for n, _ in complete if n not in prices]
        if missing_band_prices:
            raise ValueError("使用 --bands 时这些候选缺少价格：%s"
                             % "、".join(missing_band_prices))
        pts = [{"name": n, "score": scores[n], "axis": prices[n]} for n, _ in complete
               if n in prices]
        bands = band_split(pts, edges)

    # ---- 输出 ----
    if args.json or getattr(args, "json_global", False):
        payload = {
            "attrs": [{"name": a[0], "sign": a[1]} for a in attrs],
            "weights_pct": [round(w * 100, 1) for w in weights],
            "raw_weights": raw_weights,
            "scores": [{"name": n, "score": round(scores[n], 2),
                        "price": prices.get(n)} for n in ranked],
            "normalized": dict((n, [round(x, 4) for x in v]) for n, v in norm.items()),
            "contrib": dict((n, [round(norm[n][i] * weights[i] * 100, 2)
                                 for i in range(len(attrs))]) for n in ranked),
            "gated_out": gated_out,
            "incomplete": incomplete,
            "imputed": {
                name: {attrs[index][0]: value for index, value in cells.items()}
                for name, cells in imputed.items()
            },
            "sensitivity": sens,
            "verdict": verdict_key,
            "verdict_text": verdict_text,
            "worst_case": worst,
            "price_front": price_front,
            "axis_front": axis_front,
            "bands": bands,
            "notes": notes,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("## 1. 权重\n")
    print("| 属性 | 方向 | 权重 |")
    print("|---|---|---|")
    for (nm, sign), w in zip(attrs, weights):
        print("| %s | %s | %.1f%% |" % (nm, "越大越好" if sign == "+" else "越小越好", w * 100))
    print()

    print("## 2. 归一化矩阵（0–1，本批次内相对位置）\n")
    header = "| 属性 | " + " | ".join(ranked) + " |"
    print(header)
    print("|" + "---|" * (len(ranked) + 1))
    for i, (nm, sign) in enumerate(attrs):
        cells = []
        for name in ranked:
            raw_label = num(data_map[name][i])
            if name in imputed and i in imputed[name]:
                raw_label += "，插补"
            cells.append("%.2f (%s)" % (norm[name][i], raw_label))
        print("| %s %s | %s |" % (nm, sign, " | ".join(cells)))
    print()
    print("括号内为原始值。分数是相对位置，不是绝对好坏——换个候选池就会变。\n")

    print("## 3. 综合得分（价格未计入）\n")
    print("| 名次 | 产品 | 得分 | 价格 |")
    print("|---|---|---|---|")
    for i, name in enumerate(ranked):
        print("| %d | %s | %.2f | %s |" % (i + 1, name, scores[name], money(prices.get(name))))
    print()
    if gated_out:
        print("**未进表 · 硬门槛不过**：%s\n" % "、".join(gated_out))
    if incomplete:
        print("**未进表 · 数据有缺失值**：%s\n" % "、".join(incomplete))

    print("### 3.1 得分构成（每项贡献 = 归一化值 × 权重 × 100，横向相加 = 总分）\n")
    print("| 产品 | " + " | ".join(a[0] for a in attrs) + " | **合计** |")
    print("|" + "---|" * (len(attrs) + 2))
    for name in ranked:
        cells = []
        for i, w in enumerate(weights):
            cells.append("%.2f" % (norm[name][i] * w * 100))
        print("| %s | %s | **%.2f** |" % (name, " | ".join(cells), scores[name]))
    maxc = [max(norm[n][i] for n in ranked) * weights[i] * 100
            for i in range(len(attrs))]
    print("| **该项满分** | %s | **%.2f** |"
          % (" | ".join("%.2f" % x for x in maxc), sum(maxc)))
    print()
    nodiff = [attrs[i][0] for i in range(len(attrs))
              if max(norm[n][i] for n in ranked) - min(norm[n][i] for n in ranked) < EPS]
    if nodiff:
        print("无区分度属性（进入排名的产品在该项取值相同，统一按 0.5 计入，"
              "对排名无影响）：%s\n" % "、".join(nodiff))

    print("## 4. 稳健性检验\n")
    print("### 权重敏感性（换赢家所需的最小权重变动）\n")
    if sens:
        print("| 挑战者 | 关键属性 | 该权重需变为原值 | 即 | 当前分差 |")
        print("|---|---|---|---|---|")
        for s in sens:
            print("| %s | %s | %.2f× | %+.0f%% | %.2f |"
                  % (s["challenger"], s["criterion"], s["factor"],
                     s["change_pct"], s["score_gap"]))
        print()
    print(verdict_text)
    print()

    if worst:
        print("### 数据误差（最坏情况）\n")
        print("各产品按其数据可靠性，在**不利方向**上偏移后重算"
              "（越大越好的属性打折，越小越好的属性加价）：\n")
        print("| 产品 | 不确定度 | 原得分 | 最坏得分 | 原名次 | 最坏名次 |")
        print("|---|---|---|---|---|---|")
        for r in worst["rows"]:
            move = ""
            if r["rank_worst"] > r["rank"]:
                move = " ↓%d" % (r["rank_worst"] - r["rank"])
            elif r["rank_worst"] < r["rank"]:
                move = " ↑%d" % (r["rank"] - r["rank_worst"])
            print("| %s | ±%.0f%% | %.2f | %.2f | %d | %d%s |"
                  % (r["name"], r["sigma"] * 100, r["score"], r["score_worst"],
                     r["rank"], r["rank_worst"], move))
        print()
        if worst["winner_changed"]:
            print("**结论翻转。** 在数据误差范围内，第一名从「%s」变成「%s」。"
                  "原推荐建立在某些产品的乐观数据上——"
                  "要么把这些数据再核一遍，要么就别把分差当回事。"
                  % (ranked[0], worst["worst_winner"]))
        else:
            print("**结论不变。** 即便所有产品都按最不利方向偏移，"
                  "第一名仍是「%s」。推荐在数据误差范围内是稳的。" % ranked[0])
        print()

    if price_front:
        print("## 5. 价格前沿（非支配解：这个价位没有更好的）\n")
        if worst and worst["winner_changed"]:
            print("> 注意：本节仍按原始数据计算。4.2 显示数据误差足以翻转结论，"
                  "所以这张图的排序同样不可信——先把有争议的数据核实，再回来看。\n")
        print("| 产品 | 价格 | 得分 |")
        print("|---|---|---|")
        prev = None
        for p in price_front:
            delta = ""
            if prev is not None:
                d_price = p["axis"] - prev["axis"]
                d_score = p["score"] - prev["score"]
                if d_price > EPS:
                    delta = "多花 %s 买 %.1f 分（%s/分）" % (
                        money(d_price), d_score, money(d_price / max(d_score, 0.01)))
                elif abs(d_price) <= EPS and abs(d_score) <= EPS:
                    delta = "与此前完全并列"
                elif d_score > EPS:
                    delta = "更便宜且更高分"
            print("| %s | %s | %.2f | %s |" % (p["name"], money(p["axis"]), p["score"], delta))
            prev = p
        print()
        dropped = [n for n in ranked if n not in [p["name"] for p in price_front]]
        if dropped:
            print("被支配（同价位或更低价格已有更好选择）：%s\n" % "、".join(dropped))

    if axis_front:
        print("## 6. %s 前沿\n" % axis_name)
        print("| 产品 | %s | 得分 |" % axis_name)
        print("|---|---|---|")
        prev = None
        for p in axis_front:
            delta = ""
            if prev is not None:
                d_axis = abs(p["axis"] - prev["axis"])
                d_score = p["score"] - prev["score"]
                if d_axis > EPS:
                    delta = "每改善 1 单位花 %.1f 分" % (d_axis / max(d_score, 0.01))
            print("| %s | %s | %.2f | %s |" % (p["name"], num(p["axis"]), p["score"], delta))
            prev = p
        print()

    if bands:
        print("## 7. 价格分档\n")
        print("| 价位段 | 最优 | 得分 | 段内候选数 |")
        print("|---|---|---|---|")
        for b in bands:
            lo = "—" if b["lo"] == -INF else money(b["lo"])
            hi = "—" if b["hi"] == INF else money(b["hi"])
            if b["best"] is None:
                print("| %s ~ %s | 无候选 | — | 0 |" % (lo, hi))
            else:
                print("| %s ~ %s | %s (%s) | %.2f | %d |"
                      % (lo, hi, b["best"]["name"], money(b["best"]["axis"]),
                         b["best"]["score"], b["count"]))
        print()

    if notes:
        print("## 提示\n")
        for n in notes:
            print("- " + n)
        print()

    if verdict_key in ("fragile", "tie"):
        print("> 交付要求：结论必须写成「这两款实质等价」，不许强行排名。")
    return 0


# ---------- CLI ----------

def build_parser():
    p = argparse.ArgumentParser(
        prog="pick.py",
        description="产品选型计算：ROC 权重 / 归一化打分 / 敏感性 / Pareto 前沿")
    p.add_argument("--json", dest="json_global", action="store_true", help="输出 JSON")

    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("roc", help="从排序换算 ROC 权重")
    r.add_argument("--order", required=True, help='从最重要到最不重要，逗号分隔')
    r.add_argument("--json", dest="json", action="store_true", help="输出 JSON")
    r.set_defaults(func=cmd_roc)

    x = sub.add_parser("run", help="完整计算")
    x.add_argument("--attrs", required=True,
                   help='名称:+ 或 名称:-，逗号分隔，如 "蓬松度:+,总重:-"')
    x.add_argument("--weights", required=True,
                   help="与属性同序，百分比或小数均可，内部归一化")
    x.add_argument("--data", required=True, help='产品:值,值;产品:值,值（缺值写 ?）')
    x.add_argument("--price", default=None, help='产品:价格;产品:价格（只展示，不进得分）')
    x.add_argument("--axis", default=None, help="副前沿的物理轴（必须是某属性名）")
    x.add_argument("--bands", default=None, help='价格分档边界，如 "1000,2000"；区间为 [前一边界, 当前边界)')
    x.add_argument("--gates", default=None, help='硬门槛，如 "A:fail;B:pass"')
    x.add_argument("--max-price", dest="max_price", type=float, default=None,
                   help="预算上限，过滤后重新归一化重算")
    x.add_argument("--worst", default=None,
                   help='各产品的数据不确定度，如 "A款:0,B款:0.12,C款:0.05"；'
                        "该产品在不利方向偏移这么多后重算排名")
    x.add_argument("--impute", choices=["none", "median"], default="none",
                   help="缺失值处理：默认排除；median 使用同列已知值中位数并标注插补")
    x.add_argument("--json", dest="json", action="store_true", help="输出 JSON")
    x.set_defaults(func=cmd_run)

    return p


def main():
    p = build_parser()
    args = p.parse_args()
    if not getattr(args, "cmd", None):
        p.print_help()
        return 2
    try:
        return args.func(args)
    except ValueError as e:
        print("参数错误：%s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
