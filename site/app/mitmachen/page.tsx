import { PageHeader } from "@/components/layout/page-header"
import { ContactForm } from "@/components/forms/contact-form"
import { joinPage } from "@/content/pages"
import { formCopy } from "@/content/pages"
import { pageMeta } from "@/lib/seo"

export const metadata = pageMeta(joinPage.title, joinPage.description)

export default function JoinPage() {
  return (
    <>
      <PageHeader eyebrow={joinPage.eyebrow} title={joinPage.title} lede={joinPage.lede} />
      <div className="mx-auto grid max-w-6xl gap-12 px-4 py-14 sm:px-6 lg:grid-cols-2">
        <section>
          <h2 className="font-display text-3xl uppercase leading-none">Einstieg</h2>
          <ol className="mt-4 space-y-3">
            {joinPage.steps.map((step, index) => (
              <li key={step} className="flex gap-3 border-2 border-line bg-surface p-3 shadow-hard-sm">
                <span className="font-display text-2xl leading-none text-brand">{String(index + 1).padStart(2, "0")}</span>
                <span className="leading-relaxed">{step}</span>
              </li>
            ))}
          </ol>
        </section>
        <section>
          <h2 className="font-display text-3xl uppercase leading-none">{formCopy.contactTitle}</h2>
          <div className="mt-4">
            <ContactForm defaultTopic="Mitmachen" />
          </div>
        </section>
      </div>
      <div className="border-t-2 border-line bg-surface-2">
        <div className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
          <h2 className="font-display text-3xl uppercase leading-none">Arbeitsgruppen</h2>
          <ul className="mt-6 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {joinPage.groups.map((group) => (
              <li key={group.title} className="border-2 border-line bg-surface p-4 shadow-hard">
                <h3 className="font-display text-2xl uppercase leading-none">{group.title}</h3>
                <p className="mt-3 leading-relaxed">{group.text}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  )
}
