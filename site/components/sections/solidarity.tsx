import { Section, SectionHeading } from "@/components/layout/section"
import { home } from "@/content/home"

export function Solidarity() {
  return (
    <Section id="solidaritaet">
      <SectionHeading eyebrow={home.solidarity.eyebrow} title={home.solidarity.title}>
        {home.solidarity.lede}
      </SectionHeading>
      <ul className="mt-8 grid gap-4 md:grid-cols-2">
        {home.solidarity.items.map((item) => (
          <li key={item.title} className="border-l-8 border-hemp bg-surface-2 px-4 py-4">
            <h3 className="font-display text-2xl uppercase leading-none">{item.title}</h3>
            <p className="mt-2 leading-relaxed">{item.text}</p>
          </li>
        ))}
      </ul>
    </Section>
  )
}
