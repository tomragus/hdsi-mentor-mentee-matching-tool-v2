import { useState } from 'react'
import type { MissingQuestion, UploadSummary } from '../api'

type Props = {
  summary: UploadSummary | null
  busy: boolean
  error: { message: string; missing?: MissingQuestion[] } | null
  onUpload: (mentorFile: File, menteeFile: File) => void
}

export function Upload({ summary, busy, error, onUpload }: Props) {
  const [mentorFile, setMentorFile] = useState<File | null>(null)
  const [menteeFile, setMenteeFile] = useState<File | null>(null)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (mentorFile && menteeFile) onUpload(mentorFile, menteeFile)
  }

  return (
    <section className="panel">
      <h2>1. Upload the two form exports</h2>

      <form className="uploads" onSubmit={submit}>
        <label>
          Mentor questionnaire
          <input
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={(event) => setMentorFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <label>
          Mentee questionnaire
          <input
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={(event) => setMenteeFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <button type="submit" disabled={!mentorFile || !menteeFile || busy}>
          {busy ? 'Checking…' : 'Upload'}
        </button>
      </form>

      {summary && (
        <p className="note">
          Read {summary.mentor_rows} mentor responses and {summary.mentee_rows} mentee
          responses against {summary.questions} questions.
        </p>
      )}

      {error && (
        <div className="error">
          <p>{error.message}</p>
          {error.missing && (
            // Naming the questions is the point: a coordinator has to know
            // which one to fix in the form.
            <ul>
              {error.missing.map((item) => (
                <li key={`${item.side}-${item.row}`}>
                  <strong>{item.side}</strong> row {item.row}: {item.question}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
