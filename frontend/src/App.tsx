import { useState } from 'react'
import { flagReasons, openMatch, openPerson, runMatching, uploadExports } from './api'
import type {
  Match,
  MatchDetail,
  MissingQuestion,
  PersonDetail,
  Report,
  UploadSummary,
} from './api'
import { MatchSheet, PersonSheet } from './components/Detail'

type Failure = { message: string; missing?: MissingQuestion[] }

const pairKey = (match: Match) => `${match.mentor_key}|${match.mentee_key}`

// --- the shell ---------------------------------------------------------------

export default function App() {
  const [summary, setSummary] = useState<UploadSummary | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [detail, setDetail] = useState<MatchDetail | null>(null)
  const [person, setPerson] = useState<PersonDetail | null>(null)
  const [error, setError] = useState<Failure | null>(null)
  const [busy, setBusy] = useState(false)

  // The only manual state there is. Which solver matches were pulled apart,
  // and which pairs were made by hand. Everything the manual area shows is
  // derived from these two, so the pool can never disagree with the table.
  const [pulled, setPulled] = useState<Set<string>>(new Set())
  const [manualPairs, setManualPairs] = useState<Match[]>([])

  function resetManual() {
    setPulled(new Set())
    setManualPairs([])
  }

  async function handleUpload(mentorFile: File, menteeFile: File) {
    setBusy(true)
    setError(null)
    const result = await uploadExports(mentorFile, menteeFile)
    if (result.ok) {
      setSummary(result.data)
      // A new cohort invalidates whatever is currently on screen.
      setReport(null)
      resetManual()
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
    if (result.ok) {
      setReport(result.data)
      // A fresh solve supersedes every hand adjustment made against the old one.
      resetManual()
    } else setError(result)
    setBusy(false)
  }

  async function handleOpen(mentorKey: string, menteeKey: string) {
    const result = await openMatch(mentorKey, menteeKey)
    if (result.ok) setDetail(result.data)
    else setError(result)
  }

  async function handleOpenPerson(key: string) {
    const result = await openPerson(key)
    if (result.ok) setPerson(result.data)
    else setError(result)
  }

  function handlePull(match: Match) {
    if (match.manual) {
      setManualPairs((pairs) => pairs.filter((p) => pairKey(p) !== pairKey(match)))
      return
    }
    setPulled((keys) => new Set(keys).add(pairKey(match)))
  }

  async function handlePair(mentorKey: string, menteeKey: string) {
    // Every pair is scored, including ones the solver never used, so a
    // hand-made pair can show a real percentage.
    const result = await openMatch(mentorKey, menteeKey)
    if (!result.ok) return setError(result)
    setManualPairs((pairs) => [...pairs, toMatch(result.data)])
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
        <Results
          report={report}
          pulled={pulled}
          manualPairs={manualPairs}
          onPull={handlePull}
          onPair={handlePair}
          onOpen={handleOpen}
          onOpenPerson={handleOpenPerson}
        />
      )}

      <MatchSheet detail={detail} onClose={() => setDetail(null)} />
      <PersonSheet person={person} onClose={() => setPerson(null)} />
    </main>
  )
}

// --- step 1: the uploads -----------------------------------------------------

