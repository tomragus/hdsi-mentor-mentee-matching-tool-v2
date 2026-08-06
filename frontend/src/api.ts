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
}

export type WaitlistEntry = {
  mentee_key: string
  mentee_name: string
  best_percentage: number | null
  best_mentor_name: string | null
}

export type BlockingPair = {
  mentor_key: string
  mentor_name: string
  mentee_key: string
  mentee_name: string
  percentage: number
  mentor_current_percentage: number | null
  mentee_current_percentage: number | null
}

export type AvoidBlock = {
  mentor_key: string
  mentor_name: string
  mentee_key: string
  mentee_name: string
  mentor_triggers: string[]
  mentee_triggers: string[]
}

export type ReviewFlag = {
  side: string
  respondent_key: string
  name: string
  reason: string
}

export type Cutoff = {
  row: number
  question: string
  percentiles: number[]
  upper: number
  lower: number
  pair_count: number
}

export type Report = {
  matches: Match[]
  waitlist: WaitlistEntry[]
  blocking_pairs: BlockingPair[]
  avoid_blocks: AvoidBlock[]
  review_flags: ReviewFlag[]
  cutoffs: Cutoff[]
  unfilled_slots: number
}

export type QuestionRow = {
  row: number
  weight: number
  mentor_question: string
  mentee_question: string | null
  mentor_answer: string
  mentee_answer: string
  points: number | null
  contribution: number | null
  maximum: number | null
  penalty: number
}

export type MatchDetail = {
  mentor: { key: string; name: string; email: string }
  mentee: { key: string; name: string; email: string }
  percentage: number | null
  raw: number | null
  maximum: number | null
  questions: QuestionRow[]
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

