import { useState } from 'react'

// The whole client: the shapes the backend returns, typed wrappers over its four
// endpoints, and every component.
//
// Each fetch wrapper returns a Result rather than throwing, so callers have to
// deal with the failure case, and so the upload error can carry the list of
// questions that could not be found.

// --- what the backend returns ------------------------------------------------

type MissingQuestion = { side: string; row: number; question: string }

type Result<T> =
  | { ok: true; data: T }
  | { ok: false; message: string; missing?: MissingQuestion[] }

type Match = {
  mentor_key: string
  mentor_name: string
  mentee_key: string
  mentee_name: string
  percentage: number
  mentor_capacity: number
  manual?: true // set on pairs made by hand, which the backend never sends
}

// The backend always sends addresses. A hand-made row is built from an opened
// pair, which carries none, and looks them up in the roster instead.
type ReportMatch = Match & { mentor_email: string; mentee_email: string }

type Report = {
  matches: ReportMatch[]
  waitlist: { mentee_key: string; mentee_name: string; mentee_email: string }[]
  unmatched_mentors: {
    mentor_key: string
    mentor_name: string
    email: string
    capacity: number
  }[]
  review_flags: { respondent_key: string; reason: string }[]
}

type QuestionRow = { row: number; question: string; mentor_answer: string; mentee_answer: string }

type MatchDetail = {
  mentor: { key: string; name: string }
  mentee: { key: string; name: string }
  percentage: number | null
  questions: QuestionRow[]
}

type PersonDetail = {
  key: string
  name: string
  side: string
  email: string
  questions: { row: number; question: string | null; answer: string }[]
}

const OFFLINE = 'Could not reach the backend. Start it with: uv run uvicorn app.main:app'

// The server sheds its uploaded cohort whenever it restarts, which it does after
// a stretch of no use. Answering with the bare detail ("Upload both exports
// first.") does not explain why a page that worked a minute ago has stopped.
const ASLEEP =
  'The server went to sleep while this page was open, so the uploaded files are ' +
  'gone. Upload them again and press Match.'

async function send<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  try {
    const response = await fetch(path, init)
    const body = await response.json().catch(() => null)
    if (response.ok) return { ok: true, data: body as T }

    // The dev server answers with a gateway error, and no JSON, when the backend
    // is not listening. That is the common case, so it gets the instruction
    // rather than a status code.
    if (body === null && response.status >= 500) return { ok: false, message: OFFLINE }

    // 409 is the one thing the server says when it has no cohort loaded.
    if (response.status === 409) return { ok: false, message: ASLEEP }

    // The upload error is an object naming each unresolved question; every other
    // error is a plain string.
    const detail = body?.detail
    if (detail && typeof detail === 'object') {
      return { ok: false, message: detail.message, missing: detail.missing }
    }
    return { ok: false, message: detail ?? `Request failed (${response.status})` }
  } catch {
    return { ok: false, message: OFFLINE }
  }
}

// `swapped` says the two files were the wrong way round and were read the other
// way instead, which is worth telling somebody about.
function uploadExports(mentor: File, mentee: File): Promise<Result<{ swapped: boolean }>> {
  const body = new FormData()
  body.append('mentor_file', mentor)
  body.append('mentee_file', mentee)
  return send('/api/upload', { method: 'POST', body })
}

const runMatching = () => send<Report>('/api/run', { method: 'POST' })

const jsonPost = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// These two read rather than change anything, so they would ordinarily be GETs.
// A person is identified by their email address, and the host writes every
// request path to its own log -- so a GET would file the cohort's addresses into
// logging as a side effect of the coordinator clicking through matches. A body
// is not logged.
const openMatch = (mentorKey: string, menteeKey: string) =>
  send<MatchDetail>('/api/match', jsonPost({ mentor_key: mentorKey, mentee_key: menteeKey }))

const openPerson = (key: string) => send<PersonDetail>('/api/person', jsonPost({ key }))

const clearSession = () => send<{ status: string }>('/api/clear', { method: 'POST' })

// --- the shell ---------------------------------------------------------------

type Failure = { message: string; missing?: MissingQuestion[] }

const SWAPPED_NOTICE =
  'Next time, make sure you put the files in the right order! Processing anyway...'

const STALE_BACKEND_NOTICE =
  'The server is running an older version of itself, so the CSV export would have ' +
  'no email addresses in it. Restart it (uv run uvicorn app.main:app) and match again.'

/** Whether the server answered without address fields at all.
 *
 *  A response is cast rather than validated, so a server predating the export
 *  really can answer without them. Worth catching, because it reaches the
 *  coordinator as a column of blank cells that looks exactly like a cohort where
 *  nobody gave an address — an empty string means "none on file", which is a
 *  different thing and perfectly normal.
 */
