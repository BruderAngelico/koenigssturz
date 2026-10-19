export function PageHeader({
  eyebrow,
  title,
  lede,
}: {
  eyebrow: string
  title: string
  lede: string
}) {
  return (
    <header className="relative overflow-hidden border-b-2 border-line">
      <div className="pointer-events-none absolute -right-10 top-0 h-40 w-40 rotate-12 bg-resistance" aria-hidden />
      <div className="pointer-events-none absolute -right-6 top-24 h-24 w-40 rotate-12 bg-hemp" aria-hidden />
      <div className="relative mx-auto max-w-6xl px-4 py-12 sm:px-6 sm:py-16">
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-brand">{eyebrow}</p>
        <h1 className="mt-3 max-w-4xl font-display text-5xl uppercase leading-[0.86] sm:text-7xl">{title}</h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed">{lede}</p>
      </div>
    </header>
  )
}
