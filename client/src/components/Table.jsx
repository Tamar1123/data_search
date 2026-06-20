import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import './Table.css'

const PAGE_SIZE = 50

const OPERATORS = {
  string:  [{ label: 'contains',     op: 'contains'    },
            { label: 'equals',       op: 'equals'      },
            { label: 'starts with',  op: 'starts_with' }],
  limited: [{ label: 'equals',       op: 'equals'      }],
  number:  [{ label: '=',            op: 'eq'          },
            { label: '>',            op: 'gt'          },
            { label: '<',            op: 'lt'          },
            { label: '≥',            op: 'gte'         },
            { label: '≤',            op: 'lte'         }],
  date:    [{ label: 'equals',       op: 'eq'          },
            { label: 'before',       op: 'lt'          },
            { label: 'after',        op: 'gt'          },
            { label: 'on or before', op: 'lte'         },
            { label: 'on or after',  op: 'gte'         }],
  boolean: [{ label: 'is true',      op: 'is_true'     },
            { label: 'is false',     op: 'is_false'    }],
}

const DEFAULT_OP = {
  string: 'contains', limited: 'equals', number: 'eq', date: 'eq', boolean: 'is_true',
}

function FilterRow({ filter, colMeta, onUpdate, onRemove }) {
  const col = colMeta.find(c => c.name === filter.col)
  const colType = col?.type ?? 'string'
  const operators = OPERATORS[colType] ?? OPERATORS.string
  const showValue = colType !== 'boolean'

  function handleColChange(e) {
    const newCol = e.target.value
    const newType = colMeta.find(c => c.name === newCol)?.type ?? 'string'
    onUpdate(filter.id, { col: newCol, op: DEFAULT_OP[newType], val: '' })
  }

  return (
    <div className="table-filter-row">
      <select value={filter.col} onChange={handleColChange}>
        {colMeta.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
      </select>
      <select value={filter.op} onChange={e => onUpdate(filter.id, { op: e.target.value, val: '' })}>
        {operators.map(({ label, op }) => <option key={op} value={op}>{label}</option>)}
      </select>
      {showValue && (
        colType === 'limited' && col?.options
          ? <select value={filter.val} onChange={e => onUpdate(filter.id, { val: e.target.value })}>
              <option value="">— any —</option>
              {col.options.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          : <input
              type={colType === 'number' ? 'number' : colType === 'date' ? 'date' : 'text'}
              value={filter.val}
              placeholder="value"
              onChange={e => onUpdate(filter.id, { val: e.target.value })}
            />
      )}
      <button className="table-btn table-btn-remove" onClick={() => onRemove(filter.id)}>×</button>
    </div>
  )
}

export default function Table({
  name = '',
  headers = [],
  content = [],
  onSave = null,
  initialContext = null,
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
  const [filters, setFilters] = useState([])
  const [nextFilterId, setNextFilterId] = useState(0)
  const [fileError, setFileError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [showTextInput, setShowTextInput] = useState(false)
  const [rawText, setRawText] = useState('')
  const [saved, setSaved] = useState(false)
  const [chatHistory, setChatHistory] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)

  const fileInputRef = useRef(null)
  const chatEndRef = useRef(null)
  const chatInputRef = useRef(null)
  const fetchAbortRef = useRef(null)

  // Only active filters are sent to the server
  const filtersJson = useMemo(() => {
    const active = filters.filter(f => f.val !== '' || f.op === 'is_true' || f.op === 'is_false')
    return active.length ? JSON.stringify(active.map(({ col, op, val }) => ({ col, op, val }))) : ''
  }, [filters])

  const fetchRows = useCallback(async (dsId, pg, search, fJson) => {
    if (!dsId) return
    if (fetchAbortRef.current) fetchAbortRef.current.abort()
    const controller = new AbortController()
    fetchAbortRef.current = controller

    const params = new URLSearchParams({ dataset_id: dsId, page: pg, page_size: PAGE_SIZE })
    if (search) params.set('search', search)
    if (fJson) params.set('filters', fJson)
    try {
      const res = await fetch(`/api/rows?${params}`, { signal: controller.signal })
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
    fetchRows(currentDatasetId, page, searchText, filtersJson)
  }, [currentDatasetId, page, searchText, filtersJson, fetchRows])

  // Fetch column type metadata whenever the dataset changes
  useEffect(() => {
    if (!currentDatasetId) { setColMeta([]); return }
    fetch(`/api/columns?dataset_id=${currentDatasetId}`)
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
      const res = await fetch('/api/upload', {
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
      setFilters([])
      setNextFilterId(0)
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
      const res = await fetch(`/api/rows?${params}`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.error)
      await onSave({
        name: tableName,
        headers: data.columns.map(c => [c, 'string']),
        rows: data.rows,
        context: { summary: '' },
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
      const res = await fetch('/api/ask', {
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
    setFilters([])
    setNextFilterId(0)
    setFileError('')
    setSaved(false)
    setChatHistory([])
    setChatInput('')
  }

  function addFilter() {
    if (colMeta.length === 0) return
    const first = colMeta[0]
    setFilters(prev => [...prev, { id: nextFilterId, col: first.name, op: DEFAULT_OP[first.type], val: '' }])
    setNextFilterId(n => n + 1)
    setPage(1)
  }

  function updateFilter(id, patch) {
    setFilters(prev => prev.map(f => f.id !== id ? f : { ...f, ...patch }))
    setPage(1)
  }

  function removeFilter(id) {
    setFilters(prev => prev.filter(f => f.id !== id))
    setPage(1)
  }

  const hasData = currentDatasetId !== null && columns.length > 0

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
            <div className="table-file-loader">
              {onSave && !saved && (
                <button className="table-btn" onClick={handleSave}>Save</button>
              )}
              <button className="table-btn table-btn-danger" onClick={clearTable}>
                {saved ? 'Upload new dataset' : 'Clear'}
              </button>
              {fileError && <span className="table-error">{fileError}</span>}
            </div>
          )}

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
              <div className="table-filters">
                {filters.map(f => (
                  <FilterRow key={f.id} filter={f} colMeta={colMeta} onUpdate={updateFilter} onRemove={removeFilter} />
                ))}
                <button className="table-btn table-add-filter" onClick={addFilter} disabled={colMeta.length === 0}>
                  + Add filter
                </button>
                {filters.length > 0 && (
                  <button className="table-btn" onClick={() => { setFilters([]); setPage(1) }}>
                    Reset filters
                  </button>
                )}
              </div>
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

            <div className="table-chat">
              <h3 className="table-chat-title">Ask about this dataset</h3>
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
            </div>
          </div>
        </>
      )}
    </section>
  )
}
