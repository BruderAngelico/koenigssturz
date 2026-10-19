import { Section, SectionHeading } from "@/components/layout/section"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { home } from "@/content/home"

export function Faq() {
  return (
    <Section id="faq">
      <SectionHeading eyebrow={home.faq.eyebrow} title={home.faq.title} />
      <Accordion type="single" collapsible className="mt-8 grid gap-3">
        {home.faq.items.map((item, index) => (
          <AccordionItem key={item.q} value={`faq-${index}`}>
            <AccordionTrigger>{item.q}</AccordionTrigger>
            <AccordionContent>{item.a}</AccordionContent>
          </AccordionItem>
        ))}
      </Accordion>
    </Section>
  )
}
