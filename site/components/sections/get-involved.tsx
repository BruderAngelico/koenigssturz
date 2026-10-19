import { BookOpen, CalendarDays, ShieldCheck, Users } from "lucide-react"
import Link from "next/link"
import { Section, SectionHeading } from "@/components/layout/section"
import { Button } from "@/components/ui/button"
import { home } from "@/content/home"

const icons = [Users, BookOpen, CalendarDays, ShieldCheck]

export function GetInvolved() {
  return (
    <Section id="mitmachen">
      <SectionHeading eyebrow={home.join.eyebrow} title={home.join.title} />
      <ul className="mt-8 grid gap-4 sm:grid-cols-2">
        {home.join.items.map((item, index) => {
          const Icon = icons[index] ?? Users
          return (
            <li key={item.title} className="flex gap-4 border-2 border-line bg-surface p-4 shadow-hard-sm">
              <span className="inline-flex size-11 shrink-0 items-center justify-center bg-resistance text-paper">
                <Icon aria-hidden />
              </span>
              <div>
                <h3 className="font-display text-2xl uppercase leading-none">{item.title}</h3>
                <p className="mt-2 leading-relaxed">{item.text}</p>
              </div>
            </li>
          )
        })}
      </ul>
      <div className="mt-6">
        <Button asChild>
          <Link href="/mitmachen">{home.join.link}</Link>
        </Button>
      </div>
    </Section>
  )
}
