import type { MatchDetail as Detail } from '../api'

type Props = {
  detail: Detail | null
  onClose: () => void
}

export function MatchDetail({ detail, onClose }: Props) {
  if (!detail) return null

  return (
    <div className="overlay" onClick={onClose}>
      <div className="sheet" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h2>
              {detail.mentor.name} &times; {detail.mentee.name}
            </h2>
            <p className="note">
              {detail.percentage}% &middot; {detail.raw} of {detail.maximum} points
            </p>
          </div>
          <button onClick={onClose}>Close</button>
        </header>

        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Question</th>
                <th>Mentor</th>
                <th>Mentee</th>
                <th>Points</th>
              </tr>
            </thead>
            <tbody>
              {detail.questions.map((question) => (
                <tr key={question.row}>
                  <td>
                    {question.mentor_question}
                    {question.weight > 0 && (
                      <span className="tag">weight {question.weight}</span>
                    )}
                  </td>
                  <td className="answer">{question.mentor_answer || '—'}</td>
                  <td className="answer">{question.mentee_answer || '—'}</td>
                  <td className="muted">
                    {/* Blank rather than zero when a question was not scored
                        for this pair, since the two mean different things. */}
                    {question.points === null
                      ? '—'
                      : `${question.contribution} / ${question.maximum}`}
                    {question.penalty > 0 && (
                      <span className="tag">−{question.penalty} write-in</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
