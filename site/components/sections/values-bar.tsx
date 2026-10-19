import { HandHeart, Landmark, Megaphone, Shield } from "lucide-react"
import { HardCard, Reveal } from "@/components/motion/reveal"
import { Section, SectionHeading } from "@/components/layout/section"
import { home } from "@/content/home"

const icons = [HandHeart, Landmark, Shield, Megaphone]

export function ValuesBar() {
  return (
    <Section id="werte" tone="alt">
      <SectionHeading eyebrow={home.values.eyebrow} title={home.values.title} />
      <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {home.values.items.map((item, index) => {
          const Icon = icons[index] ?? HandHeart
          return (
            <li key={item.title}>
              <Reveal delay={index * 0.05} className="h-full">
                <HardCard className="flex h-full flex-col p-4">
                  <span className="inline-flex size-11 items-center justify-center border-2 border-line bg-hemp text-paper">
                    <Icon aria-hidden />
                  </span>
                  <h3 className="mt-4 font-display text-2xl uppercase leading-none">{item.title}</h3>
                  <p className="mt-3 leading-relaxed">{item.text}</p>
                </HardCard>
              </Reveal>
            </li>
          )
        })}
      </ul>
    </Section>
  )
}
