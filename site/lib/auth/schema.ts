import { z } from "zod"

/**
 * Shared form contracts. A future Supabase adapter can reuse these types
 * without importing React.
 */

export const loginSchema = z.object({
  email: z.string().min(1, "E-Mail fehlt.").email("Das ist keine gültige E-Mail."),
  password: z.string().min(1, "Passwort fehlt."),
})

export const registerSchema = z.object({
  name: z
    .string()
    .min(2, "Name oder Spitzname braucht mindestens 2 Zeichen.")
    .max(80, "Bitte kürzer als 80 Zeichen."),
  email: z.string().min(1, "E-Mail fehlt.").email("Das ist keine gültige E-Mail."),
  password: z
    .string()
    .min(8, "Mindestens 8 Zeichen.")
    .regex(/[A-Za-zÄÖÜäöüß]/, "Mindestens ein Buchstabe.")
    .regex(/\d/, "Mindestens eine Zahl."),
  age: z.boolean().refine((value) => value, {
    message: "Bitte bestätige, dass du mindestens 18 Jahre alt bist.",
  }),
  rules: z.boolean().refine((value) => value, {
    message: "Bitte die Regeln und den Safer-Space-Kodex akzeptieren.",
  }),
})

export const resetSchema = z.object({
  email: z.string().min(1, "E-Mail fehlt.").email("Das ist keine gültige E-Mail."),
})

export type LoginValues = z.infer<typeof loginSchema>
export type RegisterValues = z.infer<typeof registerSchema>
export type ResetValues = z.infer<typeof resetSchema>
