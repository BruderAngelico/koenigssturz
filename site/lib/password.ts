export type PasswordStrength = {
  score: 0 | 1 | 2 | 3
  label: string
}

/** Visual hint only. The zod schema is the actual rule. */
export function ratePassword(password: string): PasswordStrength {
  if (!password) return { score: 0, label: "noch leer" }

  let points = 0
  if (password.length >= 8) points += 1
  if (password.length >= 12) points += 1
  if (/[a-zäöüß]/.test(password) && /[A-ZÄÖÜ]/.test(password)) points += 1
  if (/\d/.test(password)) points += 1
  if (/[^\p{L}\d\s]/u.test(password)) points += 1

  const score: 1 | 2 | 3 = points >= 5 ? 3 : points >= 3 ? 2 : 1
  const label = score === 1 ? "schwach" : score === 2 ? "mittel" : "stark"
  return { score, label }
}
