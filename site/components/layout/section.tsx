import { cn } from "@/lib/utils"

export function Section({
  id,
  children,
  className,
  tone = "plain",
}: {
  id?: string
  children: React.ReactNode
  className?: string
  tone?: "plain" | "alt" | "ink"
}) {
  return (
    <section
      id={id}
      className={cn(
        "border-b-2 border-line py-14 sm:py-20",
        tone === "alt" && "bg-surface-2",
        tone === "ink" && "bg-charcoal text-paper",
        className,
      )}
    >
      <div className="mx-auto w-full max-w-6xl px-4 sm:px-6">{children}</div>
    </section>
  )
}

export function SectionHeading({
  eyebrow,
  title,
  children,
}: {
  eyebrow?: string
  title: string
  children?: React.ReactNode
}) {
  return (
    <div className="max-w-3xl">
      {eyebrow ? (
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-brand">{eyebrow}</p>
      ) : null}
      <h2 className="mt-2 font-display text-4xl uppercase leading-[0.9] sm:text-5xl">{title}</h2>
      {children ? <p className="mt-4 text-lg leading-relaxed">{children}</p> : null}
    </div>
  )
}
