import { useState, useRef, useEffect } from 'react'
import './Table.css'

const OPERATORS = {
  string: ['contains', 'equals', 'starts_with'],
  number: ['=', '>', '<', '>=', '<='],
  date: ['=', 'before', 'after'],
  boolean: ['is true', 'is false'],
}

const DEFAULT_OP = {
  string: 'contains',
  number: '=',
  date: '=',
  boolean: 'is true',
}

function matchesFilter(value, operator, filterValue, type) {
  const v = String(value ?? '').toLowerCase()
  const fv = filterValue.toLowerCase()

  if (type === 'string') {
    if (operator === 'contains') return v.includes(fv)
    if (operator === 'equals') return v === fv
    if (operator === 'starts_with') return v.startsWith(fv)
  }

  if (type === 'number') {
    const n = Number(value)
    const fn = Number(filterValue)
    if (isNaN(n) || isNaN(fn)) return false
    if (operator === '=') return n === fn
    if (operator === '>') return n > fn
    if (operator === '<') return n < fn
    if (operator === '>=') return n >= fn
    if (operator === '<=') return n <= fn
  }

  if (type === 'date') {
    const d = new Date(value)
    const fd = new Date(filterValue)
    if (isNaN(d) || isNaN(fd)) return false
    if (operator === '=') return d.getTime() === fd.getTime()
    if (operator === 'before') return d < fd
    if (operator === 'after') return d > fd
  }

  if (type === 'boolean') {
    const b = String(value).toLowerCase() === 'true'
    if (operator === 'is true') return b
    if (operator === 'is false') return !b
  }

  return true
}

