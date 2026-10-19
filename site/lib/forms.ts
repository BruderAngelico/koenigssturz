import { z } from "zod"

/** Contact and newsletter are UI-only. Nothing is stored or sent. */

export const contactSchema = z.object({
  name: z.string().min(2, "Name oder Spitzname fehlt."),
  email: z.string().min(1, "E-Mail fehlt.").email("Das ist keine gültige E-Mail."),
  topic: z.string().min(2, "Worum geht es?"),
  message: z.string().min(10, "Bitte etwas ausführlicher, mindestens 10 Zeichen."),
  notice: z.boolean().refine((value) => value, {
    message: "Bitte bestätigen, dass das Formular noch nichts sendet.",
  }),
})

export const newsletterSchema = z.object({
  email: z.string().min(1, "E-Mail fehlt.").email("Das ist keine gültige E-Mail."),
})

export type ContactValues = z.infer<typeof contactSchema>
export type NewsletterValues = z.infer<typeof newsletterSchema>
