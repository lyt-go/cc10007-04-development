import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/joint-release'

type Json = Record<string, string | number | boolean | null>

type Version = {
  rule_id: number
  规则编号: string
  规则版本: string
  status: string
}
type ModelVersion = {
  model_id: number
  模型编号: string
  模型版本: string
  status: string
  模型准确率: number | string
  预警推送频率: string
}
type Dataset = {
  id: number
  名单编号: string
  发布批次: string
  rule_id: number
  model_id: number
  规则编号: string
  规则版本: string
  模型编号: string
  模型版本: string
  模型准确率: number | string
  预警推送频率: string
  覆盖人数: number
  状态: string
  生成时间: string
  成员: Json[]
}
type PendingRow = {
  id: number
  用户编号: string
  名单编号: string
  发布批次: string
  规则编号: string
  规则版本: string
  模型编号: string
  模型版本: string
  价值分层: string
  流失概率: number
  预警推送频率: string
  状态: string
  是否当前: boolean
  投递时间: string
}
type Batch = {
  id: number
  发布批次: string
  rule_id: number | null
  model_id: number | null
  规则回执齐备: boolean
  模型回执齐备: boolean
  准入状态: string
  更新时间: string
}
type Voucher = {
  id: number
  凭证编号: string
  业务核验码: string
  规范口径核验码: string
  发布批次: string
  名单编号: string
  规则编号: string
  规则版本: string
  模型编号: string
  模型版本: string
  模型准确率: number | string
  预警推送频率: string
  覆盖人数: number
  开立时间: string
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export default function JointReleaseView() {
  const [versions, setVersions] = useState<{ 分层规则: Version[]; 预警模型: ModelVersion[]; 已发布名单: Json[] } | null>(null)
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [pending, setPending] = useState<PendingRow[]>([])
  const [batches, setBatches] = useState<Batch[]>([])
  const [vouchers, setVouchers] = useState<Voucher[]>([])
  const [ruleId, setRuleId] = useState('3')
  const [modelId, setModelId] = useState('3')
  const [gate, setGate] = useState<Json | null>(null)
  const [lineage, setLineage] = useState<Json | null>(null)
  const [message, setMessage] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [currentOnly, setCurrentOnly] = useState(true)

  const reload = useCallback(async () => {
    setErrorMessage('')
    try {
      const [versionSnap, datasetPage, pendingPage, batchPage, voucherPage] = await Promise.all([
        getJson<{ 分层规则: Version[]; 预警模型: ModelVersion[]; 已发布名单: Json[] }>(`${ENDPOINT}/versions`),
        getJson<{ items: Dataset[] }>(`${ENDPOINT}/datasets`),
        getJson<{ items: PendingRow[] }>(`${ENDPOINT}/pending?current_only=${currentOnly}`),
        getJson<{ items: Batch[] }>(`${ENDPOINT}/batches`),
        getJson<{ items: Voucher[] }>(`${ENDPOINT}/vouchers`),
      ])
      setVersions(versionSnap)
      setDatasets(datasetPage.items)
      setPending(pendingPage.items)
      setBatches(batchPage.items)
      setVouchers(voucherPage.items)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '联合发布数据读取失败')
    }
  }, [currentOnly])

  useEffect(() => {
    void reload()
  }, [reload])

  const checkGate = useCallback(async () => {
    setErrorMessage('')
    setMessage('')
    try {
      const data = await getJson<Json>(`${ENDPOINT}/gate?rule_id=${encodeURIComponent(ruleId)}&model_id=${encodeURIComponent(modelId)}`)
      setGate(data)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '准入核对失败')
    }
  }, [ruleId, modelId])

  const publish = useCallback(async () => {
    setErrorMessage('')
    setMessage('')
    try {
      const response = await request(`${ENDPOINT}/publish`, {
        method: 'POST',
        body: JSON.stringify({ values: { rule_id: Number(ruleId), model_id: Number(modelId) } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        setErrorMessage(payload.message || '联合发布未生效')
      } else {
        setMessage(payload.message)
      }
      await reload()
      await checkGate()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '联合发布失败')
    }
  }, [ruleId, modelId, reload, checkGate])

  const traceLineage = useCallback(async (voucherNo: string) => {
    setErrorMessage('')
    try {
      const data = await getJson<Json>(`${ENDPOINT}/vouchers/${encodeURIComponent(voucherNo)}/lineage`)
      setLineage(data)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '凭证追溯失败')
    }
  }, [])

  const stats = useMemo(() => {
    const currentPending = pending.filter((row) => row.是否当前)
    const covered = new Set(currentPending.map((row) => row.用户编号)).size
    return [
      { label: '流失去向名单数据集', value: datasets.length },
      { label: '待处理当前用户', value: covered },
      { label: '可发布/已发布批次', value: batches.filter((b) => b.准入状态 === '可发布' || b.准入状态 === '已发布').length },
      { label: '业务凭证', value: vouchers.length },
    ]
  }, [datasets, pending, batches, vouchers])

  const pendingColumns = ['用户编号', '价值分层', '流失概率', '规则编号', '规则版本', '模型编号', '模型版本', '预警推送频率', '状态', '是否当前', '投递时间']
  const datasetColumns = ['名单编号', '发布批次', '规则版本', '模型版本', '模型准确率', '预警推送频率', '覆盖人数', '状态', '生成时间']

  return (
    <section className="page" data-module="joint-release" data-change-code="043be525">
      <header className="page-head">
        <div>
          <h2>RFM分层 × 流失预警 联合发布准入</h2>
          <p className="page-desc">
            业务核验码 04d4803e · RFM规范口径核验码 04d29b9b · 核对基准 RFM-0003。
            规则启用与模型部署共同满足才允许成榜，竞态下只留存一份流失去向名单明细数据集，原子投递待处理明细。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" data-verification-code="04e766f7" onClick={() => void reload()}>刷新业务页</button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <div className="filter-bar">
        <label className="filter-item">
          <span>分层规则ID（RFM-0003=3）</span>
          <input value={ruleId} onChange={(e) => setRuleId(e.target.value)} />
        </label>
        <label className="filter-item">
          <span>预警模型ID（CHUR-0003=3）</span>
          <input value={modelId} onChange={(e) => setModelId(e.target.value)} />
        </label>
        <button className="btn" type="button" onClick={() => void checkGate()}>准入核对</button>
        <button className="btn primary" type="button" onClick={() => void publish()}>联合发布成榜</button>
      </div>

      {gate && (
        <table className="data-table" style={{ marginBottom: 12 }}>
          <thead>
            <tr><th>规则启用</th><th>模型部署</th><th>规则启用回执</th><th>模型部署回执</th><th>发布批次</th><th>准入状态</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>{gate['规则启用'] ? '✅ 已启用' : '⛔ 未启用'}</td>
              <td>{gate['模型部署'] ? '✅ 已部署' : '⛔ 未部署'}</td>
              <td>{gate['规则启用回执'] ? '✅' : '⛔'}</td>
              <td>{gate['模型部署回执'] ? '✅' : '⛔'}</td>
              <td>{String(gate['发布批次'] ?? '—')}</td>
              <td>{String(gate['准入状态'])}</td>
            </tr>
          </tbody>
        </table>
      )}
      {gate && Array.isArray(gate['阻断原因']) && (gate['阻断原因'] as unknown[]).length > 0 && (
        <footer className="page-foot"><span className="error-text">阻断原因：{(gate['阻断原因'] as string[]).join('；')}</span></footer>
      )}

      <h3>发布批次与回执</h3>
      <table className="data-table">
        <thead>
          <tr><th>发布批次</th><th>规则回执</th><th>模型回执</th><th>准入状态</th><th>更新时间</th></tr>
        </thead>
        <tbody>
          {batches.map((batch) => (
            <tr key={batch.id}>
              <td>{batch.发布批次}</td>
              <td>{batch.规则回执齐备 ? '✅' : '⛔'}</td>
              <td>{batch.模型回执齐备 ? '✅' : '⛔'}</td>
              <td>{batch.准入状态}</td>
              <td>{batch.更新时间}</td>
            </tr>
          ))}
          {batches.length === 0 && <tr><td className="empty-state" colSpan={5}>暂无发布批次</td></tr>}
        </tbody>
      </table>

      <h3>流失去向名单明细数据集（每批次仅一份）</h3>
      <table className="data-table">
        <thead>
          <tr>{datasetColumns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {datasets.map((dataset) => (
            <tr key={dataset.id}>
              {datasetColumns.map((c) => <td key={c}>{String((dataset as unknown as Json)[c] ?? '—')}</td>)}
            </tr>
          ))}
          {datasets.length === 0 && <tr><td className="empty-state" colSpan={datasetColumns.length}>尚未生成联合名单</td></tr>}
        </tbody>
      </table>

      <h3>名单成员（RFM-0003 分层结果驱动）</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>名单编号</th><th>用户编号</th><th>价值分层</th><th>规范复算分层</th><th>流失概率</th>
            <th>规则版本</th><th>模型版本</th><th>分层记录版本</th><th>模型数据版本</th>
          </tr>
        </thead>
        <tbody>
          {datasets.flatMap((dataset) =>
            dataset.成员.map((member) => (
              <tr key={`${dataset.名单编号}-${String(member['用户编号'])}`}>
                <td>{dataset.名单编号}</td>
                <td>{String(member['用户编号'])}</td>
                <td>{String(member['价值分层'])}</td>
                <td>{String(member['规范复算分层'])}</td>
                <td>{String(member['流失概率'])}</td>
                <td>{String(member['规则版本'])}</td>
                <td>{String(member['模型版本'])}</td>
                <td>{String(member['分层记录版本'])}</td>
                <td>{String(member['模型数据版本'])}</td>
              </tr>
            )),
          )}
          {datasets.length === 0 && <tr><td className="empty-state" colSpan={9}>暂无名单成员</td></tr>}
        </tbody>
      </table>

      <h3>流失预警待处理明细（投递目标 · 每用户一条当前记录）</h3>
      <div className="filter-bar">
        <label className="filter-item">
          <span>
            <input type="checkbox" checked={currentOnly} onChange={(e) => setCurrentOnly(e.target.checked)} /> 仅看当前名单记录
          </span>
        </label>
      </div>
      <table className="data-table">
        <thead>
          <tr>{pendingColumns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {pending.map((row) => (
            <tr key={row.id}>
              {pendingColumns.map((c) => <td key={c}>{String((row as unknown as Json)[c] ?? '—')}</td>)}
            </tr>
          ))}
          {pending.length === 0 && <tr><td className="empty-state" colSpan={pendingColumns.length}>暂无待处理记录</td></tr>}
        </tbody>
      </table>

      <h3 data-verification-code="045a23e9">业务凭证与链路核对（核验码 045a23e9 清单回看）</h3>
      <table className="data-table">
        <thead>
          <tr><th>凭证编号</th><th>发布批次</th><th>名单编号</th><th>规则版本</th><th>模型版本</th><th>模型准确率</th><th>推送频率</th><th>覆盖人数</th><th>操作</th></tr>
        </thead>
        <tbody>
          {vouchers.map((voucher) => (
            <tr key={voucher.id}>
              <td>{voucher.凭证编号}</td>
              <td>{voucher.发布批次}</td>
              <td>{voucher.名单编号}</td>
              <td>{voucher.规则版本}</td>
              <td>{voucher.模型版本}</td>
              <td>{String(voucher.模型准确率)}</td>
              <td>{voucher.预警推送频率}</td>
              <td>{voucher.覆盖人数}</td>
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void traceLineage(voucher.凭证编号)}>追溯同一份名单</button>
              </td>
            </tr>
          ))}
          {vouchers.length === 0 && <tr><td className="empty-state" colSpan={9}>暂无业务凭证</td></tr>}
        </tbody>
      </table>

      {lineage && (
        <div style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: 8, padding: 12, marginTop: 12 }}>
          <h3>链路核对：{String((lineage as Json)['凭证'] ? (lineage as unknown as { 凭证: { 凭证编号: string } }).凭证.凭证编号 : '')}</h3>
          <p className="page-desc">
            名单 {String((lineage as unknown as { 名单数据集: { 名单编号: string } }).名单数据集.名单编号)} ·
            上游分层表 user_segment · 上游模型数据集 churn_dataset ·
            覆盖人数 {String((lineage as unknown as { 核对项: { 覆盖人数: number } }).核对项.覆盖人数)} ·
            模型准确率 {String((lineage as unknown as { 核对项: { 模型准确率: number | string } }).核对项.模型准确率)} ·
            推送频率 {String((lineage as unknown as { 核对项: { 预警推送频率: string } }).核对项.预警推送频率)}
          </p>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>
            {JSON.stringify((lineage as unknown as { 核对项: unknown }).核对项, null, 2)}
          </pre>
        </div>
      )}

      <footer className="page-foot">
        <span>共 {datasets.length} 份名单数据集 · {pending.length} 条待处理记录</span>
        {message && <span style={{ color: '#067647' }}>{message}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
