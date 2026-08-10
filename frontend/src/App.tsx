import { useState } from 'react'

// The whole client: the shapes the backend returns, typed wrappers over its
// four endpoints, every component, and the mount call at the bottom.
//
// Each fetch wrapper returns a Result rather than throwing, so callers have to
// deal with the failure case, and so the upload error can carry the list of
// questions that could not be found.

// --- what the backend returns ------------------------------------------------

type MissingQuestion = { side: string; row: number; question: string }

type Result<T> =
  | { ok: true; data: T }
  | { ok: false; message: string; missing?: MissingQuestion[] }

type UploadSummary = {
  mentor_rows: number
  mentee_rows: number
  questions: number
}

type Match = {
  mentor_key: string
  mentor_name: string
  mentee_key: string
  mentee_name: string
  percentage: number
  scored_questions: number
  mentor_capacity: number
  // Set on pairs the coordinator made by hand, which the backend never sends.
  manual?: true
}

type WaitlistEntry = {
  mentee_key: string
  mentee_name: string
}

type UnmatchedMentor = {
  mentor_key: string
  mentor_name: string
  capacity: number
}

type ReviewFlag = {
  respondent_key: string
  reason: string
}

type Report = {
  matches: Match[]
  waitlist: WaitlistEntry[]
  unmatched_mentors: UnmatchedMentor[]
  review_flags: ReviewFlag[]
}

type QuestionRow = {
  row: number
  question: string
  mentor_answer: string
  mentee_answer: string
}

type MatchDetail = {
  mentor: { key: string; name: string }
  mentee: { key: string; name: string }
  percentage: number | null
  scored_questions: number
  questions: QuestionRow[]
}

type PersonDetail = {
  key: string
  name: string
  side: string
  email: string
  capacity: number
  questions: { row: number; question: string | null; answer: string }[]
}

// One person can trip more than one check, so the reasons collect into a list
// and share a single flag.
function flagReasons(report: Report): Map<string, string[]> {
  const reasons = new Map<string, string[]>()
  for (const flag of report.review_flags) {
    const existing = reasons.get(flag.respondent_key)
    if (existing) existing.push(flag.reason)
    else reasons.set(flag.respondent_key, [flag.reason])
  }
  return reasons
}

const OFFLINE = 'Could not reach the backend. Start it with: uv run uvicorn app.main:app'

async function send<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  try {
    const response = await fetch(path, init)
    const body = await response.json().catch(() => null)

    if (response.ok) return { ok: true, data: body as T }

    // The dev server answers with a gateway error, and no JSON, when the
    // backend is not listening. That is the common case, so it gets the
    // instruction rather than a status code.
    if (body === null && response.status >= 500) return { ok: false, message: OFFLINE }

    // The upload error is an object naming each unresolved question; every
    // other error is a plain string.
    const detail = body?.detail
    if (detail && typeof detail === 'object') {
      return { ok: false, message: detail.message, missing: detail.missing }
    }
    return { ok: false, message: detail ?? `Request failed (${response.status})` }
  } catch {
    return { ok: false, message: OFFLINE }
  }
}

function uploadExports(
  mentorFile: File,
  menteeFile: File,
): Promise<Result<UploadSummary>> {
  const body = new FormData()
  body.append('mentor_file', mentorFile)
  body.append('mentee_file', menteeFile)
  return send<UploadSummary>('/api/upload', { method: 'POST', body })
}

function runMatching(): Promise<Result<Report>> {
  return send<Report>('/api/run', { method: 'POST' })
}

function openMatch(
  mentorKey: string,
  menteeKey: string,
): Promise<Result<MatchDetail>> {
  const path = `/api/match/${encodeURIComponent(mentorKey)}/${encodeURIComponent(menteeKey)}`
  return send<MatchDetail>(path)
}

function openPerson(key: string): Promise<Result<PersonDetail>> {
  return send<PersonDetail>(`/api/person/${encodeURIComponent(key)}`)
}


// --- the shell ---------------------------------------------------------------

type Failure = { message: string; missing?: MissingQuestion[] }

const pairKey = (match: Match) => `${match.mentor_key}|${match.mentee_key}`

