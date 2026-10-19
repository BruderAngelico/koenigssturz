/**
 * Central site configuration.
 * Rename the project here. Long-form German copy lives in content/.
 * Placeholder values — not a real organization.
 */

export const siteConfig = {
  name: "Rätegrün",
  claim: "Gemeinsam anbauen. Gemeinsam entscheiden.",
  slogan: "Keine Chefs. Keine Ware. Ein Rat.",
  description:
    "Rätegrün ist eine linke Cannabis-Gemeinschaft im Rätesystem: basisdemokratisch, solidarisch und ohne Profit. Platzhalter-Auftritt.",
  // Placeholder origin. Replace before a real deployment.
  url: "https://raetegruen.example",
  locale: "de_DE",
  lang: "de",
  // Not a monitored inbox.
  email: "kontakt@example.org",
  ageStorageKey: "raetegruen-age-ok",
  themeStorageKey: "raetegruen-theme",
  nav: [
    { href: "/", label: "Start" },
    { href: "/wer-wir-sind", label: "Wer wir sind" },
    { href: "/raetesystem", label: "Rätesystem" },
    { href: "/regeln", label: "Regeln" },
    { href: "/mitmachen", label: "Mitmachen" },
    { href: "/support", label: "Support" },
  ],
  communityNav: [
    { href: "/regeln", label: "Regeln" },
    { href: "/support", label: "Support" },
    { href: "/mitmachen", label: "Mitmachen" },
  ],
  legalNav: [
    { href: "/impressum", label: "Impressum" },
    { href: "/datenschutz", label: "Datenschutz" },
    { href: "/barrierefreiheit", label: "Barrierefreiheit" },
  ],
  // Privacy-friendly placeholders. example.org is reserved and not a real profile.
  social: [
    { label: "Mastodon", href: "https://example.org/mastodon" },
    { label: "Matrix", href: "https://example.org/matrix" },
  ],
} as const

export type NavItem = (typeof siteConfig.nav)[number]
