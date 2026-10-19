import { LegalNote } from "@/components/layout/legal-note"
import { PageHeader } from "@/components/layout/page-header"
import { impressum } from "@/content/legal"
import { pageMeta } from "@/lib/seo"

export const metadata = pageMeta(impressum.title, impressum.description)

export default function ImpressumPage() {
  return (
    <>
      <PageHeader eyebrow="Rechtliches" title={impressum.title} lede={impressum.lede} />
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-14 sm:px-6">
        <LegalNote />
        <ul className="space-y-3">
          {impressum.fields.map((field) => (
            <li key={field} className="border-2 border-line bg-surface px-3 py-3 font-semibold">
              {field}
            </li>
          ))}
        </ul>
        <p className="leading-relaxed">{impressum.note}</p>
      </div>
    </>
  )
}
