export function Flag({ reasons }: { reasons: string[] | undefined }) {
  if (!reasons) return null
  // The CSS draws the tooltip from this attribute on hover.
  return (
    <span className="flag" data-reasons={reasons.join('\n')}>
      &#128681;
    </span>
  )
}
