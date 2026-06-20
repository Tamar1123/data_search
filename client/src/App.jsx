import { useState } from 'react'
import './App.css'
import Table from './components/Table'
import Auth from './components/Auth'
import DatasetList from './components/DatasetList'

function App() {
  const [user, setUser] = useState(null)
  const [tableKey, setTableKey] = useState(0)
  const [tableData, setTableData] = useState({ name: '', headers: [], content: [], context: null, readOnly: false, id: null })
  const [refreshKey, setRefreshKey] = useState(0)
  const [showDatasets, setShowDatasets] = useState(false)

  const emptyTable = { name: '', headers: [], content: [], context: null, readOnly: false, id: null }

  function handleAuth(newUser) {
    setUser(newUser)
    setTableData(emptyTable)
    setTableKey(k => k + 1)
    setRefreshKey(0)
    setShowDatasets(false)
  }

  async function handleSave({ name, headers, rows, context }) {
    await fetch('/api/datasets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${user.token}` },
      body: JSON.stringify({ name, headers, rows, context }),
    })
    setRefreshKey(k => k + 1)
  }

  function handleLoad(dataset) {
    setTableData({ name: dataset.name, headers: dataset.headers, content: dataset.rows, context: dataset.context ?? null, readOnly: true, id: dataset.id })
    setTableKey(k => k + 1)
    setShowDatasets(false)
  }

  function handleNew() {
    setTableData(emptyTable)
    setTableKey(k => k + 1)
    setShowDatasets(false)
  }

  if (!user) {
    return <Auth onAuth={handleAuth} />
  }

  return (
    <div className="app-wrap">
      <div className="app-header">
        <span className="app-welcome">Welcome, {user.username}</span>
        <div className="app-header-right">
          {!user.isGuest && (
            <button
              className={`app-datasets-btn${showDatasets ? ' active' : ''}`}
              onClick={() => setShowDatasets(v => !v)}
            >
              {showDatasets ? 'Hide datasets' : 'My datasets'}
            </button>
          )}
          <button className="app-signout" onClick={() => { setUser(null); setTableData(emptyTable); setTableKey(k => k + 1); setShowDatasets(false) }}>
            {user.isGuest ? 'Sign in' : 'Switch user'}
          </button>
        </div>
      </div>
      <div className="app-body">
        {!user.isGuest && showDatasets && (
          <DatasetList
            token={user.token}
            refreshKey={refreshKey}
            onLoad={handleLoad}
            onNew={handleNew}
          />
        )}
        <main className="app-main">
          <Table
            key={tableKey}
            name={tableData.name}
            headers={tableData.headers}
            content={tableData.content}
            initialContext={tableData.context}
            onSave={user.isGuest ? null : handleSave}
            readOnly={tableData.readOnly}
            datasetId={tableData.id}
            onOpenDatasets={user.isGuest ? null : () => setShowDatasets(true)}
          />
        </main>
      </div>
    </div>
  )
}

export default App
