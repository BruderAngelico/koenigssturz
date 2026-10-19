export type AuthFailureCode = "not_configured" | "invalid"

export type AuthResult =
  | { ok: true }
  | { ok: false; code: AuthFailureCode; message: string }

export type SignInInput = {
  email: string
  password: string
}

export type SignUpInput = {
  name: string
  email: string
  password: string
  age: boolean
  rules: boolean
}