function Upload({
  summary,
  busy,
  error,
  onUpload,
}: {
  summary: UploadSummary | null
  busy: boolean
  error: Failure | null
  onUpload: (mentorFile: File, menteeFile: File) => void
}) {
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

// --- what a run produced -----------------------------------------------------

/** Turn an opened pair into a row the matches table can render. */
function toMatch(detail: MatchDetail): Match {
  return {
    mentor_key: detail.mentor.key,
    mentor_name: detail.mentor.name,
    mentee_key: detail.mentee.key,
    mentee_name: detail.mentee.name,
    percentage: detail.percentage ?? 0,
    scored_questions: detail.scored_questions,
    mentor_capacity: 0, // filled in from the roster below
    manual: true,
  }
}

function Flag({ reasons }: { reasons: string[] | undefined }) {
  if (!reasons) return null
  // The CSS draws the tooltip from this attribute on hover.
  return (
    <span className="flag" data-reasons={reasons.join('\n')}>
      &#128681;
    </span>
  )
}

type ResultsProps = {
  report: Report
  pulled: Set<string>
  manualPairs: Match[]
  onPull: (match: Match) => void
  onPair: (mentorKey: string, menteeKey: string) => void
  onOpen: (mentorKey: string, menteeKey: string) => void
  onOpenPerson: (key: string) => void
}

function Results({
  report,
  pulled,
  manualPairs,
  onPull,
  onPair,
  onOpen,
  onOpenPerson,
}: ResultsProps) {
  // Which mentor card a dragged mentee is currently over, for the highlight.
  const [over, setOver] = useState<string | null>(null)

  // Every mentor reaches the pool from one of these two lists, and only these
  // carry their capacity.
  const mentors = new Map<string, { name: string; capacity: number }>()
  for (const match of report.matches) {
    mentors.set(match.mentor_key, {
      name: match.mentor_name,
      capacity: match.mentor_capacity,
    })
  }
  for (const mentor of report.unmatched_mentors) {
    mentors.set(mentor.mentor_key, {
      name: mentor.mentor_name,
      capacity: mentor.capacity,
    })
  }

  const mentees = new Map<string, string>()
  for (const match of report.matches) mentees.set(match.mentee_key, match.mentee_name)
  for (const entry of report.waitlist) mentees.set(entry.mentee_key, entry.mentee_name)

  const active = [
    ...report.matches.filter((match) => !pulled.has(pairKey(match))),
    ...manualPairs.map((match) => ({
      ...match,
      mentor_capacity: mentors.get(match.mentor_key)?.capacity ?? 1,
    })),
  ].sort((a, b) => b.percentage - a.percentage)

  const used = new Map<string, number>()
  for (const match of active) {
    used.set(match.mentor_key, (used.get(match.mentor_key) ?? 0) + 1)
  }
  const taken = new Set(active.map((match) => match.mentee_key))

  // A mentor with a place left belongs in the pool even while matched to
  // somebody else, so a capacity-2 mentor can take a second mentee by hand.
  const poolMentors = [...mentors]
    .map(([key, mentor]) => ({ key, ...mentor, used: used.get(key) ?? 0 }))
    .filter((mentor) => mentor.used < mentor.capacity)

  const poolMentees = [...mentees]
    .filter(([key]) => !taken.has(key))
    .map(([key, name]) => ({ key, name }))

  const reasons = flagReasons(report)

  return (
    <>
      <section className="panel">
        <h2>
          Matches <span className="count">{active.length}</span>
        </h2>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Score</th>
                <th>Mentor</th>
                <th>Mentee</th>
                <th>Questions</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {active.map((match) => (
                <tr key={pairKey(match)}>
                  <td className="score">
                    {match.percentage}%
                    {match.manual && <span className="tag">manual</span>}
                  </td>
                  <td>
                    {match.mentor_name}
                    {/* Only mentors offering more than one place, since a "1/1"
                        on every other row is noise. */}
                    {match.mentor_capacity > 1 && (
                      <span className="tag">
                        {used.get(match.mentor_key) ?? 0}/{match.mentor_capacity}
                      </span>
                    )}
                    <Flag reasons={reasons.get(match.mentor_key)} />
                  </td>
                  <td>
                    {match.mentee_name}
                    <Flag reasons={reasons.get(match.mentee_key)} />
                  </td>
                  {/* How many questions the score rests on, so a match built
                      on very little is visible rather than hidden. */}
                  <td className="muted">{match.scored_questions}</td>
                  <td className="actions">
                    <button onClick={() => onOpen(match.mentor_key, match.mentee_key)}>
                      Open
                    </button>
                    {/* Breaks the pair and sends both people to the manual area. */}
                    <button onClick={() => onPull(match)}>&rarr; manual</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>Manual matching</h2>
        <p className="note">
          Drag a mentee onto a mentor to pair them. Everyone without a match is
          here already; use &ldquo;&rarr; manual&rdquo; on a match to bring that
          pair back. Nothing here is scored or checked. Mentors leave this list
          once their places are filled.
        </p>

        <div className="columns">
          <div>
            <h3>
              Mentors <span className="count">{poolMentors.length}</span>
            </h3>
            {poolMentors.length === 0 && (
              <p className="note">Nobody with a free place.</p>
            )}
            {/* Only mentors with a place left reach this list, so every card
                here accepts a drop. A mentor who fills up simply leaves. */}
            {poolMentors.map((mentor) => (
              <div
                key={mentor.key}
                className={`card${over === mentor.key ? ' over' : ''}`}
                // Calling preventDefault is what marks an element as a valid
                // drop target.
                onDragOver={(event) => {
                  event.preventDefault()
                  setOver(mentor.key)
                }}
                onDragLeave={() => setOver(null)}
                onDrop={(event) => {
                  event.preventDefault()
                  setOver(null)
                  const menteeKey = event.dataTransfer.getData('text/plain')
                  if (menteeKey) onPair(mentor.key, menteeKey)
                }}
              >
                <div>
                  {mentor.name}
                  <Flag reasons={reasons.get(mentor.key)} />
                </div>
                <div className="actions">
                  {mentor.capacity > 1 && (
                    <span className="tag">
                      {mentor.used}/{mentor.capacity}
                    </span>
                  )}
                  <button onClick={() => onOpenPerson(mentor.key)}>Open</button>
                </div>
              </div>
            ))}
          </div>

          <div>
            <h3>
              Mentees <span className="count">{poolMentees.length}</span>
            </h3>
            {poolMentees.length === 0 && (
              <p className="note">Everyone has a match.</p>
            )}
            {poolMentees.map((mentee) => (
              <div
                key={mentee.key}
                className="card draggable"
                draggable
                onDragStart={(event) =>
                  event.dataTransfer.setData('text/plain', mentee.key)
                }
              >
                <div>
                  {mentee.name}
                  <Flag reasons={reasons.get(mentee.key)} />
                </div>
                <div className="actions">
                  <button onClick={() => onOpenPerson(mentee.key)}>Open</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  )
}