export default function App() {
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

  // Undo keeps whole snapshots of the two above rather than a list of actions
  // to reverse. They are small, and it means any future action is undoable
  // without writing its inverse.
  const [history, setHistory] = useState<{ pulled: Set<string>; pairs: Match[] }[]>([])

  /** Record the current state, so the action about to happen can be undone. */
  function remember() {
    setHistory((past) => [...past, { pulled: new Set(pulled), pairs: [...manualPairs] }])
  }

  function handleUndo() {
    const previous = history[history.length - 1]
    if (!previous) return
    setPulled(previous.pulled)
    setManualPairs(previous.pairs)
    setHistory((past) => past.slice(0, -1))
  }

  function resetManual() {
    setPulled(new Set())
    setManualPairs([])
    setHistory([])
  }

  // Puts the page back to how it looks on a fresh load. The backend keeps the
  // uploaded cohort, exactly as it would across a browser refresh.
  function handleClear() {
    setReport(null)
    setDetail(null)
    setPerson(null)
    setError(null)
    resetManual()
  }

  // Loading a cohort and solving it are one action. The solve is deterministic,
  // so there was never a reason to do one without the other.
  async function handleMatch(mentorFile: File, menteeFile: File) {
    setBusy(true)
    setError(null)
    // A new cohort invalidates whatever is currently on screen.
    setReport(null)
    resetManual()

    const uploaded = await uploadExports(mentorFile, menteeFile)
    if (!uploaded.ok) {
      setError(uploaded)
      setBusy(false)
      return
    }

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

  async function handleOpenPerson(key: string) {
    const result = await openPerson(key)
    if (result.ok) setPerson(result.data)
    else setError(result)
  }

  function handlePull(match: Match) {
    remember()
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
    // Recorded only once the pair is certain, so a failed lookup leaves
    // nothing to undo.
    if (!result.ok) return setError(result)
    remember()
    setManualPairs((pairs) => [...pairs, toMatch(result.data)])
  }

  return (
    <main>
      <h1>HSDSC Mentor/Mentee Match</h1>

      <p className="note">
        <strong>Instructions:</strong> Open the Google Form responses in Google Sheets,
        then click <strong>File &rarr; Download &rarr; Comma Separated Values (.csv)</strong>. Then,
        upload both here and the scoring algorithm will sort through and make suitable matches in seconds! <br />
      </p>
      <p className="note">
        Open any match to view the responses — if you're not happy with a match,
        move it down to manual review where you can view individual mentor/mentee
        responses and make your own matches how you see fit!
        <br />
      </p>

      <Upload
        busy={busy}
        error={error?.missing ? error : null}
        onMatch={handleMatch}
        onClear={handleClear}
      />

      {error && !error.missing && <div className="panel error">{error.message}</div>}

      {report && (
        <Results
          report={report}
          pulled={pulled}
          manualPairs={manualPairs}
          canUndo={history.length > 0}
          onUndo={handleUndo}
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

// --- the uploads -------------------------------------------------------------

function Upload({
  busy,
  error,
  onMatch,
  onClear,
}: {
  busy: boolean
  error: Failure | null
  onMatch: (mentorFile: File, menteeFile: File) => void
  onClear: () => void
}) {
  const [mentorFile, setMentorFile] = useState<File | null>(null)
  const [menteeFile, setMenteeFile] = useState<File | null>(null)
  // Bumping this remounts the form, which is what empties the two file inputs;
  // setting their state to null leaves the chosen filenames on screen.
  const [formKey, setFormKey] = useState(0)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (mentorFile && menteeFile) onMatch(mentorFile, menteeFile)
  }

  function clear() {
    setMentorFile(null)
    setMenteeFile(null)
    setFormKey((n) => n + 1)
    onClear()
  }

  return (
    <section className="panel">
      <h2>Upload the two .csv files</h2>

      <form key={formKey} className="uploads" onSubmit={submit}>
        <label>
          <strong>Mentor questionnaire</strong>
          <input
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={(event) => setMentorFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <label>
          <strong>Mentee questionnaire</strong>
          <input
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={(event) => setMenteeFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <button type="submit" disabled={!mentorFile || !menteeFile || busy}>
          {busy ? 'Matching…' : 'Match'}
        </button>
        {/* type="button" so it does not submit the form it sits in. */}
        <button type="button" onClick={clear} disabled={busy}>
          Clear
        </button>
      </form>

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
  canUndo: boolean
  onUndo: () => void
  onPull: (match: Match) => void
  onPair: (mentorKey: string, menteeKey: string) => void
  onOpen: (mentorKey: string, menteeKey: string) => void
  onOpenPerson: (key: string) => void
}

function Results({
  report,
  pulled,
  manualPairs,
  canUndo,
  onUndo,
  onPull,
  onPair,
  onOpen,
  onOpenPerson,
}: ResultsProps) {
  // Which mentor card a dragged mentee is currently over, for the highlight.
  const [over, setOver] = useState<string | null>(null)
  // The card being carried, taken out of the list so a drag reads as picking
  // the card up rather than copying it.
  const [lifted, setLifted] = useState<string | null>(null)

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
        <header>
          <h2>
            Matches <span className="count">{active.length}</span>
          </h2>
          {/* Reverses the last pull or hand-made pair, one step at a time. */}
          <button onClick={onUndo} disabled={!canUndo}>
            Undo
          </button>
        </header>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Score</th>
                <th>Mentor</th>
                <th>Mentee</th>
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
                  <td className="actions">
                    <button onClick={() => onOpen(match.mentor_key, match.mentee_key)}>
                      Open
                    </button>
                    {/* Breaks the pair and sends both people to the manual area. */}
                    <button onClick={() => onPull(match)}>Manual Review</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>Manual review</h2>

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
                  setLifted(null)
                  const menteeKey = event.dataTransfer.getData('text/plain')
                  if (menteeKey) onPair(mentor.key, menteeKey)
                }}
              >
                {/* Name, places, flag -- the same three in the same order as
                    the matches table, so they sit the same way. */}
                <div>
                  {mentor.name}
                  {mentor.capacity > 1 && (
                    <span className="tag">
                      {mentor.used}/{mentor.capacity}
                    </span>
                  )}
                  <Flag reasons={reasons.get(mentor.key)} />
                </div>
                <div className="actions">
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
                className={`card draggable${lifted === mentee.key ? ' lifted' : ''}`}
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.setData('text/plain', mentee.key)
                  // Hidden on the next frame rather than straight away: the
                  // browser takes its picture of the card for the drag image
                  // first, and hiding it now would leave nothing to picture.
                  requestAnimationFrame(() => setLifted(mentee.key))
                }}
                onDragEnd={() => setLifted(null)}
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


// --- the two overlays --------------------------------------------------------

function Sheet({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string
  subtitle: string
  onClose: () => void
  children: React.ReactNode
}) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className="sheet" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h2>{title}</h2>
            <p className="note">{subtitle}</p>
          </div>
          <button onClick={onClose}>Close</button>
        </header>
        <div className="scroll">
          <table>{children}</table>
        </div>
      </div>
    </div>
  )
}

function MatchSheet({
  detail,
  onClose,
}: {
  detail: MatchDetail | null
  onClose: () => void
}) {
  if (!detail) return null

  return (
    <Sheet
      title={`${detail.mentor.name} & ${detail.mentee.name}`}
      subtitle={`${detail.percentage}% match`}
      onClose={onClose}
    >
      <thead>
        <tr>
          <th>Question</th>
          <th>Mentor</th>
          <th>Mentee</th>
        </tr>
      </thead>
      <tbody>
        {detail.questions.map((question) => (
          <tr key={question.row}>
            <td>{question.question}</td>
            <td className="answer">{question.mentor_answer || '—'}</td>
            <td className="answer">{question.mentee_answer || '—'}</td>
          </tr>
        ))}
      </tbody>
    </Sheet>
  )
}

function PersonSheet({
  person,
  onClose,
}: {
  person: PersonDetail | null
  onClose: () => void
}) {
  if (!person) return null

  const subtitle = `${person.side} · ${person.email || 'no email given'}`

  return (
    <Sheet title={person.name} subtitle={subtitle} onClose={onClose}>
      <thead>
        <tr>
          <th>Question</th>
          <th>Answer</th>
        </tr>
      </thead>
      <tbody>
        {person.questions.map((question) => (
          <tr key={question.row}>
            <td>{question.question}</td>
            <td className="answer">{question.answer}</td>
          </tr>
        ))}
      </tbody>
    </Sheet>
  )
}
