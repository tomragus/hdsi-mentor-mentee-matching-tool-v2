import { useState } from 'react'
import { openMatch, runMatching, uploadExports } from './api'
import type { MatchDetail as Detail, MissingQuestion, Report, UploadSummary } from './api'
import { Leaderboard } from './components/Leaderboard'
import { MatchDetail } from './components/MatchDetail'
import { Review } from './components/Review'
import { Upload } from './components/Upload'

type Failure = { message: string; missing?: MissingQuestion[] }

export default function App() {
  const [summary, setSummary] = useState<UploadSummary | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<Failure | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleUpload(mentorFile: File, menteeFile: File) {
    setBusy(true)
    setError(null)
    const result = await uploadExports(mentorFile, menteeFile)
    if (result.ok) {
      setSummary(result.data)
      // A new cohort invalidates whatever is currently on screen.
      setReport(null)
    } else {
      setSummary(null)
      setError(result)
    }
    setBusy(false)
  }

  async function handleRun() {
    setBusy(true)
    setError(null)
    const result = await runMatching()
    if (result.ok) setReport(result.data)
    else setError(result)
    setBusy(false)
  }

  async function handleOpen(mentorKey: string, menteeKey: string) {
    const result = await openMatch(mentorKey, menteeKey)
    if (result.ok) setDetail(result.data)
    else setError(result)
  }

  return (
    <main>
      <h1>HDSI Mentor / Mentee Matching</h1>

      <Upload
        summary={summary}
        busy={busy}
        error={error?.missing ? error : null}
        onUpload={handleUpload}
      />

      {summary && (
        <section className="panel">
          <h2>2. Run the match</h2>
          <button onClick={handleRun} disabled={busy}>
            {busy ? 'Working…' : report ? 'Run again' : 'Run matching'}
          </button>
          {!report && (
            <p className="note">
              The first run loads the embedding model, so it takes a few seconds.
            </p>
          )}
        </section>
      )}

      {error && !error.missing && <div className="panel error">{error.message}</div>}

      {report && (
        <>
          <Leaderboard report={report} onOpen={handleOpen} />
          <Review report={report} />
        </>
      )}

      <MatchDetail detail={detail} onClose={() => setDetail(null)} />
    </main>
  )
}
