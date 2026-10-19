import { legalBanner } from "@/content/legal"

export function LegalNote() {
  return (
    <aside className="border-2 border-charcoal bg-signal px-4 py-3 text-charcoal shadow-hard-sm" role="note">
      <p className="font-semibold">{legalBanner.title}</p>
      <p className="mt-1 text-sm leading-relaxed">{legalBanner.text}</p>
    </aside>
  )
}
