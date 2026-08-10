// The two read-only overlays: one pairing side by side, or one person alone.
// Both are the same sheet with a different table in it.

import type { MatchDetail, PersonDetail } from '../api'

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

export function MatchSheet({
  detail,
  onClose,
}: {
  detail: MatchDetail | null
  onClose: () => void
}) {
  if (!detail) return null

  return (
    <Sheet
      title={`${detail.mentor.name} × ${detail.mentee.name}`}
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

export function PersonSheet({
  person,
  onClose,
}: {
  person: PersonDetail | null
  onClose: () => void
}) {
  if (!person) return null

  const subtitle =
    `${person.side} · ${person.email || 'no email given'}` +
    (person.side === 'mentor' ? ` · offers ${person.capacity}` : '')

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