const addressesMissing = (report: Report) =>
  report.matches.some((match) => (match as Partial<ReportMatch>).mentor_email === undefined)

const pairKey = (match: Match) => `${match.mentor_key}|${match.mentee_key}`

/** Turn an opened pair into a row the matches table can render. */
const toMatch = (detail: MatchDetail): Match => ({
  mentor_key: detail.mentor.key,
  mentor_name: detail.mentor.name,
  mentee_key: detail.mentee.key,
  mentee_name: detail.mentee.name,
  percentage: detail.percentage ?? 0,
  mentor_capacity: 0, // filled in from the roster in Results
  manual: true,
})

// --- the CSV export ----------------------------------------------------------

const EXPORT_FILENAME = 'mentor-mentee-matches.csv'
const EXPORT_COLUMNS = ['Mentor name', 'Mentor email', 'Mentee name', 'Mentee email', 'Status']

// Excel decides a cell is a formula from its first character, after the quoting
// has already been stripped -- so a student who typed =HYPERLINK(...) as their
// name would have it run when the coordinator opens the export. A leading
// apostrophe is Excel's own "this is text" marker. Real names and addresses
// never start with these, so in practice this never fires.
const FORMULA_START = /^[=+\-@\t\r]/

/** Every cell is quoted: a name can hold a comma, and "Smith, Jr." would
 *  otherwise split itself across two columns. */
const csvCell = (value: string) => {
  const safe = FORMULA_START.test(value) ? `'${value}` : value
  return `"${safe.replace(/"/g, '""')}"`
}

// A byte-order mark is what makes Excel read the file as UTF-8; without it an
// accented name arrives mangled. Written as an escape, since the character itself
// is invisible in a source file.
const BOM = '\uFEFF'

