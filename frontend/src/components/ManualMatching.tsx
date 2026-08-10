import { useState } from 'react'
import { Flag } from './Flag'

export type PoolMentor = {
  key: string
  name: string
  capacity: number
  // How many places this mentor has already taken, counting manual pairs.
  used: number
}

export type PoolMentee = {
  key: string
  name: string
}

type Props = {
  mentors: PoolMentor[]
  mentees: PoolMentee[]
  reasons: Map<string, string[]>
  onPair: (mentorKey: string, menteeKey: string) => void
  onOpenPerson: (key: string) => void
}

export function ManualMatching({
  mentors,
  mentees,
  reasons,
  onPair,
  onOpenPerson,
}: Props) {
  // Which mentor card a dragged mentee is currently over, for the highlight.
  const [over, setOver] = useState<string | null>(null)

  return (
    <section className="panel">
      <h2>Manual matching</h2>
      <p className="note">
        Drag a mentee onto a mentor to pair them. Everyone without a match is
        here already; use &ldquo;&rarr; manual&rdquo; on a match to bring that
        pair back. Nothing here is scored or checked. Mentors leave this list
        once their places are filled.
      </p>

      <div className="columns">
        <div>
          <h3>
            Mentors <span className="count">{mentors.length}</span>
          </h3>
          {mentors.length === 0 && <p className="note">Nobody with a free place.</p>}
          {/* Only mentors with a place left reach this list, so every card
              here accepts a drop. A mentor who fills up simply leaves. */}
          {mentors.map((mentor) => (
            <div
              key={mentor.key}
              className={`card${over === mentor.key ? ' over' : ''}`}
              // Calling preventDefault is what marks an element as a valid
              // drop target.
              onDragOver={(event) => {
                event.preventDefault()
                setOver(mentor.key)
              }}
              onDragLeave={() => setOver(null)}
              onDrop={(event) => {
                event.preventDefault()
                setOver(null)
                const menteeKey = event.dataTransfer.getData('text/plain')
                if (menteeKey) onPair(mentor.key, menteeKey)
              }}
            >
              <div>
                {mentor.name}
                <Flag reasons={reasons.get(mentor.key)} />
              </div>
              <div className="actions">
                {mentor.capacity > 1 && (
                  <span className="tag">
                    {mentor.used}/{mentor.capacity}
                  </span>
                )}
                <button onClick={() => onOpenPerson(mentor.key)}>Open</button>
              </div>
            </div>
          ))}
        </div>

        <div>
          <h3>
            Mentees <span className="count">{mentees.length}</span>
          </h3>
          {mentees.length === 0 && <p className="note">Everyone has a match.</p>}
          {mentees.map((mentee) => (
            <div
              key={mentee.key}
              className="card draggable"
              draggable
              onDragStart={(event) =>
                event.dataTransfer.setData('text/plain', mentee.key)
              }
            >
              <div>
                {mentee.name}
                <Flag reasons={reasons.get(mentee.key)} />
              </div>
              <div className="actions">
                <button onClick={() => onOpenPerson(mentee.key)}>Open</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
