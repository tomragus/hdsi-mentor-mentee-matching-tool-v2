import type { PersonDetail as Detail } from '../api'

type Props = {
  person: Detail | null
  onClose: () => void
}

export function PersonDetail({ person, onClose }: Props) {
  if (!person) return null

  return (
    <div className="overlay" onClick={onClose}>
      <div className="sheet" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h2>{person.name}</h2>
            <p className="note">
              {person.side} &middot; {person.email || 'no email given'}
              {person.side === 'mentor' && ` · offers ${person.capacity}`}
            </p>
          </div>
          <button onClick={onClose}>Close</button>
        </header>

        <div className="scroll">
          <table>
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
          </table>
        </div>
      </div>
    </div>
  )
}
