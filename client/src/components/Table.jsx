import { Fragment, useState, useRef, useEffect, useCallback } from 'react'
import './Table.css'
import { API } from '../api'

const PAGE_SIZE = 50

const NO_VALUE_OPS = new Set(['is_true', 'is_false', 'is_empty', 'not_empty'])

const OPERATORS = {
  string: [
    { label: 'contains',          op: 'contains'        },
    { label: 'not contains',      op: 'not_contains'    },
    { label: 'equals',            op: 'equals'          },
    { label: 'not equals',        op: 'not_equals'      },
    { label: 'starts with',       op: 'starts_with'     },
    { label: 'not starts with',   op: 'not_starts_with' },
    { label: 'is empty',          op: 'is_empty'        },
    { label: 'is not empty',      op: 'not_empty'       },
  ],
  limited: [
    { label: 'equals',            op: 'equals'          },
    { label: 'not equals',        op: 'not_equals'      },
    { label: 'is empty',          op: 'is_empty'        },
    { label: 'is not empty',      op: 'not_empty'       },
  ],
  number: [
    { label: '=',            op: 'eq'        },
    { label: '≠',            op: 'neq'       },
    { label: '>',            op: 'gt'        },
    { label: '<',            op: 'lt'        },
    { label: '≥',            op: 'gte'       },
    { label: '≤',            op: 'lte'       },
    { label: 'is empty',     op: 'is_empty'  },
    { label: 'is not empty', op: 'not_empty' },
  ],
  date: [
    { label: 'equals',       op: 'eq'        },
    { label: 'not equals',   op: 'neq'       },
    { label: 'before',       op: 'lt'        },
    { label: 'after',        op: 'gt'        },
    { label: 'on or before', op: 'lte'       },
    { label: 'on or after',  op: 'gte'       },
    { label: 'is empty',     op: 'is_empty'  },
    { label: 'is not empty', op: 'not_empty' },
  ],
  boolean: [
    { label: 'is true',  op: 'is_true'  },
    { label: 'is false', op: 'is_false' },
  ],
}

const DEFAULT_OP = {
  string: 'contains', limited: 'equals', number: 'eq', date: 'eq', boolean: 'is_true',
}

