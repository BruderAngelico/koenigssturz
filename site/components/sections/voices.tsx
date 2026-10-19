import { Section, SectionHeading } from "@/components/layout/section"
import { home } from "@/content/home"

export function Voices() {
  return (
    <Section id="stimmen" tone="alt">
      <SectionHeading eyebrow={home.voices.eyebrow} title={home.voices.title} />
      <p className="mt-4 inline-block bg-signal px-2 py-1 text-sm font-bold uppercase tracking-wide text-charcoal">
        {home.voices.note}
      </p>
      <ul className="mt-6 grid gap-4 lg:grid-cols-3">
        {home.voices.items.map((item) => (
          <li key={item.quote} className="border-2 border-line bg-surface p-5 shadow-hard">
            <p className="text-sm font-bold uppercase tracking-wide text-brand">Platzhalterstimme</p>
            <blockquote className="mt-3 text-lg leading-relaxed">„{item.quote}“</blockquote>
            <p className="mt-4 text-sm font-semibold text-muted">{item.meta}</p>
          </li>
        ))}
      </ul>
    </Section>
  )
}
