"""RFM分层 × 流失预警 联合发布准入接口（业务核验码 04d4803e）。

覆盖：准入核对、回执登记、原子发布、流失去向名单与待处理明细查询、
业务凭证全链路追溯、数据版本核对。状态判断全部下沉到 service。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas import ActionResult, EntryPayload
from app.services.joint_release import joint_service

router = APIRouter(prefix="/api/joint-release", tags=["RFM×流失联合发布"])


@router.get("/spec")
def get_spec() -> dict:
    """RFM 分层业务规则规范口径（核验码 04d29b9b）：阈值、允许范围和上限。"""
    from app import rfm_spec

    return rfm_spec.spec_view()


@router.get("/versions")
def get_versions() -> dict:
    """联合发布数据版本核对：分层规则版本、模型版本与已发布名单版本。"""
    return joint_service.version_snapshot()


@router.get("/gate")
def get_gate(rule_id: int, model_id: int) -> dict:
    """准入核对（只读）：规则启用与模型部署、两回执是否共同满足。"""
    return joint_service.gate_status(rule_id, model_id)


@router.post("/receipts/rule", response_model=ActionResult)
def add_rule_receipt(payload: EntryPayload) -> ActionResult:
    """登记规则启用回执；可携带发布批次号把两回执归入同一批次。"""
    values = payload.values
    rule_id = values.get("rule_id")
    if rule_id is None:
        return ActionResult(ok=False, message="缺少 rule_id")
    receipt, message = joint_service.register_rule_receipt(int(rule_id), values.get("batch_no"))
    if receipt is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=receipt)


@router.post("/receipts/model", response_model=ActionResult)
def add_model_receipt(payload: EntryPayload) -> ActionResult:
    """登记模型部署回执；可携带发布批次号把两回执归入同一批次。"""
    values = payload.values
    model_id = values.get("model_id")
    if model_id is None:
        return ActionResult(ok=False, message="缺少 model_id")
    receipt, message = joint_service.register_model_receipt(int(model_id), values.get("batch_no"))
    if receipt is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=receipt)


@router.get("/receipts")
def list_receipts() -> dict:
    """列出全部规则启用/模型部署回执。"""
    items = joint_service.list_receipts()
    return {"module": "joint_receipt", "total": len(items), "items": items}


@router.get("/batches")
def list_batches() -> dict:
    """列出发布批次及其准入状态。"""
    items = joint_service.list_batches()
    return {"module": "joint_batch", "total": len(items), "items": items}


@router.post("/publish", response_model=ActionResult)
def publish(payload: EntryPayload) -> ActionResult:
    """联合发布：两状态/两回执共同满足时，原子生成唯一一份流失去向名单并投递待处理明细。"""
    values = payload.values
    rule_id = values.get("rule_id")
    model_id = values.get("model_id")
    if rule_id is None or model_id is None:
        return ActionResult(ok=False, message="缺少 rule_id 或 model_id")
    result, message = joint_service.publish(int(rule_id), int(model_id), values.get("operator"))
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.get("/datasets")
def list_datasets() -> dict:
    """流失去向名单明细数据集列表（每个发布批次最多一份）。"""
    items = joint_service.list_datasets()
    return {"module": "churn_target_dataset", "total": len(items), "items": items}


@router.get("/pending")
def list_pending(current_only: bool = False) -> dict:
    """流失预警待处理明细；current_only=true 时只看每个用户唯一一条当前名单记录。"""
    items = joint_service.list_pending(current_only=current_only)
    return {"module": "churn_pending_list", "total": len(items), "items": items}


@router.get("/vouchers")
def list_vouchers() -> dict:
    """业务凭证列表。"""
    from app.store import store

    items = store.rows("joint_voucher")
    return {"module": "joint_voucher", "total": len(items), "items": [dict(row) for row in items]}


@router.get("/vouchers/{voucher_no}/lineage")
def voucher_lineage(voucher_no: str) -> dict:
    """从业务凭证按记录追到同一份流失去向名单，并核对分层/准确率/频率/覆盖人数。"""
    lineage = joint_service.lineage(voucher_no)
    if lineage is None:
        raise HTTPException(status_code=404, detail=f"业务凭证 {voucher_no} 不存在")
    return lineage
