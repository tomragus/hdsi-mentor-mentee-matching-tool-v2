import type { Match } from '../api'
import { Flag } from './Flag'

type Props = {
  matches: Match[]
  reasons: Map<string, string[]>
  // How many places each mentor has filled, counting manual pairs.
  used: Map<string, number>
  onOpen: (mentorKey: string, menteeKey: string) => void
  onPull: (match: Match) => void
}

export function Leaderboard({ matches, reasons, used, onOpen, onPull }: Props) {
  return (
    <section className="panel">
      <h2>
        Matches <span className="count">{matches.length}</span>
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
            {matches.map((match) => (
              <tr key={`${match.mentor_key}|${match.mentee_key}`}>
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
  )
}