function downloadCsv(rows: string[][]) {
  const body = rows.map((row) => row.map(csvCell).join(',')).join('\r\n')
  // CRLF line endings, which is what spreadsheet software expects of a CSV.
  const blob = new Blob([`${BOM}${body}\r\n`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = EXPORT_FILENAME
  link.click()
  URL.revokeObjectURL(url)
}

// --- the "How it Works" guide ------------------------------------------------

// Written for the coordinator running the program, not for a developer. Kept as
// plain text here rather than read from a file, so the card has nothing to load
// and cannot fail to open.

const GUIDE_TITLE = 'How matches are made:'

const GUIDE_PARAGRAPHS = [
  'When you upload mentor and mentee responses, every mentor gets compared with ' +
    'every mentee, and each possible pair gets assigned a "compatability score" ' +
    '(between 0% and 100%) based on how compatable the algorithm finds them. Things ' +
    'like whether both can commit to meeting regularly, how they prefer to stay in ' +
    "touch, and whether the mentee wants help in the mentor's field weigh heavily, " +
    'while things like specific tools, hobbies, and how someone describe their ' +
    'style, weigh a bit less.',
  'Sometimes, an ideal pair may be broken up for the overall good of the group. ' +
    'For example, mentee A might have 70% compatability with mentor C and 60% ' +
    'compatability with mentor D, while mentee B has 65% with mentor C and 10% with ' +
    'mentor D. Even though A and C have the better match, A will get matched with D ' +
    'because otherwise, B has no good match at all!',
  'Algorithms are never perfect, but hopefully this makes the decision-making ' +
    'process a little bit easier. Any matches you dislike can be easily overwritten ' +
    'in Manual Review. Once you feel comfortable with the matching, click "Export ' +
    'Matches" to get a list of matched names and emails!',
]

function GuideSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null
  return (
    <Sheet title={GUIDE_TITLE} onClose={onClose}>
      <div className="guide">
        {GUIDE_PARAGRAPHS.map((paragraph) => (
          <p key={paragraph.slice(0, 32)}>{paragraph}</p>
        ))}
      </div>
    </Sheet>
  )
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

export default function App() {
  const [report, setReport] = useState<Report | null>(null)
  const [detail, setDetail] = useState<MatchDetail | null>(null)
  const [person, setPerson] = useState<PersonDetail | null>(null)
  const [error, setError] = useState<Failure | null>(null)
  // Something the upload put right by itself. Not an error, since the run went
  // ahead, but not silent either.
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // The guide reads the same before and after a run, so it is not cleared with
  // the rest of the page.
  const [guideOpen, setGuideOpen] = useState(false)

  // The only manual state there is: which solver matches were pulled apart, and
  // which pairs were made by hand. Everything the manual area shows derives from
  // these two, so the pool can never disagree with the table.
  const [pulled, setPulled] = useState<Set<string>>(new Set())
  const [manualPairs, setManualPairs] = useState<Match[]>([])

  // Undo keeps whole snapshots rather than a list of actions to reverse. They are
  // small, and it means any future action is undoable without writing its inverse.
  const [history, setHistory] = useState<{ pulled: Set<string>; pairs: Match[] }[]>([])

  /** Record the current state, so the action about to happen can be undone. */
  const remember = () =>
    setHistory((past) => [...past, { pulled: new Set(pulled), pairs: [...manualPairs] }])

  function resetManual() {
    setPulled(new Set())
    setManualPairs([])
    setHistory([])
  }

  function handleUndo() {
    const previous = history[history.length - 1]
    if (!previous) return
    setPulled(previous.pulled)
    setManualPairs(previous.pairs)
    setHistory((past) => past.slice(0, -1))
  }

  // Puts the page back to how it looks on a fresh load, and drops the server's
  // copy of the cohort with it -- pressing Match always uploads again, so that
  // copy was never reachable from here, and leaving it only widens the stretch
  // in which a roomful of names and addresses sits in memory.
  //
  // Nothing is awaited or reported: the page has already cleared, which is what
  // the button says it does, and there is no useful second thing to tell
  // somebody if the request behind it does not land.
  function handleClear() {
    setReport(null)
    setDetail(null)
    setPerson(null)
    setError(null)
    setNotice(null)
    resetManual()
    void clearSession()
  }

  // Loading a cohort and solving it are one action. The solve is deterministic,
  // so there was never a reason to do one without the other.
  async function handleMatch(mentorFile: File, menteeFile: File) {
    setBusy(true)
    setError(null)
    setNotice(null)
    setReport(null) // a new cohort invalidates whatever is on screen
    resetManual()

    const uploaded = await uploadExports(mentorFile, menteeFile)
    if (!uploaded.ok) {
      setError(uploaded)
      setBusy(false)
      return
    }
    if (uploaded.data.swapped) setNotice(SWAPPED_NOTICE)

    const result = await runMatching()
    if (result.ok) {
      setReport(result.data)
      if (addressesMissing(result.data)) {
        // Kept alongside any notice the upload already raised rather than
        // replacing it, since both are worth reading.
        setNotice((current) => [current, STALE_BACKEND_NOTICE].filter(Boolean).join(' '))
      }
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
    remember()
    if (match.manual) {
      setManualPairs((pairs) => pairs.filter((p) => pairKey(p) !== pairKey(match)))
      return
    }
    setPulled((keys) => new Set(keys).add(pairKey(match)))
  }

  async function handlePair(mentorKey: string, menteeKey: string) {
    // Every pair is scored, including ones the solver never used, so a hand-made
    // pair can show a real percentage.
    const result = await openMatch(mentorKey, menteeKey)
    // Recorded only once the pair is certain, so a failed lookup leaves nothing
    // to undo.
    if (!result.ok) return setError(result)
    remember()
    setManualPairs((pairs) => [...pairs, toMatch(result.data)])
  }

  return (
    <main>
      <header className="masthead">
        <h1>HSDSC Mentor/Mentee Matchmaker 📇</h1>
        <button onClick={() => setGuideOpen(true)}>How it Works</button>
      </header>

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
        notice={notice}
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
      <GuideSheet open={guideOpen} onClose={() => setGuideOpen(false)} />
    </main>
  )
}

// --- the uploads -------------------------------------------------------------

type UploadProps = {
  busy: boolean
  error: Failure | null
  notice: string | null
  onMatch: (mentorFile: File, menteeFile: File) => void
  onClear: () => void
}

function Upload({ busy, error, notice, onMatch, onClear }: UploadProps) {
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

  const inputs = [
    ['Mentor questionnaire', setMentorFile],
    ['Mentee questionnaire', setMenteeFile],
  ] as const

  return (
    <section className="panel">
      <h2>Upload the two .csv files</h2>

      <form key={formKey} className="uploads" onSubmit={submit}>
        {inputs.map(([label, set]) => (
          <label key={label}>
            <strong>{label}</strong>
            <input
              type="file"
              accept=".csv,.xlsx,.xls"
              onChange={(event) => set(event.target.files?.[0] ?? null)}
            />
          </label>
        ))}
        <button type="submit" disabled={!mentorFile || !menteeFile || busy}>
          {busy ? 'Matching…' : 'Match'}
        </button>
        {/* type="button" so it does not submit the form it sits in. */}
        <button type="button" onClick={clear} disabled={busy}>
          Clear
        </button>
      </form>

      {notice && <div className="notice">{notice}</div>}

      {error && (
        <div className="error">
          <p>{error.message}</p>
          {/* Naming the questions is the point: a coordinator has to know which
              one to fix in the form. */}
          {error.missing && (
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

function Flag({ reasons }: { reasons: string[] | undefined }) {
  // The CSS draws the tooltip from this attribute on hover.
  if (!reasons) return null
  return (
    <span className="flag" data-reasons={reasons.join('\n')}>
      &#9873;&#65038;
    </span>
  )
}

type WhoProps = { name: string; used?: number; capacity?: number; reasons?: string[] }

/** Name, places, flag -- the same three in the same order everywhere. */
function Who({ name, used, capacity, reasons }: WhoProps) {
  return (
    <>
      {name}
      {/* Only mentors offering more than one place, since a "1/1" on every other
          row is noise. */}
      {capacity !== undefined && capacity > 1 && (
        <span className="tag">
          {used ?? 0}/{capacity}
        </span>
      )}
      <Flag reasons={reasons} />
    </>
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

function Results(props: ResultsProps) {
  const { report, pulled, manualPairs, canUndo, onUndo, onPull, onPair, onOpen } = props
  // Which mentor card a dragged mentee is currently over, for the highlight, and
  // the card being carried, taken out of the list so a drag reads as picking the
  // card up rather than copying it.
  const [over, setOver] = useState<string | null>(null)
  const [lifted, setLifted] = useState<string | null>(null)

  // Every mentor reaches the pool from one of these two lists, and only these
  // carry their capacity.
  const mentors = new Map<string, { name: string; capacity: number }>()
  for (const m of report.matches) {
    mentors.set(m.mentor_key, { name: m.mentor_name, capacity: m.mentor_capacity })
  }
  for (const m of report.unmatched_mentors) {
    mentors.set(m.mentor_key, { name: m.mentor_name, capacity: m.capacity })
  }

  const mentees = new Map<string, string>()
  for (const m of report.matches) mentees.set(m.mentee_key, m.mentee_name)
  for (const entry of report.waitlist) mentees.set(entry.mentee_key, entry.mentee_name)

  // Addresses for the CSV export, gathered from all three lists so a hand-made
  // pair can find one for whoever it joined together.
  const emails = new Map<string, string>()
  for (const m of report.matches) {
    emails.set(m.mentor_key, m.mentor_email)
    emails.set(m.mentee_key, m.mentee_email)
  }
  for (const m of report.unmatched_mentors) emails.set(m.mentor_key, m.email)
  for (const entry of report.waitlist) emails.set(entry.mentee_key, entry.mentee_email)

  const active = [
    ...report.matches.filter((match) => !pulled.has(pairKey(match))),
    ...manualPairs.map((match) => ({
      ...match,
      mentor_capacity: mentors.get(match.mentor_key)?.capacity ?? 1,
    })),
  ].sort((a, b) => b.percentage - a.percentage)

  const used = new Map<string, number>()
  for (const match of active) used.set(match.mentor_key, (used.get(match.mentor_key) ?? 0) + 1)
  const taken = new Set(active.map((match) => match.mentee_key))

  // A mentor with a place left belongs in the pool even while matched to somebody
  // else, so a capacity-2 mentor can take a second mentee by hand.
  const poolMentors = [...mentors]
    .map(([key, mentor]) => ({ key, ...mentor, used: used.get(key) ?? 0 }))
    .filter((mentor) => mentor.used < mentor.capacity)

  const poolMentees = [...mentees]
    .filter(([key]) => !taken.has(key))
    .map(([key, name]) => ({ key, name }))

  const reasons = flagReasons(report)

  /** The final pairs, then everyone left over, as one table.
   *
   *  Unmatched rows come from the mentee pool rather than the waitlist, because
   *  the pool is what is true after manual edits -- a mentee pulled out of a
   *  solver match and never re-paired belongs here, and the waitlist knows
   *  nothing about that.
   */
  function handleExport() {
    downloadCsv([
      EXPORT_COLUMNS,
      ...active.map((match) => [
        match.mentor_name,
        emails.get(match.mentor_key) ?? '',
        match.mentee_name,
        emails.get(match.mentee_key) ?? '',
        'Matched',
      ]),
      ...poolMentees.map((mentee) => [
        '',
        '',
        mentee.name,
        emails.get(mentee.key) ?? '',
        'Unmatched',
      ]),
    ])
  }

  return (
    <>
      <section className="panel">
        <header>
          <h2>
            Matches <span className="count">{active.length}</span>
          </h2>
          <div className="actions">
            {/* Reverses the last pull or hand-made pair, one step at a time. */}
            <button onClick={onUndo} disabled={!canUndo}>
              Undo
            </button>
            <button onClick={handleExport} disabled={!active.length && !poolMentees.length}>
              Export Matches
            </button>
          </div>
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
                    {match.manual && <span className="tag manual">manual</span>}
                  </td>
                  <td>
                    <Who
                      name={match.mentor_name}
                      used={used.get(match.mentor_key)}
                      capacity={match.mentor_capacity}
                      reasons={reasons.get(match.mentor_key)}
                    />
                  </td>
                  <td>
                    <Who name={match.mentee_name} reasons={reasons.get(match.mentee_key)} />
                  </td>
                  <td className="actions">
                    <button onClick={() => onOpen(match.mentor_key, match.mentee_key)}>Open</button>
                    {/* Breaks the pair and sends both people to the manual area. */}
                    <button onClick={() => onPull(match)}>Manual Review</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* The whole panel takes a drop, though only mentor cards do anything with
          one. A drag let go over something the browser refuses is animated back to
          where it started, and dragend does not arrive until that animation has
          played -- which is the wait before the card comes back. Accepting it
          anywhere in here ends the drag on release. */}
      <section
        className="panel"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault()
          setOver(null)
          setLifted(null)
        }}
      >
        <h2>Manual review</h2>

        <div className="columns">
          <div>
            <h3>
              Mentors <span className="count">{poolMentors.length}</span>
            </h3>
            {poolMentors.length === 0 && <p className="note">Nobody with a free place.</p>}
            {/* Only mentors with a place left reach this list, so every card here
                accepts a drop. A mentor who fills up simply leaves. */}
            {poolMentors.map((mentor) => (
              <div
                key={mentor.key}
                className={`card${over === mentor.key ? ' over' : ''}`}
                // Calling preventDefault is what marks an element as a valid drop target.
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
                <div>
                  <Who
                    name={mentor.name}
                    used={mentor.used}
                    capacity={mentor.capacity}
                    reasons={reasons.get(mentor.key)}
                  />
                </div>
                <div className="actions">
                  <button onClick={() => props.onOpenPerson(mentor.key)}>Open</button>
                </div>
              </div>
            ))}
          </div>

          <div>
            <h3>
              Mentees <span className="count">{poolMentees.length}</span>
            </h3>
            {poolMentees.length === 0 && <p className="note">Everyone has a match.</p>}
            {poolMentees.map((mentee) => (
              <div
                key={mentee.key}
                className={`card draggable${lifted === mentee.key ? ' lifted' : ''}`}
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.setData('text/plain', mentee.key)
                  // Hidden on the next frame rather than straight away: the browser
                  // takes its picture of the card for the drag image first, and
                  // hiding it now would leave nothing to picture.
                  requestAnimationFrame(() => setLifted(mentee.key))
                }}
                onDragEnd={() => setLifted(null)}
              >
                <div>
                  <Who name={mentee.name} reasons={reasons.get(mentee.key)} />
                </div>
                <div className="actions">
                  <button onClick={() => props.onOpenPerson(mentee.key)}>Open</button>
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

type SheetProps = {
  title: string
  // The guide has nothing to put here; both match sheets do.
  subtitle?: string
  onClose: () => void
  children: React.ReactNode
}

/** The overlay card. Its body is whatever the caller passes: the two match
 *  sheets hand it a scrolling table, the guide hands it prose. */
function Sheet({ title, subtitle, onClose, children }: SheetProps) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className="sheet" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <p className="note">{subtitle}</p>}
          </div>
          <button onClick={onClose}>Close</button>
        </header>
        {children}
      </div>
    </div>
  )
}

/** A scrolling table, which is what both match sheets put in a Sheet. */
const SheetTable = ({ children }: { children: React.ReactNode }) => (
  <div className="scroll">
    <table>{children}</table>
  </div>
)

function MatchSheet({ detail, onClose }: { detail: MatchDetail | null; onClose: () => void }) {
  if (!detail) return null
  return (
    <Sheet
      title={`${detail.mentor.name} & ${detail.mentee.name}`}
      subtitle={`${detail.percentage}% match`}
      onClose={onClose}
    >
      <SheetTable>
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
      </SheetTable>
    </Sheet>
  )
}

function PersonSheet({ person, onClose }: { person: PersonDetail | null; onClose: () => void }) {
  if (!person) return null
  return (
    <Sheet
      title={person.name}
      subtitle={`${person.side} · ${person.email || 'no email given'}`}
      onClose={onClose}
    >
      <SheetTable>
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
      </SheetTable>
    </Sheet>
  )
}