function FilterRow({ filter, colMeta, onUpdate, onRemove }) {
  const col = colMeta.find(c => c.name === filter.col)
  const colType = col?.type ?? 'string'
  const operators = OPERATORS[colType] ?? OPERATORS.string
  const showValue = !NO_VALUE_OPS.has(filter.op)

  function handleColChange(e) {
    const newCol = e.target.value
    const newType = colMeta.find(c => c.name === newCol)?.type ?? 'string'
    onUpdate({ col: newCol, op: DEFAULT_OP[newType], val: '' })
  }

  return (
    <span className="table-filter-row">
      <select className="fcol" value={filter.col} onChange={handleColChange}>
        {colMeta.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
      </select>
      <select className="fop" value={filter.op} onChange={e => onUpdate({ op: e.target.value, val: '' })}>
        {operators.map(({ label, op }) => <option key={op} value={op}>{label}</option>)}
      </select>
      {showValue && (
        colType === 'limited' && col?.options
          ? <select className="fval" value={filter.val} onChange={e => onUpdate({ val: e.target.value })}>
              <option value="">any</option>
              {col.options.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          : <input
              className="fval"
              type={colType === 'number' ? 'number' : colType === 'date' ? 'date' : 'text'}
              value={filter.val}
              placeholder="value"
              onChange={e => onUpdate({ val: e.target.value })}
            />
      )}
      <button className="table-btn table-btn-remove" onClick={onRemove}>×</button>
    </span>
  )
}

function LogicToggle({ value, onChange }) {
  return (
    <span className="table-logic-toggle">
      <button className={`table-logic-btn${value === 'AND' ? ' active' : ''}`} onClick={() => onChange('AND')}>AND</button>
      <button className={`table-logic-btn${value === 'OR' ? ' active' : ''}`} onClick={() => onChange('OR')}>OR</button>
    </span>
  )
}

function FilterGroup({ group, colMeta, onUpdateLogic, onAddFilter, onUpdateFilter, onRemoveFilter, onRemove }) {
  return (
    <div className="table-filter-group">
      {group.filters.length >= 2 && <LogicToggle value={group.logic} onChange={onUpdateLogic} />}
      <span className="table-filter-group-filters">
        {group.filters.map(f => (
          <FilterRow key={f.id} filter={f} colMeta={colMeta}
            onUpdate={patch => onUpdateFilter(f.id, patch)}
            onRemove={() => onRemoveFilter(f.id)} />
        ))}
        <button className="table-btn table-btn-icon" onClick={onAddFilter} disabled={!colMeta.length} title="Add filter">+</button>
      </span>
      <button className="table-btn table-btn-remove" onClick={onRemove} title="Remove group">×</button>
    </div>
  )
}

export default function Table({
  name = '',
  headers = [],
  content = [],
  onSave = null,
  readOnly = false,
  datasetId = null,
  onOpenDatasets = null,
}) {
  const [currentDatasetId, setCurrentDatasetId] = useState(datasetId)
  const [colMeta, setColMeta] = useState([])
  const [columns, setColumns] = useState(() => headers.map(h => (Array.isArray(h) ? h[0] : h)))
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [tableName, setTableName] = useState(name)
  const [searchText, setSearchText] = useState('')
  const [filterTree, setFilterTree] = useState({ logic: 'AND', groups: [] })
  const [appliedFiltersJson, setAppliedFiltersJson] = useState('')
  const [showFilters, setShowFilters] = useState(false)
  const [fileError, setFileError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [showTextInput, setShowTextInput] = useState(false)
  const [rawText, setRawText] = useState('')
  const [saved, setSaved] = useState(false)
  const [chatHistory, setChatHistory] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [showChat, setShowChat] = useState(true)

  const nextIdRef = useRef(0)
  const fileInputRef = useRef(null)
  const chatEndRef = useRef(null)
  const chatInputRef = useRef(null)
  const fetchAbortRef = useRef(null)

  function getNextId() { return nextIdRef.current++ }

  const fetchRows = useCallback(async (dsId, pg, search, fJson) => {
    if (!dsId) return
    if (fetchAbortRef.current) fetchAbortRef.current.abort()
    const controller = new AbortController()
    fetchAbortRef.current = controller

    const params = new URLSearchParams({ dataset_id: dsId, page: pg, page_size: PAGE_SIZE })
    if (search) params.set('search', search)
    if (fJson) params.set('filters', fJson)
    try {
      const res = await fetch(`${API}/api/rows?${params}`, { signal: controller.signal })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Failed to load rows')
      setRows(data.rows)
      setTotal(data.total)
      setPages(data.pages)
      if (data.columns.length > 0) setColumns(data.columns)
    } catch (err) {
      if (err.name === 'AbortError') return
      setFileError(err.message)
    }
  }, [])

  useEffect(() => {
    fetchRows(currentDatasetId, page, searchText, appliedFiltersJson)
  }, [currentDatasetId, page, searchText, appliedFiltersJson, fetchRows])

  useEffect(() => {
    if (!currentDatasetId) { setColMeta([]); return }
    fetch(`${API}/api/columns?dataset_id=${currentDatasetId}`)
      .then(r => r.json())
      .then(data => setColMeta(data.columns ?? []))
      .catch(() => {})
  }, [currentDatasetId])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatHistory, chatLoading])

  useEffect(() => {
    if (!chatLoading) chatInputRef.current?.focus()
  }, [chatLoading])

  async function uploadData(body, isMultipart) {
    setUploading(true)
    setFileError('')
    try {
      const res = await fetch(`${API}/api/upload`, {
        method: 'POST',
        ...(isMultipart
          ? { body }
          : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Upload failed')
      setCurrentDatasetId(data.dataset_id)
      setColumns(data.columns)
      setTableName(data.name)
      setPage(1)
      setSearchText('')
      setFilterTree({ logic: 'AND', groups: [] })
      setAppliedFiltersJson('')
      setShowFilters(false)
      setSaved(false)
      setChatHistory([])
    } catch (err) {
      setFileError(err.message)
    } finally {
      setUploading(false)
    }
  }

  function handleFileLoad(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const formData = new FormData()
    formData.append('file', file)
    uploadData(formData, true)
    e.target.value = ''
  }

  function handleTextLoad() {
    uploadData({ csv: rawText, name: tableName || 'Table' }, false)
    setShowTextInput(false)
    setRawText('')
  }

  async function handleSave() {
    if (!onSave || !currentDatasetId) return
    try {
      const params = new URLSearchParams({ dataset_id: currentDatasetId, page: 1, page_size: 10000 })
      const res = await fetch(`${API}/api/rows?${params}`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.error)
      await onSave({
        name: tableName,
        headers: data.columns.map(c => [c, 'string']),
        rows: data.rows,
      })
      setSaved(true)
    } catch (err) {
      setFileError(err.message)
    }
  }

  async function handleChat() {
    const question = chatInput.trim()
    if (!question || !currentDatasetId) return
    const userMsg = { role: 'user', content: question }
    const newHistory = [...chatHistory, userMsg]
    setChatHistory(newHistory)
    setChatInput('')
    setChatLoading(true)
    chatInputRef.current?.focus()
    try {
      const res = await fetch(`${API}/api/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset_id: currentDatasetId, question, history: chatHistory }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Request failed')
      setChatHistory([...newHistory, { role: 'assistant', content: data.answer }])
    } catch (err) {
      setChatHistory([...newHistory, { role: 'assistant', content: `Error: ${err.message}` }])
    } finally {
      setChatLoading(false)
    }
  }

  function clearTable() {
    setCurrentDatasetId(null)
    setColMeta([])
    setColumns([])
    setRows([])
    setTotal(0)
    setPage(1)
    setPages(1)
    setTableName('')
    setSearchText('')
    setFilterTree({ logic: 'AND', groups: [] })
    setAppliedFiltersJson('')
    setShowFilters(false)
    setFileError('')
    setSaved(false)
    setChatHistory([])
    setChatInput('')
  }

  function addGroup() {
    const groupId = getNextId()
    setFilterTree(prev => ({
      ...prev,
      groups: [...prev.groups, { id: groupId, logic: 'AND', filters: [] }],
    }))
  }

  function addFilterToGroup(groupId) {
    if (colMeta.length === 0) return
    const first = colMeta[0]
    const filterId = getNextId()
    setFilterTree(prev => ({
      ...prev,
      groups: prev.groups.map(g =>
        g.id !== groupId ? g : {
          ...g,
          filters: [...g.filters, { id: filterId, col: first.name, op: DEFAULT_OP[first.type], val: '' }],
        }
      ),
    }))
  }

  function updateFilterInGroup(groupId, filterId, patch) {
    setFilterTree(prev => ({
      ...prev,
      groups: prev.groups.map(g =>
        g.id !== groupId ? g : {
          ...g,
          filters: g.filters.map(f => f.id !== filterId ? f : { ...f, ...patch }),
        }
      ),
    }))
  }

  function removeFilterFromGroup(groupId, filterId) {
    setFilterTree(prev => ({
      ...prev,
      groups: prev.groups.map(g =>
        g.id !== groupId ? g : { ...g, filters: g.filters.filter(f => f.id !== filterId) }
      ),
    }))
  }

  function removeGroup(groupId) {
    setFilterTree(prev => ({ ...prev, groups: prev.groups.filter(g => g.id !== groupId) }))
  }

  function setGroupLogic(groupId, logic) {
    setFilterTree(prev => ({
      ...prev,
      groups: prev.groups.map(g => g.id !== groupId ? g : { ...g, logic }),
    }))
  }

  function setTopLogic(logic) {
    setFilterTree(prev => ({ ...prev, logic }))
  }

  function applyFilters() {
    const hasActive = filterTree.groups.some(g =>
      g.filters.some(f => f.val !== '' || NO_VALUE_OPS.has(f.op))
    )
    setAppliedFiltersJson(hasActive ? JSON.stringify(filterTree) : '')
    setPage(1)
  }

  function resetFilters() {
    setFilterTree({ logic: 'AND', groups: [] })
    setAppliedFiltersJson('')
    setPage(1)
  }

  const hasData = currentDatasetId !== null && columns.length > 0
  const hasPendingGroups = filterTree.groups.length > 0
  const filtersApplied = appliedFiltersJson !== ''
  const activeFilterCount = filterTree.groups.reduce((n, g) =>
    n + g.filters.filter(f => f.val !== '' || NO_VALUE_OPS.has(f.op)).length, 0
  )

  return (
    <section className="table-root">
      <input ref={fileInputRef} type="file" accept=".txt,.csv" hidden onChange={handleFileLoad} />

      {!hasData ? (
        <div className="table-upload-landing">
          <p className="table-upload-title">Load a dataset to explore</p>
          <div className="table-upload-options">
            <button
              className="table-upload-option"
              onClick={() => { setShowTextInput(false); setFileError(''); fileInputRef.current.click() }}
              disabled={uploading}
            >
              <strong>Load from file</strong>
              <span>{uploading ? 'Uploading…' : 'Upload a CSV or TXT file'}</span>
            </button>
            <button
              className="table-upload-option"
              onClick={() => { setShowTextInput(v => !v); setFileError('') }}
            >
              <strong>Paste CSV text</strong>
              <span>Type or paste comma-separated data</span>
            </button>
            {onOpenDatasets && (
              <button className="table-upload-option" onClick={onOpenDatasets}>
                <strong>Open saved dataset</strong>
                <span>Load a previously saved dataset</span>
              </button>
            )}
          </div>

          {showTextInput && (
            <div className="table-text-input table-upload-text">
              <textarea
                value={rawText}
                onChange={e => setRawText(e.target.value)}
                placeholder={'Name,Age,Joined,Active\n"Braund, Mr. Owen",22,2020-01-15,true\n"Cumings, Mrs. John",38,2019-06-03,false'}
                rows={6}
              />
              <div className="table-text-input-actions">
                <button className="table-btn" onClick={handleTextLoad} disabled={uploading}>Apply</button>
                <button className="table-btn" onClick={() => { setShowTextInput(false); setRawText(''); setFileError('') }}>Cancel</button>
              </div>
            </div>
          )}

          {fileError && <span className="table-error">{fileError}</span>}
        </div>
      ) : (
        <>
          <div className="table-title-row">
            {readOnly
              ? <p className="table-name-display">{tableName}</p>
              : <input
                  className="table-name-input"
                  type="text"
                  value={tableName}
                  onChange={e => setTableName(e.target.value)}
                  placeholder="Table name"
                />
            }
            {!readOnly && (
              <>
                {onSave && !saved && (
                  <button className="table-btn" onClick={handleSave}>Save</button>
                )}
                <button className="table-btn table-btn-danger" onClick={clearTable}>
                  {saved ? 'Upload new dataset' : 'Clear'}
                </button>
                {fileError && <span className="table-error">{fileError}</span>}
              </>
            )}
          </div>

          <div className="table-search">
            <input
              type="search"
              placeholder="Search all columns…"
              value={searchText}
              onChange={e => { setSearchText(e.target.value); setPage(1) }}
            />
          </div>

          <div className="table-body-row">
            <div className="table-col">
              {/* Always-visible filter bar */}
              <div className="table-filter-bar">
                <button
                  className={`table-btn table-filter-toggle${showFilters ? ' active' : ''}`}
                  onClick={() => {
                    setShowFilters(v => {
                      if (!v && filterTree.groups.length === 0) {
                        setFilterTree(prev => ({
                          ...prev,
                          groups: [{ id: getNextId(), logic: 'AND', filters: [] }],
                        }))
                      }
                      return !v
                    })
                  }}
                  disabled={colMeta.length === 0}
                >
                  {showFilters ? '▾' : '▸'} Filters
                  {activeFilterCount > 0 && <span className="table-filter-badge">{activeFilterCount}</span>}
                </button>
                {showFilters && (
                  <>
                    <button className="table-btn table-btn-icon table-add-filter" onClick={addGroup} disabled={!colMeta.length} title="Add filter group">+ group</button>
                    {hasPendingGroups && (
                      <>
                        <button className="table-btn table-btn-apply" onClick={applyFilters}>Apply</button>
                        <button className="table-btn" onClick={resetFilters}>Reset</button>
                      </>
                    )}
                    {!hasPendingGroups && filtersApplied && (
                      <button className="table-btn" onClick={resetFilters}>Clear</button>
                    )}
                  </>
                )}
                {!showFilters && filtersApplied && (
                  <span className="table-filter-active-note">{activeFilterCount} active</span>
                )}
                {!showChat && (
                  <button className="table-btn" style={{ marginLeft: 'auto' }} onClick={() => setShowChat(true)}>
                    Show chat
                  </button>
                )}
              </div>

              {/* Collapsible filter panel */}
              {showFilters && (
                <div className="table-filters">
                  {filterTree.groups.map((group, gi) => (
                    <Fragment key={group.id}>
                      {gi > 0 && (
                        <div className="table-filter-group-connector">
                          <LogicToggle value={filterTree.logic} onChange={setTopLogic} />
                        </div>
                      )}
                      <FilterGroup
                        group={group}
                        colMeta={colMeta}
                        onUpdateLogic={logic => setGroupLogic(group.id, logic)}
                        onAddFilter={() => addFilterToGroup(group.id)}
                        onUpdateFilter={(fid, patch) => updateFilterInGroup(group.id, fid, patch)}
                        onRemoveFilter={fid => removeFilterFromGroup(group.id, fid)}
                        onRemove={() => removeGroup(group.id)}
                      />
                    </Fragment>
                  ))}
                </div>
              )}

              <div className="table-scroll-wrapper">
                <table className="table-grid">
                  <thead>
                    <tr>
                      {columns.map(col => <th key={col}>{col}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, i) => (
                      <tr key={i}>
                        {columns.map(col => <td key={col}>{String(row[col] ?? '')}</td>)}
                      </tr>
                    ))}
                    {rows.length === 0 && (
                      <tr className="table-empty">
                        <td colSpan={columns.length || 1}>No results.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              <div className="table-pagination">
                <p className="table-row-count">
                  {total === 0
                    ? 'No rows'
                    : `${(page - 1) * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE, total)} of ${total} rows`}
                </p>
                {pages > 1 && (
                  <div className="table-page-controls">
                    <button className="table-btn" disabled={page <= 1} onClick={() => setPage(1)}>«</button>
                    <button className="table-btn" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>‹</button>
                    <span className="table-page-label">Page {page} of {pages}</span>
                    <button className="table-btn" disabled={page >= pages} onClick={() => setPage(p => p + 1)}>›</button>
                    <button className="table-btn" disabled={page >= pages} onClick={() => setPage(pages)}>»</button>
                  </div>
                )}
              </div>
            </div>

            {showChat && <div className="table-chat">
              <h3 className="table-chat-title">
                Ask about this dataset
                <button className="table-chat-close" onClick={() => setShowChat(false)}>×</button>
              </h3>
              <div className="table-chat-messages">
                {chatHistory.map((msg, i) => (
                  <div key={i} className={`table-chat-msg table-chat-msg-${msg.role}`}>
                    {msg.content}
                  </div>
                ))}
                {chatLoading && (
                  <div className="table-chat-msg table-chat-msg-assistant table-chat-loading">…</div>
                )}
                <div ref={chatEndRef} />
              </div>
              <div className="table-chat-input-row">
                <input
                  ref={chatInputRef}
                  type="text"
                  placeholder="Ask a question about the data…"
                  value={chatInput}
                  onChange={e => setChatInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && !chatLoading && handleChat()}
                  disabled={chatLoading}
                />
                <button
                  className="table-btn"
                  onClick={handleChat}
                  disabled={chatLoading || !chatInput.trim()}
                >
                  Send
                </button>
              </div>
            </div>}
          </div>
        </>
      )}
    </section>
  )
}
