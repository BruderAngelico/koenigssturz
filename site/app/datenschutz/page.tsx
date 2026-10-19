import { LegalNote } from "@/components/layout/legal-note"
import { PageHeader } from "@/components/layout/page-header"
import { privacy } from "@/content/legal"
import { pageMeta } from "@/lib/seo"

export const metadata = pageMeta(privacy.title, privacy.description)

export default function PrivacyPage() {
  return (
    <>
      <PageHeader eyebrow="Rechtliches" title={privacy.title} lede={privacy.lede} />
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-14 sm:px-6">
        <LegalNote />
        <ul className="space-y-3">
          {privacy.points.map((point) => (
            <li key={point} className="border-l-4 border-hemp pl-3 leading-relaxed">
              {point}
            </li>
          ))}
        </ul>
        <ul className="space-y-3">
          {privacy.fields.map((field) => (
            <li key={field} className="border-2 border-line bg-surface px-3 py-3 font-semibold">
              {field}
            </li>
          ))}
        </ul>
      </div>
    </>
  )
}
