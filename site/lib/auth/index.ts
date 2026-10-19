import type { AuthResult, SignInInput, SignUpInput } from "@/lib/auth/types"

/**
 * Auth boundary. UI only — no network, no secrets, no cookies.
 *
 * Replace these functions with Supabase Auth later, for example:
 *   signIn  -> supabase.auth.signInWithPassword
 *   signUp  -> supabase.auth.signUp
 *   requestPasswordReset -> supabase.auth.resetPasswordForEmail
 *   signOut -> supabase.auth.signOut
 *
 * Read public config from environment variables in a server module
 * when a backend exists. Do not put keys in this file.
 */

const NOT_CONFIGURED =
  "Noch nicht angebunden. Es wurde nichts gesendet und kein Konto angelegt."

function wait(ms = 400) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export async function signIn(input: SignInInput): Promise<AuthResult> {
  if (!input.email || !input.password) {
    return { ok: false, code: "invalid", message: "E-Mail und Passwort fehlen." }
  }
  await wait()
  return { ok: false, code: "not_configured", message: NOT_CONFIGURED }
}

export async function signUp(input: SignUpInput): Promise<AuthResult> {
  if (!input.age || !input.rules) {
    return { ok: false, code: "invalid", message: "Alter und Regeln müssen bestätigt sein." }
  }
  await wait()
  return { ok: false, code: "not_configured", message: NOT_CONFIGURED }
}

export async function requestPasswordReset(email: string): Promise<AuthResult> {
  if (!email) {
    return { ok: false, code: "invalid", message: "E-Mail fehlt." }
  }
  await wait()
  return { ok: false, code: "not_configured", message: NOT_CONFIGURED }
}

export async function signOut(): Promise<AuthResult> {
  await wait(150)
  return { ok: true }
}
