import { LegalNote } from "@/components/layout/legal-note"
import { PageHeader } from "@/components/layout/page-header"
import { a11y } from "@/content/legal"
import { pageMeta } from "@/lib/seo"

export const metadata = pageMeta(a11y.title, a11y.description)

export default function AccessibilityPage() {
  return (
    <>
      <PageHeader eyebrow="Zugang" title={a11y.title} lede={a11y.lede} />
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-14 sm:px-6">
        <LegalNote />
        <ul className="space-y-3">
          {a11y.goals.map((goal) => (
            <li key={goal} className="border-l-4 border-hemp pl-3 leading-relaxed">
              {goal}
            </li>
          ))}
        </ul>
        <ul className="space-y-3">
          {a11y.fields.map((field) => (
            <li key={field} className="border-2 border-line bg-surface px-3 py-3 font-semibold">
              {field}
            </li>
          ))}
        </ul>
      </div>
    </>
  )
}
