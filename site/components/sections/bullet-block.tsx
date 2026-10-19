export function BulletBlock({ title, items }: { title: string; items: readonly string[] }) {
  return (
    <section className="grid gap-4 border-t-2 border-line pt-8 lg:grid-cols-12">
      <h2 className="font-display text-3xl uppercase leading-none lg:col-span-4">{title}</h2>
      <ul className="space-y-3 lg:col-span-8">
        {items.map((item) => (
          <li key={item} className="border-l-4 border-hemp pl-3 leading-relaxed">
            {item}
          </li>
        ))}
      </ul>
    </section>
  )
}
