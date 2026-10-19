import { PageHeader } from "@/components/layout/page-header"
import { ExternalLink } from "@/components/layout/external-link"
import { ContactForm } from "@/components/forms/contact-form"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { home } from "@/content/home"
import { supportPage } from "@/content/pages"
import { pageMeta } from "@/lib/seo"

export const metadata = pageMeta(supportPage.title, supportPage.description)

export default function SupportPage() {
  return (
    <>
      <PageHeader eyebrow={supportPage.eyebrow} title={supportPage.title} lede={supportPage.lede} />
      <div className="mx-auto grid max-w-6xl gap-12 px-4 py-14 sm:px-6 lg:grid-cols-2">
        <div className="space-y-10">
          <section>
            <h2 className="font-display text-3xl uppercase leading-none">Awareness</h2>
            <p className="mt-4 border-l-4 border-resistance bg-surface-2 px-3 py-3 leading-relaxed">{supportPage.awareness}</p>
          </section>
          <section>
            <h2 className="font-display text-3xl uppercase leading-none">{supportPage.risksTitle}</h2>
            <ul className="mt-4 space-y-3">
              {supportPage.risks.map((item) => (
                <li key={item} className="border-l-4 border-hemp pl-3 leading-relaxed">
                  {item}
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h2 className="font-display text-3xl uppercase leading-none">{supportPage.helpTitle}</h2>
            <p className="mt-3 text-sm text-muted">{supportPage.helpNote}</p>
            <ul className="mt-4 space-y-3">
              {supportPage.help.map((item) => (
                <li key={item.name} className="border-2 border-line bg-surface px-3 py-3">
                  <ExternalLink href={item.href}>{item.name}</ExternalLink>
                  <p className="mt-1 text-sm leading-relaxed">{item.detail}</p>
                </li>
              ))}
            </ul>
          </section>
        </div>
        <section>
          <h2 className="font-display text-3xl uppercase leading-none">Kontakt</h2>
          <div className="mt-4">
            <ContactForm defaultTopic="Support" />
          </div>
        </section>
      </div>
      <div className="border-t-2 border-line">
        <div className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
          <h2 className="font-display text-3xl uppercase leading-none">Fragen</h2>
          <Accordion type="single" collapsible className="mt-6 grid gap-3">
            {home.faq.items.map((item, index) => (
              <AccordionItem key={item.q} value={`support-${index}`}>
                <AccordionTrigger>{item.q}</AccordionTrigger>
                <AccordionContent>{item.a}</AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </div>
      </div>
    </>
  )
}
