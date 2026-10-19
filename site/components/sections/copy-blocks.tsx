export function CopyBlocks({
  blocks,
}: {
  blocks: ReadonlyArray<{
    title: string
    paragraphs: readonly string[]
    todo?: string
  }>
}) {
  return (
    <div className="mx-auto max-w-6xl space-y-12 px-4 py-14 sm:px-6">
      {blocks.map((block) => (
        <section key={block.title} className="grid gap-4 border-t-2 border-line pt-8 lg:grid-cols-12">
          <h2 className="font-display text-3xl uppercase leading-none lg:col-span-4">{block.title}</h2>
          <div className="space-y-4 lg:col-span-8">
            {block.paragraphs.map((paragraph) => (
              <p key={paragraph} className="max-w-prose leading-relaxed">
                {paragraph}
              </p>
            ))}
            {block.todo ? (
              <p className="border-l-4 border-resistance bg-surface-2 px-3 py-2 text-sm font-semibold">{block.todo}</p>
            ) : null}
          </div>
        </section>
      ))}
    </div>
  )
}
