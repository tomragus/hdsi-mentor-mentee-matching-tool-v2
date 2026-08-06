import type { Report } from '../api'

type Props = {
  report: Report
  onOpen: (mentorKey: string, menteeKey: string) => void
}

export function Leaderboard({ report, onOpen }: Props) {
  return (
    <>
      <section className="panel">
        <h2>
          Matches <span className="count">{report.matches.length}</span>
        </h2>
        {report.unfilled_slots > 0 && (
          <p className="note">{report.unfilled_slots} mentor slots went unfilled.</p>
        )}

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
              {report.matches.map((match) => (
                <tr key={`${match.mentor_key}|${match.mentee_key}`}>
                  <td className="score">{match.percentage}%</td>
                  <td>{match.mentor_name}</td>
                  <td>{match.mentee_name}</td>
                  {/* How many questions the score rests on, so a match built
                      on very little is visible rather than hidden. */}
                  <td className="muted">{match.scored_questions}</td>
                  <td className="actions">
                    <button onClick={() => onOpen(match.mentor_key, match.mentee_key)}>
                      Open
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {report.waitlist.length > 0 && (
        <section className="panel">
          <h2>
            Waitlist <span className="count">{report.waitlist.length}</span>
          </h2>
          <p className="note">Mentees with no slot, best prospects first.</p>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Best available</th>
                  <th>Mentee</th>
                  <th>Would pair with</th>
                </tr>
              </thead>
              <tbody>
                {report.waitlist.map((entry) => (
                  <tr key={entry.mentee_key}>
                    <td className="score">
                      {entry.best_percentage === null ? '—' : `${entry.best_percentage}%`}
                    </td>
                    <td>{entry.mentee_name}</td>
                    <td className="muted">{entry.best_mentor_name ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  )
}
