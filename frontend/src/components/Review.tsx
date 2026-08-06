import type { Report } from '../api'

type Props = {
  report: Report
}

export function Review({ report }: Props) {
  return (
    <>
      {report.avoid_blocks.length > 0 && (
        <section className="panel">
          <h2>
            Blocked by an avoid answer{' '}
            <span className="count">{report.avoid_blocks.length}</span>
          </h2>
          <p className="note">
            One side asked to avoid something the other works on, so these pairings
            were left out of the assignment.
          </p>
          <ul className="cards">
            {report.avoid_blocks.map((block) => (
              <li key={`${block.mentor_key}|${block.mentee_key}`}>
                <div>
                  <strong>{block.mentor_name}</strong> &times;{' '}
                  <strong>{block.mentee_name}</strong>
                  <p className="note">
                    {block.mentor_triggers.length > 0 && (
                      <>Mentor avoids: {block.mentor_triggers.join(', ')}. </>
                    )}
                    {block.mentee_triggers.length > 0 && (
                      <>Mentee avoids: {block.mentee_triggers.join(', ')}.</>
                    )}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {report.blocking_pairs.length > 0 && (
        <section className="panel">
          <h2>
            Would rather have each other{' '}
            <span className="count">{report.blocking_pairs.length}</span>
          </h2>
          <p className="note">
            The assignment is best for the cohort overall, but these two would each
            prefer the other over who they were given.
          </p>
          <ul className="cards">
            {report.blocking_pairs.map((pair) => (
              <li key={`${pair.mentor_key}|${pair.mentee_key}`}>
                <div>
                  <strong>{pair.mentor_name}</strong> &times;{' '}
                  <strong>{pair.mentee_name}</strong> would score {pair.percentage}%
                  <p className="note">
                    Mentor currently has{' '}
                    {pair.mentor_current_percentage === null
                      ? 'a free slot'
                      : `${pair.mentor_current_percentage}%`}
                    ; mentee currently has{' '}
                    {pair.mentee_current_percentage === null
                      ? 'no match'
                      : `${pair.mentee_current_percentage}%`}
                    .
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {report.review_flags.length > 0 && (
        <section className="panel">
          <h2>
            Needs a look <span className="count">{report.review_flags.length}</span>
          </h2>
          <ul className="cards">
            {report.review_flags.map((flag, index) => (
              <li key={`${flag.respondent_key}-${index}`}>
                <div>
                  <strong>{flag.name}</strong> <span className="tag">{flag.side}</span>
                  <p className="note">{flag.reason}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {report.cutoffs.length > 0 && (
        <details className="panel">
          <summary>
            Similarity cutoffs used this run{' '}
            <span className="count">{report.cutoffs.length}</span>
          </summary>
          <p className="note">
            Derived from this cohort, not fixed. A 10 means the top slice of pairs on
            that question in this run.
          </p>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Question</th>
                  <th>Percentiles</th>
                  <th>10 points at</th>
                  <th>5 points at</th>
                  <th>Pairs</th>
                </tr>
              </thead>
              <tbody>
                {report.cutoffs.map((cutoff) => (
                  <tr key={cutoff.row}>
                    <td>{cutoff.question}</td>
                    <td className="muted">{cutoff.percentiles.join(' / ')}</td>
                    <td className="score">{cutoff.upper}</td>
                    <td className="score">{cutoff.lower}</td>
                    <td className="muted">{cutoff.pair_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </>
  )
}
