import { useState } from 'react'
import { flagReasons, openMatch, openPerson, runMatching, uploadExports } from './api'
import type {
  Match,
  MatchDetail as Detail,
  MissingQuestion,
  PersonDetail as Person,
  Report,
  UploadSummary,
} from './api'
import { Leaderboard } from './components/Leaderboard'
import { MatchDetail } from './components/MatchDetail'
import { ManualMatching } from './components/ManualMatching'
import type { PoolMentee, PoolMentor } from './components/ManualMatching'
import { PersonDetail } from './components/PersonDetail'
import { Upload } from './components/Upload'

type Failure = { message: string; missing?: MissingQuestion[] }

const pairKey = (match: Match) => `${match.mentor_key}|${match.mentee_key}`

export default function App() {
  const [summary, setSummary] = useState<UploadSummary | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [person, setPerson] = useState<Person | null>(null)
  const [error, setError] = useState<Failure | null>(null)
  const [busy, setBusy] = useState(false)

  // The only manual state there is. Which solver matches were pulled apart,
  // and which pairs were made by hand. Everything the manual area shows is
  // derived from these two, so the pool can never disagree with the table.
  const [pulled, setPulled] = useState<Set<string>>(new Set())
  const [manualPairs, setManualPairs] = useState<Match[]>([])

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

  function resetManual() {
    setPulled(new Set())
    setManualPairs([])
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
        <Matching
          report={report}
          pulled={pulled}
          manualPairs={manualPairs}
          onPull={handlePull}
          onPair={async (mentorKey, menteeKey) => {
            // Every pair is scored, including ones the solver never used, so a
            // hand-made pair can show a real percentage.
            const result = await openMatch(mentorKey, menteeKey)
            if (!result.ok) return setError(result)
            setManualPairs((pairs) => [...pairs, toMatch(result.data)])
          }}
          onOpen={handleOpen}
          onOpenPerson={handleOpenPerson}
        />
      )}

      <MatchDetail detail={detail} onClose={() => setDetail(null)} />
      <PersonDetail person={person} onClose={() => setPerson(null)} />
    </main>
  )
}

/** Turn an opened pair into a row the Matches table can render. */
function toMatch(detail: Detail): Match {
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

type MatchingProps = {
  report: Report
  pulled: Set<string>
  manualPairs: Match[]
  onPull: (match: Match) => void
  onPair: (mentorKey: string, menteeKey: string) => void
  onOpen: (mentorKey: string, menteeKey: string) => void
  onOpenPerson: (key: string) => void
}

function Matching({
  report,
  pulled,
  manualPairs,
  onPull,
  onPair,
  onOpen,
  onOpenPerson,
}: MatchingProps) {
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
  const poolMentors: PoolMentor[] = [...mentors]
    .map(([key, mentor]) => ({
      key,
      name: mentor.name,
      capacity: mentor.capacity,
      used: used.get(key) ?? 0,
    }))
    .filter((mentor) => mentor.used < mentor.capacity)

  const poolMentees: PoolMentee[] = [...mentees]
    .filter(([key]) => !taken.has(key))
    .map(([key, name]) => ({ key, name }))

  const reasons = flagReasons(report)

  return (
    <>
      <Leaderboard
        matches={active}
        reasons={reasons}
        used={used}
        onOpen={onOpen}
        onPull={onPull}
      />
      <ManualMatching
        mentors={poolMentors}
        mentees={poolMentees}
        reasons={reasons}
        onPair={onPair}
        onOpenPerson={onOpenPerson}
      />
    </>
  )
}
