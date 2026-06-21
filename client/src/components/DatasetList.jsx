import { useState, useEffect } from 'react'
import './DatasetList.css'
import { API } from '../api'

export default function DatasetList({ token, refreshKey, onLoad, onNew }) {
  const [datasets, setDatasets] = useState([])

  useEffect(() => {
    fetch(`${API}/api/datasets`, { headers: { 'Authorization': `Bearer ${token}` } })
      .then(r => r.json())
      .then(data => Array.isArray(data) ? setDatasets(data) : [])
      .catch(() => {})
  }, [token, refreshKey])

  async function handleLoad(id) {
    const res = await fetch(`${API}/api/datasets/${id}`)
    const data = await res.json()
    onLoad(data)
  }

  async function handleDelete(id) {
    await fetch(`${API}/api/datasets/${id}`, { method: 'DELETE', headers: { 'Authorization': `Bearer ${token}` } })
    setDatasets(prev => prev.filter(d => d.id !== id))
  }

  return (
    <aside className="dataset-list">
      <button className="dataset-btn-new" onClick={onNew}>+ New dataset</button>
      <h3 className="dataset-list-title">My datasets</h3>
      {datasets.length === 0
        ? <p className="dataset-empty">No saved datasets yet.</p>
        : <ul className="dataset-items">
            {datasets.map(d => (
              <li key={d.id} className="dataset-item">
                <span className="dataset-name" title={d.name}>{d.name}</span>
                <div className="dataset-actions">
                  <button className="dataset-btn" onClick={() => handleLoad(d.id)}>Load</button>
                  <button className="dataset-btn dataset-btn-delete" onClick={() => handleDelete(d.id)}>✕</button>
                </div>
              </li>
            ))}
          </ul>
      }
    </aside>
  )
}
