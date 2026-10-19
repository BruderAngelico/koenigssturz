export function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  const external = href.startsWith("http")
  return (
    <a
      href={href}
      className="underline decoration-2 underline-offset-4"
      {...(external ? { rel: "noopener noreferrer", target: "_blank" } : {})}
    >
      {children}
      {external ? <span className="sr-only"> (extern)</span> : null}
    </a>
  )
}
