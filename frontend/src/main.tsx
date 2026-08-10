// Entry point. Kept separate from App.tsx so that file has an export, which is
// what lets Vite hot-reload edits without dropping the loaded report.

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
