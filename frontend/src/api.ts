// Typed wrappers over the backend. Every call returns a Result rather than
// throwing, so callers have to deal with the failure case, and so the upload
// error can carry the list of questions that could not be found.

export type MissingQuestion = { side: string; row: number; question: string }

export type Result<T> =
  | { ok: true; data: T }
  | { ok: false; message: string; missing?: MissingQuestion[] }

export type UploadSummary = {
  mentor_rows: number
  mentee_rows: number
  questions: number
}

export type Match = {
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

export type WaitlistEntry = {
  mentee_key: string
  mentee_name: string
}

export type UnmatchedMentor = {
  mentor_key: string
  mentor_name: string
  capacity: number
}

export type ReviewFlag = {
  respondent_key: string
  reason: string
}

export type Report = {
  matches: Match[]
  waitlist: WaitlistEntry[]
  unmatched_mentors: UnmatchedMentor[]
  review_flags: ReviewFlag[]
}

export type QuestionRow = {
  row: number
  question: string
  mentor_answer: string
  mentee_answer: string
}

export type MatchDetail = {
  mentor: { key: string; name: string }
  mentee: { key: string; name: string }
  percentage: number | null
  scored_questions: number
  questions: QuestionRow[]
}

export type PersonDetail = {
  key: string
  name: string
  side: string
  email: string
  capacity: number
  questions: { row: number; question: string | null; answer: string }[]
}

// One person can trip more than one check, so the reasons collect into a list
// and share a single flag.
export function flagReasons(report: Report): Map<string, string[]> {
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

export function uploadExports(
  mentorFile: File,
  menteeFile: File,
): Promise<Result<UploadSummary>> {
  const body = new FormData()
  body.append('mentor_file', mentorFile)
  body.append('mentee_file', menteeFile)
  return send<UploadSummary>('/api/upload', { method: 'POST', body })
}

export function runMatching(): Promise<Result<Report>> {
  return send<Report>('/api/run', { method: 'POST' })
}

export function openMatch(
  mentorKey: string,
  menteeKey: string,
): Promise<Result<MatchDetail>> {
  const path = `/api/match/${encodeURIComponent(mentorKey)}/${encodeURIComponent(menteeKey)}`
  return send<MatchDetail>(path)
}

export function openPerson(key: string): Promise<Result<PersonDetail>> {
  return send<PersonDetail>(`/api/person/${encodeURIComponent(key)}`)
}

