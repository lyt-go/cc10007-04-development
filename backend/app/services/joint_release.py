"""RFM分层 × 流失预警 联合发布准入服务（业务核验码 04d4803e）。

核心约束：
1. 规则启用状态与模型部署状态必须共同满足，缺一不可新增正式名单；
2. 规则启用回执与模型部署回执归入同一发布批次，两回执齐备才允许成榜；
3. 同批次/同规则×模型只允许产生一份「流失去向名单明细数据集」，
   并发（竞态）下通过进程锁 + 已发布幂等保证只留存一份；
4. 名单写入为原子动作：任何一步失败都不写入单侧状态，已落库内容整体回滚；
5. 名单投递到流失预警待处理明细，每个用户仅保留一条当前名单记录，
   并携带分层规则版本与模型版本；
6. 全链路可按业务凭证从上游记录逐跳追溯到同一份名单。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app import rfm_spec
from app.store import store

BUSINESS_CODE = rfm_spec.BUSINESS_CODE
SPEC_CODE = rfm_spec.SPEC_CODE

RFM_ENABLED_STATUS = "已启用"
CHURN_DEPLOYED_STATUS = "已部署"

# 内部数据表（统一注册到内存仓库）
T_RECEIPT = "joint_receipt"          # 规则启用 / 模型部署回执
T_BATCH = "joint_batch"              # 发布批次
T_DATASET = "churn_target_dataset"   # 流失去向名单明细数据集（成榜结果）
T_PENDING = "churn_pending_list"     # 流失预警待处理明细（投递目标）
T_VOUCHER = "joint_voucher"          # 业务凭证（链路核对）

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(table: str) -> int:
    rows = store.rows(table)
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _find_by_field(table: str, field: str, value: Any) -> dict[str, Any] | None:
    for row in store.rows(table):
        if row.get(field) == value:
            return row
    return None


def _rule(rule_id: int) -> dict[str, Any] | None:
    return store.find("rfm", rule_id)


def _model(model_id: int) -> dict[str, Any] | None:
    return store.find("churn", model_id)


def _rule_receipt(rule_id: int) -> dict[str, Any] | None:
    return _find_by_field(T_RECEIPT, "rule_id", rule_id)


def _model_receipt(model_id: int) -> dict[str, Any] | None:
    return _find_by_field(T_RECEIPT, "model_id", model_id)


def _find_batch(rule_id: int, model_id: int) -> dict[str, Any] | None:
    for row in store.rows(T_BATCH):
        if (
            row.get("rule_id") is not None
            and row.get("model_id") is not None
            and int(row.get("rule_id")) == rule_id
            and int(row.get("model_id")) == model_id
        ):
            return row
    return None


def _published_dataset(rule_id: int, model_id: int) -> dict[str, Any] | None:
    """同一规则×模型最多一份已发布数据集。"""
    for row in store.rows(T_DATASET):
        if (
            row.get("rule_id") is not None
            and row.get("model_id") is not None
            and int(row.get("rule_id")) == rule_id
            and int(row.get("model_id")) == model_id
            and row.get("状态") == "已发布"
        ):
            return row
    return None


class JointReleaseService:
    """联合发布准入的全部状态流转都收在本类，路由层不做业务判断。"""

    # ---------- 回执登记：规则启用 / 模型部署 ----------
    def register_rule_receipt(self, rule_id: int, batch_no: str | None = None) -> tuple[dict[str, Any] | None, str]:
        with _LOCK:
            rule = _rule(rule_id)
            if rule is None:
                return None, f"分层规则 {rule_id} 不存在或已归档"
            if rule.get("status") != RFM_ENABLED_STATUS:
                return None, f"规则 {rule.get('规则编号')} 未启用（当前状态：{rule.get('status')}），不能登记启用回执"
            existing = _rule_receipt(rule_id)
            if existing is not None:
                # 显式指定发布批次时允许把既有回执归入同批次（两回执同批场景）
                if batch_no and existing.get("发布批次") != batch_no:
                    batch = self._ensure_batch(rule_id, None, batch_no)
                    existing["batch_id"] = batch["id"]
                    existing["发布批次"] = batch["发布批次"]
                    self._refresh_batch_gate(batch)
                return existing, "规则启用回执已存在，重复登记被幂等忽略"
            batch = self._ensure_batch(rule_id, None, batch_no)
            receipt = {
                "id": _next_id(T_RECEIPT),
                "回执编号": f"RCV-R-{rule_id:04d}-{_next_id(T_RECEIPT):04d}",
                "回执类型": "规则启用回执",
                "batch_id": batch["id"],
                "发布批次": batch["发布批次"],
                "rule_id": rule_id,
                "model_id": None,
                "版本": rule.get("规则版本") or rule.get("规则编号"),
                "登记时间": _now(),
            }
            store.rows(T_RECEIPT).append(receipt)
            self._refresh_batch_gate(batch)
            return receipt, "规则启用回执已登记"

    def register_model_receipt(self, model_id: int, batch_no: str | None = None) -> tuple[dict[str, Any] | None, str]:
        with _LOCK:
            model = _model(model_id)
            if model is None:
                return None, f"预警模型 {model_id} 不存在或已归档"
            if model.get("status") != CHURN_DEPLOYED_STATUS:
                return None, f"模型 {model.get('模型编号')} 未部署（当前状态：{model.get('status')}），不能登记部署回执"
            existing = _model_receipt(model_id)
            if existing is not None:
                # 显式指定发布批次时允许把既有回执归入同批次（两回执同批场景）
                if batch_no and existing.get("发布批次") != batch_no:
                    batch = self._ensure_batch(None, model_id, batch_no)
                    existing["batch_id"] = batch["id"]
                    existing["发布批次"] = batch["发布批次"]
                    self._refresh_batch_gate(batch)
                return existing, "模型部署回执已存在，重复登记被幂等忽略"
            batch = self._ensure_batch(None, model_id, batch_no)
            receipt = {
                "id": _next_id(T_RECEIPT),
                "回执编号": f"RCV-C-{model_id:04d}-{_next_id(T_RECEIPT):04d}",
                "回执类型": "模型部署回执",
                "batch_id": batch["id"],
                "发布批次": batch["发布批次"],
                "rule_id": None,
                "model_id": model_id,
                "版本": model.get("模型版本") or model.get("模型编号"),
                "登记时间": _now(),
            }
            store.rows(T_RECEIPT).append(receipt)
            self._refresh_batch_gate(batch)
            return receipt, "模型部署回执已登记"

    def _ensure_batch(
        self,
        rule_id: int | None,
        model_id: int | None,
        batch_no: str | None,
    ) -> dict[str, Any]:
        """取或建发布批次；同批次号会把规则与模型归到一起（两回执同批场景）。"""
        # 1) 显式批次号优先：命中则复用，未命中则按号新建；当前侧挂上并从其它批次摘除
        if batch_no:
            batch = _find_by_field(T_BATCH, "发布批次", batch_no)
            if batch is None:
                batch = {
                    "id": _next_id(T_BATCH),
                    "发布批次": batch_no,
                    "rule_id": rule_id,
                    "model_id": model_id,
                    "规则回执齐备": False,
                    "模型回执齐备": False,
                    "准入状态": "等待回执",
                    "创建时间": _now(),
                    "更新时间": _now(),
                }
                store.rows(T_BATCH).append(batch)
            else:
                if rule_id is not None:
                    batch["rule_id"] = rule_id
                if model_id is not None:
                    batch["model_id"] = model_id
            self._detach_from_other_batches(batch, rule_id, model_id)
            return batch
        # 2) 无显式号时，复用当前侧已经归属的批次
        if rule_id is not None:
            for row in store.rows(T_BATCH):
                if row.get("rule_id") is not None and int(row.get("rule_id")) == rule_id:
                    if model_id is not None:
                        row["model_id"] = model_id
                    return row
        if model_id is not None:
            for row in store.rows(T_BATCH):
                if row.get("model_id") is not None and int(row.get("model_id")) == model_id:
                    if rule_id is not None:
                        row["rule_id"] = rule_id
                    return row
        # 3) 都没有则新建
        seq = _next_id(T_BATCH)
        batch = {
            "id": seq,
            "发布批次": batch_no or f"PUB-{datetime.now().strftime('%Y%m%d')}-{seq:03d}",
            "rule_id": rule_id,
            "model_id": model_id,
            "规则回执齐备": False,
            "模型回执齐备": False,
            "准入状态": "等待回执",
            "创建时间": _now(),
            "更新时间": _now(),
        }
        store.rows(T_BATCH).append(batch)
        return batch

    def _detach_from_other_batches(self, target: dict[str, Any], rule_id: int | None, model_id: int | None) -> None:
        """把指定规则/模型从其它批次摘除，保证一条回执只归属一个发布批次。"""
        for other in store.rows(T_BATCH):
            if other is target:
                continue
            touched = False
            if rule_id is not None and other.get("rule_id") is not None and int(other.get("rule_id")) == rule_id:
                other["rule_id"] = None
                touched = True
            if model_id is not None and other.get("model_id") is not None and int(other.get("model_id")) == model_id:
                other["model_id"] = None
                touched = True
            if touched:
                other["准入状态"] = "已归并"
                other["更新时间"] = _now()

    def _coalesce_batch(self, rule_id: int, model_id: int) -> dict[str, Any] | None:
        """把规则回执与模型回执归入同一发布批次。

        两回执独立登记时可能落在两个批次；发布前以规则回执所在批次为准，
        把模型并进去并清空其原批次，保证同一发布批次只对应一份联合名单。
        """
        rule_batch = next(
            (row for row in store.rows(T_BATCH)
             if row.get("rule_id") is not None and int(row.get("rule_id")) == rule_id),
            None,
        )
        model_batch = next(
            (row for row in store.rows(T_BATCH)
             if row.get("model_id") is not None and int(row.get("model_id")) == model_id),
            None,
        )
        if rule_batch is None and model_batch is None:
            return None
        target = rule_batch or model_batch
        target["rule_id"] = rule_id
        target["model_id"] = model_id
        for other in store.rows(T_BATCH):
            if other is not target and (
                (other.get("rule_id") is not None and int(other.get("rule_id")) == rule_id)
                or (other.get("model_id") is not None and int(other.get("model_id")) == model_id)
            ):
                other["rule_id"] = None
                other["model_id"] = None
                other["准入状态"] = "已归并"
                other["更新时间"] = _now()
        # 回执与批次号对齐
        for receipt in store.rows(T_RECEIPT):
            if receipt.get("rule_id") == rule_id or receipt.get("model_id") == model_id:
                receipt["batch_id"] = target["id"]
                receipt["发布批次"] = target["发布批次"]
        self._refresh_batch_gate(target)
        return target

    def _refresh_batch_gate(self, batch: dict[str, Any]) -> None:
        rule_id = batch.get("rule_id")
        model_id = batch.get("model_id")
        rule_ok = bool(rule_id is not None and _rule_receipt(int(rule_id)) is not None)
        model_ok = bool(model_id is not None and _model_receipt(int(model_id)) is not None)
        batch["规则回执齐备"] = rule_ok
        batch["模型回执齐备"] = model_ok
        if rule_ok and model_ok:
            batch["准入状态"] = "可发布"
        else:
            batch["准入状态"] = "等待回执"
        batch["更新时间"] = _now()

    # ---------- 准入核对（不产生写入） ----------
    def gate_status(self, rule_id: int, model_id: int) -> dict[str, Any]:
        with _LOCK:
            rule = _rule(rule_id)
            model = _model(model_id)
            rule_enabled = bool(rule is not None and rule.get("status") == RFM_ENABLED_STATUS)
            model_deployed = bool(model is not None and model.get("status") == CHURN_DEPLOYED_STATUS)
            rule_receipt = _rule_receipt(rule_id)
            model_receipt = _model_receipt(model_id)
            batch = _find_batch(rule_id, model_id)
            if batch is None:
                # 两回执可能仍在各自批次，发布时会归并；这里回退展示任一关联批次
                batch = next(
                    (row for row in store.rows(T_BATCH)
                     if (row.get("rule_id") is not None and int(row.get("rule_id")) == rule_id)
                     or (row.get("model_id") is not None and int(row.get("model_id")) == model_id)),
                    None,
                )
            publishable = rule_enabled and model_deployed and rule_receipt is not None and model_receipt is not None
            reasons: list[str] = []
            if not rule_enabled:
                reasons.append("规则未启用（未部署模型或未启用规则时不得新增正式名单）")
            if not model_deployed:
                reasons.append("模型未部署（未部署模型或未启用规则时不得新增正式名单）")
            if rule_enabled and rule_receipt is None:
                reasons.append("缺少规则启用回执")
            if model_deployed and model_receipt is None:
                reasons.append("缺少模型部署回执")
            return {
                "业务核验码": BUSINESS_CODE,
                "规范口径核验码": SPEC_CODE,
                "基准规则": rfm_spec.BASELINE_RULE_CODE,
                "rule_id": rule_id,
                "model_id": model_id,
                "规则启用": rule_enabled,
                "模型部署": model_deployed,
                "规则启用回执": rule_receipt is not None,
                "模型部署回执": model_receipt is not None,
                "发布批次": batch["发布批次"] if batch else None,
                "准入状态": "可发布" if publishable else "未满足",
                "可发布": publishable,
                "阻断原因": reasons,
            }

    # ---------- 联合发布：生成唯一一份名单并原子投递 ----------
    def publish(self, rule_id: int, model_id: int, operator: str | None = None) -> tuple[dict[str, Any] | None, str]:
        with _LOCK:
            gate = self.gate_status(rule_id, model_id)
            if not gate["可发布"]:
                return None, "联合发布准入未满足：" + "；".join(gate["阻断原因"])

            # 竞态/重复成榜：同一规则×模型只留存一份已发布数据集
            existing = _published_dataset(rule_id, model_id)
            if existing is not None:
                return existing, "同一发布批次已存在流失去向名单明细数据集，重复成榜被幂等拦截，仅留存一份"

            rule = _rule(rule_id)
            model = _model(model_id)
            rule_version = rule.get("规则版本") or rule.get("规则编号")
            model_version = model.get("模型版本") or model.get("模型编号")
            batch = self._coalesce_batch(rule_id, model_id)
            assert batch is not None  # 两回执齐备时批次必然存在

            # 上游关联：按用户编号把分层记录与预警模型数据集 join
            segments = {
                row["用户编号"]: row
                for row in store.rows("user_segment")
                if row.get("规则编号") == rule.get("规则编号")
            }
            model_rows = [
                row
                for row in store.rows("churn_dataset")
                if row.get("模型编号") == model.get("模型编号")
            ]

            members: list[dict[str, Any]] = []
            violations: list[str] = []
            seen_users: set[str] = set()
            for model_row in model_rows:
                user_id = model_row["用户编号"]
                if user_id in seen_users:  # 每个用户只保留一条
                    continue
                segment = segments.get(user_id)
                if segment is None:
                    violations.append(f"用户 {user_id} 缺少RFM分层记录，无法关联")
                    continue
                record_violations = rfm_spec.validate_record(
                    segment.get("最近消费天数"),
                    segment.get("消费频次"),
                    segment.get("消费金额"),
                )
                if record_violations:
                    violations.extend(f"用户 {user_id}：{text}" for text in record_violations)
                    continue
                expected_tier = rfm_spec.classify(
                    segment.get("最近消费天数"),
                    segment.get("消费频次"),
                    segment.get("消费金额"),
                )
                # 流失去向：RFM 结果驱动（R 低档）且模型判定为流失，二者交集才是完整联合名单
                rfm_risk = rfm_spec.is_churn_risk(segment.get("最近消费天数"))
                model_risk = int(model_row.get("模型判定", 0)) == 1 or float(model_row.get("流失概率", 0.0)) >= float(
                    model.get("预测阈值", 0.75)
                )
                if not (rfm_risk and model_risk):
                    continue
                seen_users.add(user_id)
                members.append({
                    "用户编号": user_id,
                    "价值分层": segment.get("价值分层") or expected_tier,
                    "规范复算分层": expected_tier,
                    "分层一致": (segment.get("价值分层") or expected_tier) == expected_tier,
                    "最近消费天数": segment.get("最近消费天数"),
                    "消费频次": segment.get("消费频次"),
                    "消费金额": segment.get("消费金额"),
                    "流失概率": model_row.get("流失概率"),
                    "模型判定": model_row.get("模型判定"),
                    "规则编号": rule.get("规则编号"),
                    "规则版本": rule_version,
                    "模型编号": model.get("模型编号"),
                    "模型版本": model_version,
                    "分层记录版本": segment.get("记录版本"),
                    "模型数据版本": model_row.get("数据版本"),
                    "打分时间": model_row.get("打分时间"),
                })

            if violations:
                return None, "上游数据未通过规范口径校验，已中止成榜且未写入任何单侧状态：" + "；".join(violations)

            members.sort(key=lambda item: (-float(item["流失概率"]), item["用户编号"]))

            # ---- 原子写入：先在暂存区构建全部内容，再一次性落库；任何异常整体回滚 ----
            snapshot = self._snapshot_tables()
            try:
                dataset = self._write_dataset(batch, rule, model, members, operator)
                self._deliver_pending(dataset, members)
                voucher = self._write_voucher(batch, rule, model, dataset, members)
                batch["准入状态"] = "已发布"
                batch["更新时间"] = _now()
            except Exception as exc:  # 失败回滚，绝不留下单侧状态
                self._restore_tables(snapshot)
                return None, f"名单写入失败，已整体回滚（未留下单侧状态）：{exc}"

            return {
                "dataset": dataset,
                "voucher": voucher,
                "成员数": len(members),
                "发布批次": batch["发布批次"],
            }, "联合名单已发布并投递至流失预警待处理明细"

    def _snapshot_tables(self) -> dict[str, list[dict[str, Any]]]:
        return {
            name: [dict(row) for row in store.rows(name)]
            for name in (T_DATASET, T_PENDING, T_VOUCHER, T_BATCH)
        }

    def _restore_tables(self, snapshot: dict[str, list[dict[str, Any]]]) -> None:
        for name, rows in snapshot.items():
            store.rows(name).clear()
            store.rows(name).extend(dict(row) for row in rows)

    def _write_dataset(
        self,
        batch: dict[str, Any],
        rule: dict[str, Any],
        model: dict[str, Any],
        members: list[dict[str, Any]],
        operator: str | None,
    ) -> dict[str, Any]:
        seq = _next_id(T_DATASET)
        dataset_no = f"CHURN-LIST-{datetime.now().strftime('%Y%m%d')}-{seq:03d}"
        dataset = {
            "id": seq,
            "名单编号": dataset_no,
            "发布批次": batch["发布批次"],
            "batch_id": batch["id"],
            "rule_id": rule.get("id"),
            "model_id": model.get("id"),
            "规则编号": rule.get("规则编号"),
            "规则版本": rule.get("规则版本") or rule.get("规则编号"),
            "模型编号": model.get("模型编号"),
            "模型版本": model.get("模型版本") or model.get("模型编号"),
            "模型准确率": model.get("模型准确率"),
            "预警推送频率": model.get("预警推送频率"),
            "覆盖人数": len(members),
            "状态": "已发布",
            "业务核验码": BUSINESS_CODE,
            "规范口径核验码": SPEC_CODE,
            "生成时间": _now(),
            "操作人": operator or "值班管理员",
            "成员": [dict(member) for member in members],
        }
        store.rows(T_DATASET).append(dataset)
        return dataset

    def _deliver_pending(self, dataset: dict[str, Any], members: list[dict[str, Any]]) -> None:
        """投递到流失预警待处理明细：同一用户只保留一条当前名单记录。"""
        pending_rows = store.rows(T_PENDING)
        for member in members:
            user_id = member["用户编号"]
            for row in pending_rows:
                if row.get("用户编号") == user_id and row.get("是否当前"):
                    row["是否当前"] = False
                    row["状态"] = "已被新名单替换"
                    row["失效时间"] = _now()
            pending_rows.append({
                "id": _next_id(T_PENDING),
                "用户编号": user_id,
                "名单编号": dataset["名单编号"],
                "发布批次": dataset["发布批次"],
                "规则编号": member["规则编号"],
                "规则版本": member["规则版本"],
                "模型编号": member["模型编号"],
                "模型版本": member["模型版本"],
                "价值分层": member["价值分层"],
                "流失概率": member["流失概率"],
                "预警推送频率": dataset["预警推送频率"],
                "状态": "待处理",
                "是否当前": True,
                "投递时间": _now(),
            })

    def _write_voucher(
        self,
        batch: dict[str, Any],
        rule: dict[str, Any],
        model: dict[str, Any],
        dataset: dict[str, Any],
        members: list[dict[str, Any]],
    ) -> dict[str, Any]:
        voucher_no = f"VCH-{datetime.now().strftime('%Y%m%d')}-{dataset['id']:04d}"
        voucher = {
            "id": _next_id(T_VOUCHER),
            "凭证编号": voucher_no,
            "业务核验码": BUSINESS_CODE,
            "规范口径核验码": SPEC_CODE,
            "发布批次": batch["发布批次"],
            "名单编号": dataset["名单编号"],
            "规则编号": rule.get("规则编号"),
            "规则版本": rule.get("规则版本") or rule.get("规则编号"),
            "模型编号": model.get("模型编号"),
            "模型版本": model.get("模型版本") or model.get("模型编号"),
            "模型准确率": model.get("模型准确率"),
            "预警推送频率": model.get("预警推送频率"),
            "覆盖人数": len(members),
            "上游分层表": "user_segment",
            "上游模型数据集": "churn_dataset",
            "成员用户": [member["用户编号"] for member in members],
            "开立时间": _now(),
        }
        store.rows(T_VOUCHER).append(voucher)
        return voucher

    # ---------- 查询：名单、待处理明细、批次、凭证 ----------
    def list_datasets(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(row) for row in store.rows(T_DATASET)]

    def list_pending(self, *, current_only: bool = False) -> list[dict[str, Any]]:
        with _LOCK:
            rows = store.rows(T_PENDING)
            if current_only:
                rows = [row for row in rows if row.get("是否当前")]
            return [dict(row) for row in rows]

    def list_batches(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(row) for row in store.rows(T_BATCH)]

    def list_receipts(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(row) for row in store.rows(T_RECEIPT)]

    def lineage(self, voucher_no: str) -> dict[str, Any] | None:
        """从业务凭证按记录追到同一份名单：凭证 → 名单 → 上游分层/模型记录。"""
        with _LOCK:
            voucher = _find_by_field(T_VOUCHER, "凭证编号", voucher_no)
            if voucher is None:
                return None
            dataset = _find_by_field(T_DATASET, "名单编号", voucher["名单编号"])
            if dataset is None:
                return None
            user_ids = [member["用户编号"] for member in dataset.get("成员", [])]
            segment_rows = [
                row for row in store.rows("user_segment") if row.get("用户编号") in user_ids
            ]
            model_rows = [
                row for row in store.rows("churn_dataset") if row.get("用户编号") in user_ids
            ]
            pending_rows = [
                row
                for row in store.rows(T_PENDING)
                if row.get("用户编号") in user_ids and row.get("是否当前")
            ]
            return {
                "凭证": voucher,
                "名单数据集": dataset,
                "上游用户分层记录": segment_rows,
                "上游预警模型数据集": model_rows,
                "待处理明细当前记录": pending_rows,
                "核对项": {
                    "用户价值分层": {row["用户编号"]: row.get("价值分层") for row in segment_rows},
                    "模型准确率": voucher.get("模型准确率"),
                    "预警推送频率": voucher.get("预警推送频率"),
                    "覆盖人数": voucher.get("覆盖人数"),
                },
            }

    def version_snapshot(self) -> dict[str, Any]:
        """联合发布的数据版本核对视图：规则版本 + 模型版本 + 数据版本。"""
        with _LOCK:
            return {
                "业务核验码": BUSINESS_CODE,
                "规范口径核验码": SPEC_CODE,
                "基准规则": rfm_spec.BASELINE_RULE_CODE,
                "规范口径": rfm_spec.spec_view(),
                "分层规则": [
                    {
                        "rule_id": row.get("id"),
                        "规则编号": row.get("规则编号"),
                        "规则版本": row.get("规则版本"),
                        "status": row.get("status"),
                    }
                    for row in store.rows("rfm")
                ],
                "预警模型": [
                    {
                        "model_id": row.get("id"),
                        "模型编号": row.get("模型编号"),
                        "模型版本": row.get("模型版本"),
                        "status": row.get("status"),
                        "模型准确率": row.get("模型准确率"),
                        "预警推送频率": row.get("预警推送频率"),
                    }
                    for row in store.rows("churn")
                ],
                "已发布名单": [
                    {
                        "名单编号": row.get("名单编号"),
                        "规则版本": row.get("规则版本"),
                        "模型版本": row.get("模型版本"),
                        "覆盖人数": row.get("覆盖人数"),
                        "生成时间": row.get("生成时间"),
                    }
                    for row in store.rows(T_DATASET)
                    if row.get("状态") == "已发布"
                ],
            }


joint_service = JointReleaseService()


def init_seed_receipts() -> None:
    """启动引导：种子数据里已启用/已部署的记录补齐回执，并归入同一发布批次。

    保证开箱即有一个两回执齐备、可发布的联合批次（RFM-0003 × CHUR-0003）。
    """
    from app.store import store as _store

    enabled_rules = [
        int(row["id"]) for row in _store.rows("rfm") if row.get("status") == RFM_ENABLED_STATUS
    ]
    deployed_models = [
        int(row["id"]) for row in _store.rows("churn") if row.get("status") == CHURN_DEPLOYED_STATUS
    ]
    if not enabled_rules and not deployed_models:
        return
    shared_batch = f"PUB-{datetime.now().strftime('%Y%m%d')}-SEED"
    for rule_id in enabled_rules:
        joint_service.register_rule_receipt(rule_id, batch_no=shared_batch)
    for model_id in deployed_models:
        joint_service.register_model_receipt(model_id, batch_no=shared_batch)