function FilterRow({ filter, headers, rows, onUpdate, onRemove }) {
  const colType = headers.find(([col]) => col === filter.column)?.[1] ?? 'string'
  const operators = OPERATORS[colType] ?? OPERATORS.string
  const showValueInput = colType !== 'boolean'
  const isEquality = filter.operator === 'equals' || filter.operator === '='

  const uniqueVals = isEquality && colType === 'string'
    ? [...new Set(rows.map(r => String(r[filter.column] ?? '')).filter(v => v !== ''))].sort()
    : []
  const showDropdown = uniqueVals.length > 0 && uniqueVals.length <= 10

  function handleColumnChange(e) {
    const newCol = e.target.value
    const newType = headers.find(([col]) => col === newCol)?.[1] ?? 'string'
    onUpdate(filter.id, { column: newCol, operator: DEFAULT_OP[newType], value: '' })
  }

  return (
    <div className="table-filter-row">
      <select value={filter.column} onChange={handleColumnChange}>
        {headers.map(([col]) => <option key={col} value={col}>{col}</option>)}
      </select>
      <select value={filter.operator} onChange={e => onUpdate(filter.id, { operator: e.target.value, value: '' })}>
        {operators.map(op => <option key={op} value={op}>{op}</option>)}
      </select>
      {showValueInput && (
        showDropdown
          ? <select value={filter.value} onChange={e => onUpdate(filter.id, { value: e.target.value })}>
              <option value="">— any —</option>
              {uniqueVals.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          : <input
              type={colType === 'number' ? 'number' : colType === 'date' ? 'date' : 'text'}
              value={filter.value}
              placeholder="value"
              onChange={e => onUpdate(filter.id, { value: e.target.value })}
            />
      )}
      <button className="table-btn table-btn-remove" onClick={() => onRemove(filter.id)}>×</button>
    </div>
  )
}

function parseCSVLine(line) {
  const fields = []
  let i = 0
  while (i < line.length) {
    if (line[i] === '"') {
      let field = ''
      i++
      while (i < line.length) {
        if (line[i] === '"' && line[i + 1] === '"') { field += '"'; i += 2 }
        else if (line[i] === '"') { i++; break }
        else { field += line[i++] }
      }
      fields.push(field)
      if (line[i] === ',') i++
    } else {
      const end = line.indexOf(',', i)
      if (end === -1) { fields.push(line.slice(i)); break }
      fields.push(line.slice(i, end))
      i = end + 1
    }
  }
  return fields
}

function inferType(values) {
  const nonEmpty = values.filter(v => v !== '')
  if (nonEmpty.length === 0) return 'string'
  if (nonEmpty.every(v => ['true', 'false'].includes(v.toLowerCase()))) return 'boolean'
  if (nonEmpty.every(v => !isNaN(Number(v)))) return 'number'
  if (nonEmpty.every(v => /^\d{4}-\d{2}-\d{2}$/.test(v))) return 'date'
  return 'string'
}

function parseCSV(text, fallbackName = 'Table') {
  const lines = text.split('\n').filter(l => l.trim() && !l.startsWith('#'))
  if (lines.length < 1) throw new Error('File is empty')

  const colNames = parseCSVLine(lines[0])
  if (colNames.length === 0) throw new Error('No columns found in header row')

  const rows = lines.slice(1).map(line => {
    const values = parseCSVLine(line)
    return Object.fromEntries(colNames.map((col, i) => [col, values[i] ?? '']))
  })

  const headers = colNames.map(col => [col, inferType(rows.map(r => r[col] ?? ''))])

  return { name: fallbackName, headers, rows }
}

function buildContextSummary(name, hdrs, rowCount) {
  const cols = hdrs.map(([col, type]) => `${col} (${type})`).join(', ')
  return `Dataset "${name}": ${rowCount} rows. Columns: ${cols}.`
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
  const [tableName, setTableName] = useState(name)
  const [tableHeaders, setHeaders] = useState(headers)
  const [rows, setRows] = useState(content)
  const [searchText, setSearchText] = useState('')
  const [filters, setFilters] = useState([])
  const [nextFilterId, setNextFilterId] = useState(0)
  const [fileError, setFileError] = useState('')
  const [typeErrors, setTypeErrors] = useState([])
  const [showTextInput, setShowTextInput] = useState(false)
  const [rawText, setRawText] = useState('')
  const [context, setContext] = useState({ summary: initialContext?.summary ?? '' })
  const [saved, setSaved] = useState(false)
  const [chatHistory, setChatHistory] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)

  const fileInputRef = useRef(null)
  const chatEndRef = useRef(null)
  const chatInputRef = useRef(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatHistory, chatLoading])

  useEffect(() => {
    if (!chatLoading) chatInputRef.current?.focus()
  }, [chatLoading])

  async function validateTypes(hdrs, rws) {
    try {
      const res = await fetch('/api/validate-types', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ headers: hdrs, rows: rws }),
      })
      const data = await res.json()
      if (!res.ok) {
        setFileError(data.error ?? 'Type validation request failed')
        return
      }
      setTypeErrors(data.errors)
    } catch {
      setFileError('Could not reach server for type validation')
    }
  }

  function handleFileLoad(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const fileName = file.name.replace(/\.[^/.]+$/, '')
    const reader = new FileReader()
    reader.onload = ev => {
      try {
        const { name: parsedName, headers: parsedHeaders, rows: parsedRows } = parseCSV(ev.target.result, fileName)
        setTableName(parsedName)
        setHeaders(parsedHeaders)
        setRows(parsedRows)
        setSearchText('')
        setFilters([])
        setNextFilterId(0)
        setFileError('')
        setTypeErrors([])
        setSaved(false)
        setContext({ summary: buildContextSummary(parsedName, parsedHeaders, parsedRows.length) })
        validateTypes(parsedHeaders, parsedRows)
      } catch (err) {
        setFileError(err.message)
      }
    }
    reader.onerror = () => setFileError('Could not read file')
    reader.readAsText(file)
    e.target.value = ''
  }

  function handleTextLoad() {
    try {
      const { name: parsedName, headers: parsedHeaders, rows: parsedRows } = parseCSV(rawText, tableName || 'Table')
      setTableName(parsedName)
      setHeaders(parsedHeaders)
      setRows(parsedRows)
      setSearchText('')
      setFilters([])
      setNextFilterId(0)
      setFileError('')
      setTypeErrors([])
      setShowTextInput(false)
      setRawText('')
      setSaved(false)
      setContext({ summary: buildContextSummary(parsedName, parsedHeaders, parsedRows.length) })
      validateTypes(parsedHeaders, parsedRows)
    } catch (err) {
      setFileError(err.message)
    }
  }

  async function handleChat() {
    const query = chatInput.trim()
    if (!query) return
    const userMsg = { role: 'user', content: query }
    const newHistory = [...chatHistory, userMsg]
    setChatHistory(newHistory)
    setChatInput('')
    setChatLoading(true)
    chatInputRef.current?.focus()
    try {
      const schema = `Columns: ${tableHeaders.map(([col, type]) => `${col} (${type})`).join(', ')}\nTotal rows: ${rows.length}`
      const body = datasetId
        ? { query, schema, summary: context.summary, history: chatHistory, dataset_id: datasetId, headers: tableHeaders }
        : { query, schema, summary: context.summary, history: chatHistory, rows, headers: tableHeaders }
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Chat request failed')
      setChatHistory([...newHistory, { role: 'assistant', content: data.response }])
    } catch (err) {
      setChatHistory([...newHistory, { role: 'assistant', content: `Error: ${err.message}` }])
    } finally {
      setChatLoading(false)
    }
  }

  function clearTable() {
    setTableName('')
    setHeaders([])
    setRows([])
    setFilters([])
    setSearchText('')
    setTypeErrors([])
    setFileError('')
    setContext({ summary: '' })
    setChatHistory([])
    setChatInput('')
  }

  function addFilter() {
    if (tableHeaders.length === 0) return
    const [col, type] = tableHeaders[0]
    setFilters(prev => [...prev, { id: nextFilterId, column: col, operator: DEFAULT_OP[type], value: '' }])
    setNextFilterId(n => n + 1)
  }

  function updateFilter(id, patch) {
    setFilters(prev => prev.map(f => {
      if (f.id !== id) return f
      const updated = { ...f, ...patch }
      if (patch.column !== undefined) {
        const newType = tableHeaders.find(([col]) => col === patch.column)?.[1] ?? 'string'
        updated.operator = DEFAULT_OP[newType]
        updated.value = ''
      }
      return updated
    }))
  }

  function removeFilter(id) {
    setFilters(prev => prev.filter(f => f.id !== id))
  }

  const filteredRows = rows.filter(row =>
    filters.every(f => {
      if (f.value === '' && f.operator !== 'is true' && f.operator !== 'is false') return true
      const colType = tableHeaders.find(([col]) => col === f.column)?.[1] ?? 'string'
      return matchesFilter(row[f.column], f.operator, f.value, colType)
    })
  )

  const visibleRows = searchText
    ? filteredRows.filter(row =>
        tableHeaders.some(([col]) =>
          String(row[col] ?? '').toLowerCase().includes(searchText.toLowerCase())
        )
      )
    : filteredRows

  return (
    <section className="table-root">
      {/* Hidden file input — always mounted so ref is always valid */}
      <input ref={fileInputRef} type="file" accept=".txt,.csv" hidden onChange={handleFileLoad} />

      {tableHeaders.length === 0 ? (
        /* ── Landing / upload state ── */
        <div className="table-upload-landing">
          <p className="table-upload-title">Load a dataset to explore</p>
          <div className="table-upload-options">
            <button
              className="table-upload-option"
              onClick={() => { setShowTextInput(false); setFileError(''); fileInputRef.current.click() }}
            >
              <strong>Load from file</strong>
              <span>Upload a CSV or TXT file</span>
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
                <button className="table-btn" onClick={handleTextLoad}>Apply</button>
                <button className="table-btn" onClick={() => { setShowTextInput(false); setRawText(''); setFileError('') }}>Cancel</button>
              </div>
            </div>
          )}

          {fileError && <span className="table-error">{fileError}</span>}
        </div>
      ) : (
        /* ── Dataset loaded state ── */
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
                <button className="table-btn" onClick={() => { onSave({ name: tableName, headers: tableHeaders, rows, context }); setSaved(true) }}>Save</button>
              )}
              <button className="table-btn table-btn-danger" onClick={clearTable}>
                {saved ? 'Upload new dataset' : 'Clear'}
              </button>
              {fileError && <span className="table-error">{fileError}</span>}
            </div>
          )}

          {typeErrors.length > 0 && (
            <div className="table-type-errors">
              <strong>Type errors:</strong>
              <ul>
                {typeErrors.map((e, i) => (
                  <li key={i}>Row {e.row}, &ldquo;{e.column}&rdquo;: expected {e.expected}, got &ldquo;{e.value}&rdquo;</li>
                ))}
              </ul>
            </div>
          )}

          <div className="table-search">
            <input
              type="search"
              placeholder="Search all columns…"
              value={searchText}
              onChange={ev => setSearchText(ev.target.value)}
            />
          </div>

          <div className="table-filters">
            {filters.map(f => (
              <FilterRow key={f.id} filter={f} headers={tableHeaders} rows={rows} onUpdate={updateFilter} onRemove={removeFilter} />
            ))}
            <button className="table-btn table-add-filter" onClick={addFilter}>+ Add filter</button>
          </div>

          <div className="table-body-row">
            <div className="table-col">
              <div className="table-scroll-wrapper">
                <table className="table-grid">
                  <thead>
                    <tr>
                      {tableHeaders.map(([col]) => <th key={col}>{col}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRows.map((row, i) => (
                      <tr key={i}>
                        {tableHeaders.map(([col]) => <td key={col}>{String(row[col] ?? '')}</td>)}
                      </tr>
                    ))}
                    {visibleRows.length === 0 && (
                      <tr className="table-empty">
                        <td colSpan={tableHeaders.length || 1}>No results.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <p className="table-row-count">
                {visibleRows.length < rows.length
                  ? `${visibleRows.length} of ${rows.length} rows`
                  : `${rows.length} rows`}
              </p>
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
