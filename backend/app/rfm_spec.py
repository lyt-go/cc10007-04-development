"""RFM分层业务规则规范口径（核验码 04d29b9b）。

联合发布准入（核验码 04d4803e）在判定用户价值分层、流失去向名单时，
统一以本模块的阈值、允许范围和上限为唯一口径；RFM-0003 为核对基准记录。
"""
from __future__ import annotations

from typing import Any

# 业务核验码：本规范服务的联合发布业务
BUSINESS_CODE = "04d4803e"
# 规范口径核验码
SPEC_CODE = "04d29b9b"
# 核对基准记录
BASELINE_RULE_CODE = "RFM-0003"

# 各指标允许范围与上限（含端点）
BOUNDS: dict[str, dict[str, Any]] = {
    "最近消费天数": {
        "field": "R",
        "label": "最近消费天界",
        "min": 0,
        "max": 365,
        "cap": 365,
        "unit": "天",
        # 评分阈值：<= 高阈值 记高分，<= 中阈值 记中分，其余低分
        "high_threshold": 30,
        "mid_threshold": 90,
    },
    "消费频次": {
        "field": "F",
        "label": "消费频次界",
        "min": 0,
        "max": 1000,
        "cap": 1000,
        "unit": "次",
        "high_threshold": 20,
        "mid_threshold": 5,
    },
    "消费金额": {
        "field": "M",
        "label": "消费金额界",
        "min": 0,
        "max": 1_000_000,
        "cap": 1_000_000,
        "unit": "元",
        "high_threshold": 5000,
        "mid_threshold": 1000,
    },
}

# 价值分层允许取值（上限 5 层）
TIER_CAP = 5
TIERS = ["重要价值客户", "重要保持客户", "一般价值客户", "一般维持客户", "低价值客户"]
LEVELS = ["高", "中", "低"]


def spec_view() -> dict[str, Any]:
    """对外暴露的规范口径视图，供前端与凭证核对。"""
    return {
        "business_code": BUSINESS_CODE,
        "spec_code": SPEC_CODE,
        "baseline_rule_code": BASELINE_RULE_CODE,
        "tier_cap": TIER_CAP,
        "tiers": list(TIERS),
        "bounds": {key: dict(value) for key, value in BOUNDS.items()},
    }


def _to_number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_metric(metric: str, value: Any) -> tuple[bool, str]:
    """按允许范围和上限校验单个指标，返回（是否合规, 说明）。"""
    bound = BOUNDS.get(metric)
    if bound is None:
        return False, f"未知RFM指标「{metric}」"
    number = _to_number(value)
    if number is None:
        return False, f"{metric}取值「{value}」不是合法数值"
    if number < bound["min"] or number > bound["max"]:
        return False, (
            f"{metric}={number}{bound['unit']} 超出允许范围 "
            f"[{bound['min']}, {bound['max']}]（上限 {bound['cap']}{bound['unit']}）"
        )
    return True, ""


def validate_record(r_value: Any, f_value: Any, m_value: Any) -> list[str]:
    """校验一条用户分层记录的 R/F/M 是否全部符合规范口径。"""
    violations: list[str] = []
    for metric, value in (
        ("最近消费天数", r_value),
        ("消费频次", f_value),
        ("消费金额", m_value),
    ):
        ok, reason = validate_metric(metric, value)
        if not ok:
            violations.append(reason)
    return violations


def _level(metric: str, value: float) -> str:
    bound = BOUNDS[metric]
    # 最近消费天数越小越优：低于高阈值记高分，低于中阈值记中分，其余低分；
    # 频次/金额越大越优：达到高阈值记高分，达到中阈值记中分，其余低分。
    if metric == "最近消费天数":
        if value <= bound["high_threshold"]:
            return "高"
        if value <= bound["mid_threshold"]:
            return "中"
        return "低"
    if value >= bound["high_threshold"]:
        return "高"
    if value >= bound["mid_threshold"]:
        return "中"
    return "低"


def score_levels(r_value: Any, f_value: Any, m_value: Any) -> dict[str, str]:
    """按规范阈值把 R/F/M 转为 高/中/低 三档。"""
    r_num = _to_number(r_value) or 0.0
    f_num = _to_number(f_value) or 0.0
    m_num = _to_number(m_value) or 0.0
    return {
        "R": _level("最近消费天数", r_num),
        "F": _level("消费频次", f_num),
        "M": _level("消费金额", m_num),
    }


def classify(r_value: Any, f_value: Any, m_value: Any) -> str:
    """按规范口径判定用户价值分层（RFM-0003 核对基准同口径）。

    以 R 高/低档与 F、M 的高/中/低三档组合映射到上限 5 层的价值分层，
    与 RFM-0003 种子分层记录逐户一致，可用于发布前复算核对。
    """
    levels = score_levels(r_value, f_value, m_value)
    r_level, f_level, m_level = levels["R"], levels["F"], levels["M"]
    f_or_m_high = f_level == "高" or m_level == "高"
    f_or_m_low = f_level == "低" and m_level == "低"

    if r_level == "高":
        if f_or_m_high:
            return "重要价值客户"
        if f_or_m_low:
            return "低价值客户"
        return "一般价值客户"

    # R 为中/低档（最近消费不活跃）
    if f_or_m_high:
        return "重要保持客户"
    if f_or_m_low:
        return "低价值客户"
    return "一般维持客户"


def is_churn_risk(r_value: Any) -> bool:
    """RFM 侧流失候选：最近消费低档（最近消费天数 > 90 天）。"""
    return score_levels(r_value, 0, 0)["R"] == "低"
